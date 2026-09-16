"""A background upload queue.

Sending a document to Paperless has two slow parts: the POST itself, and the
consumption task that runs afterwards and can take anywhere from a second to
several minutes (``upload/dialog.py`` used to poll it for up to 180 s with the
whole window modal). This module decouples the two: ``submit`` returns
immediately, and a single worker thread carries each item from export through
upload through consumption, at its own pace, while the caller keeps scanning.

The worker is a cooperative state machine rather than a per-item blocking
loop — that distinction is the entire point of the module. If item 1's slow
consumption poll blocked the worker, item 2 could not even be *uploaded*
until item 1 finished, and a batch of receipts would serialise into one long
wait exactly like the dialog it replaces. Instead, every call to ``tick()``
does at most one unit of work: send the next QUEUED item if there is one,
otherwise poll whichever CONSUMING item is due next. A pending poll never
blocks a fresh upload.

No ``gi`` import, by the convention the rest of this codebase's pure-logic
modules follow (see ``paperless.py``, ``status.py``): ``on_changed`` is a
plain callable invoked from the worker thread, and the GTK layer is the one
that wraps it in ``GLib.idle_add``.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path

from ..config import cache_dir as _default_cache_dir
from ..export import ExportError, document_dpi, export_pdf
from ..pages import Page, snapshot
from .base import DocumentMeta, TaskOutcome, UploadError, Uploader

#: How often the worker rechecks for work when it has none. Cheap — a lock
#: and a scan of a handful of items — so this can be short without wasting
#: CPU, and short is what keeps a test's fake clock advance visible quickly.
TICK = 0.2

#: Consumption is queued server-side, so polling has to be patient — but
#: bounded. Longer than the old modal dialog's 180 s (POLL_TIMEOUT in
#: upload/dialog.py) because nobody is sitting watching a spinner this time.
QUEUE_POLL_TIMEOUT = 600.0

#: Per-item backoff between consumption polls, in seconds.
POLL_BACKOFF = (2.0, 2.0, 3.0, 5.0, 8.0, 10.0, 15.0)


class UploadState(Enum):
    QUEUED = "queued"
    EXPORTING = "exporting"
    UPLOADING = "uploading"
    CONSUMING = "consuming"
    DONE = "done"
    FAILED = "failed"


_ACTIVE_STATES = frozenset(
    {
        UploadState.QUEUED,
        UploadState.EXPORTING,
        UploadState.UPLOADING,
        UploadState.CONSUMING,
    }
)


@dataclass(slots=True)
class UploadItem:
    id: int
    #: A short label for the document, shown in the queue popover — not sent
    #: to Paperless as a title.
    name: str
    #: Detached copies, safe to render on the worker thread. See
    #: pages.snapshot.
    pages: tuple[Page, ...]
    #: The live Page objects the caller submitted, kept only so the GTK layer
    #: can find its way back to the cards that produced this item. The worker
    #: never reads or writes these.
    origins: tuple[Page, ...]
    dpi: int
    meta: DocumentMeta
    state: UploadState = UploadState.QUEUED
    pdf: Path | None = None
    task_id: str | None = None
    document_id: int | None = None
    #: Human-readable reason, set only when state is FAILED.
    error: str = ""
    polls: int = 0
    next_poll_at: float = 0.0
    deadline: float = 0.0

    @property
    def needs_sources(self) -> bool:
        """Whether the scan tempdirs this item's pages live in are still
        needed. False once the PDF has been built — from then on the PDF in
        the cache directory is the only file this item depends on."""
        return self.state in (UploadState.QUEUED, UploadState.EXPORTING)

    @property
    def active(self) -> bool:
        return self.state in _ACTIVE_STATES


@dataclass(frozen=True, slots=True)
class QueueCounts:
    active: int
    failed: int
    done: int

    @property
    def total(self) -> int:
        return self.active + self.failed + self.done


class UploadQueue:
    """Owns one background worker thread and the items it is carrying.

    Construct one instance per uploader and keep it — it is not tied to any
    particular window or dialog, so it can outlive both.
    """

    def __init__(
        self,
        uploader: Uploader,
        *,
        on_changed: Callable[[UploadItem], None],
        cache_dir: Callable[[], Path] = _default_cache_dir,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        poll_timeout: float = QUEUE_POLL_TIMEOUT,
        tick: float = TICK,
    ) -> None:
        self._uploader = uploader
        self._on_changed = on_changed
        self._cache_dir = cache_dir
        self._clock = clock
        self._sleep = sleep
        self._poll_timeout = poll_timeout
        self._tick_interval = tick

        self._lock = threading.Lock()
        self._items: dict[int, UploadItem] = {}
        self._next_id = 1
        self._wake = threading.Event()
        self._stopping = threading.Event()
        self._thread: threading.Thread | None = None

    def set_uploader(self, uploader: Uploader) -> None:
        """Swap in a new uploader — e.g. after Paperless settings change.

        The queue outlives any one PaperlessUploader instance, so its own
        metadata cache can be rebuilt (a new base URL or token needs a fresh
        one) without losing whatever this queue is already carrying.
        """
        with self._lock:
            self._uploader = uploader

    def _get_uploader(self) -> Uploader:
        with self._lock:
            return self._uploader

    # -- lifecycle ----------------------------------------------------
    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stopping.clear()
        self._thread = threading.Thread(target=self._run, name="upload-queue", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stopping.set()
        self._wake.set()

    def drain(self, timeout: float) -> bool:
        """Block until nothing is active, or ``timeout`` elapses.

        Returns whether the queue was empty of active work when it returned.
        Used when the window is closing with uploads in flight: a bounded
        wait for anything already sent to reach a terminal state before the
        scan tempdirs are deleted.
        """
        deadline = self._clock() + timeout
        while self._clock() < deadline:
            if not any(item.active for item in self.items()):
                return True
            self._sleep(min(0.05, max(0.0, deadline - self._clock())))
        return not any(item.active for item in self.items())

    def _run(self) -> None:
        while not self._stopping.is_set():
            if self.tick():
                continue
            self._wake.wait(timeout=self._tick_interval)
            self._wake.clear()

    # -- submission -----------------------------------------------------
    def submit(
        self, *, name: str, pages: Sequence[Page], meta: DocumentMeta | None = None
    ) -> UploadItem:
        frozen = snapshot(list(pages))
        item = UploadItem(
            id=self._next_id,
            name=name,
            pages=frozen,
            origins=tuple(pages),
            dpi=document_dpi(frozen),
            meta=meta if meta is not None else DocumentMeta(),
        )
        with self._lock:
            self._next_id += 1
            self._items[item.id] = item
            copy = _copy(item)
        self._changed(copy)
        self._wake.set()
        return copy

    def retry(self, item_id: int) -> None:
        """Re-queue a failed item. Reuses its PDF if it is still on disk."""
        with self._lock:
            item = self._items.get(item_id)
            if item is None or item.state is not UploadState.FAILED:
                return
            item.state = UploadState.QUEUED
            item.error = ""
            copy = _copy(item)
        self._changed(copy)
        self._wake.set()

    def discard(self, item_id: int) -> None:
        """Drop an item without retrying it. The caller owns removing any
        row it built for it — no on_changed callback follows."""
        with self._lock:
            self._items.pop(item_id, None)

    def clear_finished(self) -> None:
        with self._lock:
            for item_id in [
                i
                for i, it in self._items.items()
                if it.state in (UploadState.DONE, UploadState.FAILED)
            ]:
                del self._items[item_id]

    # -- introspection ----------------------------------------------------
    def items(self) -> list[UploadItem]:
        """Detached copies, in submission order."""
        with self._lock:
            return [_copy(item) for item in self._items.values()]

    def counts(self) -> QueueCounts:
        active = failed = done = 0
        for item in self.items():
            if item.state is UploadState.DONE:
                done += 1
            elif item.state is UploadState.FAILED:
                failed += 1
            else:
                active += 1
        return QueueCounts(active=active, failed=failed, done=done)

    def needs_sources(self) -> bool:
        return any(item.needs_sources for item in self.items())

    # -- worker -------------------------------------------------------
    def tick(self) -> bool:
        """Do one unit of work. Returns whether it found any to do.

        Public (and side-effect-visible only through ``on_changed`` and the
        uploader) so tests can drive the state machine deterministically,
        with a fake uploader and clock, without starting the real thread.
        """
        action = self._next_action()
        if action is None:
            return False
        kind, item_id = action
        if kind == "send":
            self._send(item_id)
        else:
            self._poll(item_id)
        return True

    def _next_action(self) -> tuple[str, int] | None:
        with self._lock:
            for item in self._items.values():
                if item.state is UploadState.QUEUED:
                    return ("send", item.id)
            now = self._clock()
            due: UploadItem | None = None
            for item in self._items.values():
                if item.state is UploadState.CONSUMING and item.next_poll_at <= now:
                    if due is None or item.next_poll_at < due.next_poll_at:
                        due = item
            if due is not None:
                return ("poll", due.id)
        return None

    def _get(self, item_id: int) -> UploadItem | None:
        with self._lock:
            item = self._items.get(item_id)
            return _copy(item) if item is not None else None

    def _set(self, item_id: int, **changes: object) -> UploadItem | None:
        with self._lock:
            item = self._items.get(item_id)
            if item is None:
                return None
            for key, value in changes.items():
                setattr(item, key, value)
            copy = _copy(item)
        self._changed(copy)
        return copy

    def _changed(self, item: UploadItem) -> None:
        self._on_changed(item)

    def _send(self, item_id: int) -> None:
        item = self._get(item_id)
        if item is None:
            return
        pdf = item.pdf
        if pdf is None or not pdf.exists():
            item = self._set(item_id, state=UploadState.EXPORTING, error="")
            if item is None:
                return
            target = (
                self._cache_dir()
                / f"{_safe_stem(item.name)}-{item_id}-{int(self._clock())}.pdf"
            )
            try:
                export_pdf(item.pages, target, item.dpi)
            except ExportError as exc:
                self._set(
                    item_id, state=UploadState.FAILED, error=f"Could not build the PDF: {exc}"
                )
                return
            pdf = target
            # Bundled with the state change rather than a separate _set: two
            # calls here would fire on_changed twice for what is one logical
            # step (the export finishing), and a retry that skips exporting
            # entirely — pdf already on disk — must produce the same
            # callback shape as a fresh send.
            item = self._set(item_id, state=UploadState.UPLOADING, pdf=pdf)
        else:
            item = self._set(item_id, state=UploadState.UPLOADING)
        if item is None:
            return
        try:
            task_id = self._get_uploader().upload(pdf, item.meta)
        except UploadError as exc:
            self._set(
                item_id, state=UploadState.FAILED, error=f"{exc}\n\nThe PDF is kept at {pdf}"
            )
            return

        now = self._clock()
        self._set(
            item_id,
            state=UploadState.CONSUMING,
            task_id=task_id,
            polls=0,
            next_poll_at=now + POLL_BACKOFF[0],
            deadline=now + self._poll_timeout,
        )

    def _poll(self, item_id: int) -> None:
        item = self._get(item_id)
        if item is None or item.state is not UploadState.CONSUMING:
            return
        try:
            state = self._get_uploader().poll(item.task_id)
        except UploadError as exc:
            self._set(
                item_id,
                state=UploadState.FAILED,
                error=f"Could not check the upload: {exc}\n\nThe PDF is kept at {item.pdf}",
            )
            return

        if state.outcome is TaskOutcome.SUCCESS:
            self._set(item_id, state=UploadState.DONE, document_id=state.document_id, error="")
            return
        if state.outcome is TaskOutcome.FAILURE:
            self._set(
                item_id,
                state=UploadState.FAILED,
                error=(
                    f"Paperless rejected the document: {state.message}\n\n"
                    f"The PDF is kept at {item.pdf}"
                ),
            )
            return

        now = self._clock()
        if now > item.deadline:
            self._set(
                item_id,
                state=UploadState.FAILED,
                error=(
                    "Paperless is still processing. Check the server.\n\n"
                    f"The PDF is kept at {item.pdf}"
                ),
            )
            return
        polls = item.polls + 1
        delay = POLL_BACKOFF[min(polls, len(POLL_BACKOFF) - 1)]
        self._set(item_id, polls=polls, next_poll_at=now + delay)


def _copy(item: UploadItem) -> UploadItem:
    return replace(item)


def _safe_stem(name: str | None) -> str:
    if not name:
        return "document"
    keep = [c if c.isalnum() or c in "-_" else "-" for c in name]
    return "".join(keep).strip("-") or "document"
