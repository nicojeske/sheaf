"""The background upload queue.

Driven entirely through ``UploadQueue.tick()`` — one deterministic unit of
work per call — against a fake uploader and a fake clock, so these tests
never start the real worker thread and never actually wait on wall-clock
time.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

import sheaf.upload.queue as queue_mod
from sheaf.pages import Page
from sheaf.upload.base import DocumentMeta, TaskOutcome, TaskState, UploadError
from sheaf.upload.queue import UploadQueue, UploadState


def _png(directory: Path, name: str) -> Path:
    path = directory / name
    Image.new("L", (40, 60), 200).save(path)
    return path


def _page(directory: Path, name: str, index: int = 1) -> Page:
    return Page(path=_png(directory, name), index=index, dpi=300)


class FakeClock:
    """A clock that only moves when the test tells it to."""

    def __init__(self, start: float = 0.0) -> None:
        self._now = start

    def __call__(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds


class FakeUploader:
    """Scripted responses, consumed one per call; the last repeats.

    ``upload_plan`` maps nothing — upload responses are queued globally, in
    call order, since the queue only ever sends one item at a time. ``polls``
    maps a task id to its own script, since several items poll concurrently.
    """

    def __init__(self) -> None:
        self.uploaded: list[Path] = []
        self._upload_plan: list[str | Exception] = []
        self._polls: dict[str, list[TaskState | Exception]] = {}

    def queue_upload(self, response: str | Exception) -> None:
        self._upload_plan.append(response)

    def queue_poll(self, task_id: str, *responses: TaskState | Exception) -> None:
        self._polls.setdefault(task_id, []).extend(responses)

    def upload(self, pdf: Path, meta: DocumentMeta) -> str:
        self.uploaded.append(pdf)
        response = self._upload_plan.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def poll(self, task_id: str) -> TaskState:
        script = self._polls.get(task_id) or []
        if len(script) > 1:
            response = script.pop(0)
        elif script:
            response = script[0]
        else:
            response = TaskState(TaskOutcome.PENDING)
        if isinstance(response, Exception):
            raise response
        return response

    # -- unused by the queue, present to satisfy the Uploader protocol ----
    def test_connection(self):
        raise NotImplementedError

    def list_tags(self, *, refresh: bool = False) -> list:
        return []

    def list_correspondents(self, *, refresh: bool = False) -> list:
        return []

    def list_document_types(self, *, refresh: bool = False) -> list:
        return []


def make_queue(tmp_path: Path, uploader: FakeUploader, clock: FakeClock, **kwargs):
    changes: list[tuple[int, UploadState]] = []

    def on_changed(item):
        changes.append((item.id, item.state))

    queue = UploadQueue(
        uploader,
        on_changed=on_changed,
        cache_dir=lambda: tmp_path / "cache",
        clock=clock,
        sleep=lambda seconds: clock.advance(seconds),
        **kwargs,
    )
    return queue, changes


def run_to_completion(queue: UploadQueue, clock: FakeClock, limit: int = 500) -> None:
    """Drive the queue with ``tick()`` until nothing is active.

    When there is nothing immediately due — every remaining item is
    CONSUMING but not yet due for a poll — jump the fake clock forward
    rather than waiting on real time, since nothing about backoff or
    timeouts is under test here.
    """
    for _ in range(limit):
        if queue.tick():
            continue
        if not any(item.active for item in queue.items()):
            return
        clock.advance(20.0)
    raise AssertionError("queue did not finish within the tick/advance budget")


def test_three_items_upload_and_consume_in_submission_order(tmp_path):
    uploader = FakeUploader()
    clock = FakeClock()
    queue, _changes = make_queue(tmp_path, uploader, clock)

    for i in (1, 2, 3):
        uploader.queue_upload(f"task{i}")
        uploader.queue_poll(f"task{i}", TaskState(TaskOutcome.SUCCESS, document_id=100 + i))
        queue.submit(name=f"Receipt {i}", pages=[_page(tmp_path, f"p{i}.png")])

    run_to_completion(queue, clock)

    items = queue.items()
    assert [item.state for item in items] == [UploadState.DONE] * 3
    assert [item.document_id for item in items] == [101, 102, 103]
    # Sent in the order they were queued.
    assert [p.name for p in uploader.uploaded] == [
        f"{queue_mod._safe_stem('Receipt 1')}-1-0.pdf",
        f"{queue_mod._safe_stem('Receipt 2')}-2-0.pdf",
        f"{queue_mod._safe_stem('Receipt 3')}-3-0.pdf",
    ]


def test_a_slow_poll_does_not_delay_the_next_upload(tmp_path):
    """The central claim of the design: item 1 sitting in CONSUMING must not
    stop item 2 from being sent."""
    uploader = FakeUploader()
    clock = FakeClock()
    queue, _changes = make_queue(tmp_path, uploader, clock)

    uploader.queue_upload("task1")
    item1 = queue.submit(name="Receipt 1", pages=[_page(tmp_path, "p1.png")])
    queue.tick()  # send item 1 -> CONSUMING, next poll not due for 2s

    assert queue.items()[0].state is UploadState.CONSUMING
    assert not queue.tick()  # nothing else to do yet: not queued, poll not due

    uploader.queue_upload("task2")
    item2 = queue.submit(name="Receipt 2", pages=[_page(tmp_path, "p2.png")])
    assert queue.tick()  # item 2 must jump ahead of item 1's not-yet-due poll

    by_id = {item.id: item for item in queue.items()}
    assert by_id[item2.id].state is UploadState.CONSUMING
    assert by_id[item1.id].state is UploadState.CONSUMING  # untouched, not failed or skipped

    uploader.queue_poll("task1", TaskState(TaskOutcome.SUCCESS, document_id=201))
    uploader.queue_poll("task2", TaskState(TaskOutcome.SUCCESS, document_id=202))
    clock.advance(10.0)
    run_to_completion(queue, clock)
    assert {item.document_id for item in queue.items()} == {201, 202}


def test_a_failed_item_does_not_stop_the_others(tmp_path):
    uploader = FakeUploader()
    clock = FakeClock()
    queue, _changes = make_queue(tmp_path, uploader, clock)

    uploader.queue_upload("task1")
    uploader.queue_poll("task1", TaskState(TaskOutcome.SUCCESS, document_id=1))
    uploader.queue_upload(UploadError("the server rejected it"))
    uploader.queue_upload("task3")
    uploader.queue_poll("task3", TaskState(TaskOutcome.SUCCESS, document_id=3))

    queue.submit(name="Receipt 1", pages=[_page(tmp_path, "p1.png")])
    queue.submit(name="Receipt 2", pages=[_page(tmp_path, "p2.png")])
    queue.submit(name="Receipt 3", pages=[_page(tmp_path, "p3.png")])

    clock.advance(10.0)
    run_to_completion(queue, clock)

    items = {item.name: item for item in queue.items()}
    assert items["Receipt 1"].state is UploadState.DONE
    assert items["Receipt 3"].state is UploadState.DONE
    assert items["Receipt 2"].state is UploadState.FAILED
    assert "the server rejected it" in items["Receipt 2"].error


def test_retry_reuses_the_existing_pdf_rather_than_re_exporting(tmp_path, monkeypatch):
    calls: list[Path] = []
    original = queue_mod.export_pdf

    def counting_export_pdf(pages, destination, dpi):
        calls.append(destination)
        return original(pages, destination, dpi)

    monkeypatch.setattr(queue_mod, "export_pdf", counting_export_pdf)

    uploader = FakeUploader()
    clock = FakeClock()
    queue, _changes = make_queue(tmp_path, uploader, clock)

    uploader.queue_upload(UploadError("first attempt failed"))
    item = queue.submit(name="Receipt", pages=[_page(tmp_path, "p1.png")])
    queue.tick()  # EXPORTING -> UPLOADING -> FAILED (export succeeded, upload did not)

    assert queue.items()[0].state is UploadState.FAILED
    assert len(calls) == 1
    pdf_after_failure = queue.items()[0].pdf
    assert pdf_after_failure is not None and pdf_after_failure.exists()

    uploader.queue_upload("task-retry")
    uploader.queue_poll("task-retry", TaskState(TaskOutcome.SUCCESS, document_id=9))
    queue.retry(item.id)
    run_to_completion(queue, clock)

    assert queue.items()[0].state is UploadState.DONE
    assert len(calls) == 1  # not re-exported
    assert uploader.uploaded == [pdf_after_failure, pdf_after_failure]


def test_a_stuck_poll_times_out_without_wedging_the_worker(tmp_path):
    uploader = FakeUploader()
    clock = FakeClock()
    queue, _changes = make_queue(tmp_path, uploader, clock, poll_timeout=5.0)

    uploader.queue_upload("stuck-task")
    # No queue_poll response at all -> FakeUploader.poll returns PENDING forever.
    queue.submit(name="Stuck receipt", pages=[_page(tmp_path, "p1.png")])
    queue.tick()  # send -> CONSUMING, deadline = 5.0

    clock.advance(6.0)
    assert queue.tick()  # the overdue poll
    assert queue.items()[0].state is UploadState.FAILED
    assert "still processing" in queue.items()[0].error

    # The worker keeps working afterwards — nothing wedges.
    uploader.queue_upload("task-ok")
    uploader.queue_poll("task-ok", TaskState(TaskOutcome.SUCCESS, document_id=42))
    queue.submit(name="Next receipt", pages=[_page(tmp_path, "p2.png")])
    clock.advance(10.0)
    run_to_completion(queue, clock)
    assert queue.items()[-1].state is UploadState.DONE


def test_on_changed_fires_for_every_transition(tmp_path):
    uploader = FakeUploader()
    clock = FakeClock()
    queue, changes = make_queue(tmp_path, uploader, clock)

    uploader.queue_upload("task1")
    uploader.queue_poll("task1", TaskState(TaskOutcome.SUCCESS, document_id=1))
    item = queue.submit(name="Receipt", pages=[_page(tmp_path, "p1.png")])
    run_to_completion(queue, clock)

    states = [state for item_id, state in changes if item_id == item.id]
    assert states == [
        UploadState.QUEUED,
        UploadState.EXPORTING,
        UploadState.UPLOADING,
        UploadState.CONSUMING,
        UploadState.DONE,
    ]


def test_needs_sources_is_true_only_before_the_pdf_exists(tmp_path):
    uploader = FakeUploader()
    clock = FakeClock()
    queue, _changes = make_queue(tmp_path, uploader, clock)

    uploader.queue_upload("task1")
    queue.submit(name="Receipt", pages=[_page(tmp_path, "p1.png")])
    assert queue.needs_sources()

    queue.tick()  # send -> CONSUMING, PDF now on disk
    assert not queue.needs_sources()


def test_counts_and_clear_finished(tmp_path):
    uploader = FakeUploader()
    clock = FakeClock()
    queue, _changes = make_queue(tmp_path, uploader, clock)

    uploader.queue_upload("task1")
    uploader.queue_poll("task1", TaskState(TaskOutcome.SUCCESS, document_id=1))
    uploader.queue_upload(UploadError("nope"))
    queue.submit(name="Good", pages=[_page(tmp_path, "good.png")])
    queue.submit(name="Bad", pages=[_page(tmp_path, "bad.png")])
    run_to_completion(queue, clock)

    counts = queue.counts()
    assert counts.done == 1
    assert counts.failed == 1
    assert counts.active == 0

    queue.clear_finished()
    assert queue.items() == []


def test_discard_removes_an_item_silently(tmp_path):
    uploader = FakeUploader()
    clock = FakeClock()
    queue, changes = make_queue(tmp_path, uploader, clock)

    uploader.queue_upload(UploadError("nope"))
    item = queue.submit(name="Bad", pages=[_page(tmp_path, "bad.png")])
    queue.tick()
    before = len(changes)

    queue.discard(item.id)
    assert queue.items() == []
    assert len(changes) == before  # no callback for a discard
