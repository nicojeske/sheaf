"""Paperless-ngx implementation of the Uploader protocol."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from .base import (
    ConnectionInfo,
    DocumentMeta,
    MetadataItem,
    TaskOutcome,
    TaskState,
    UploadError,
)

#: 10 is current, 9 is also supported. Pinning it means a server upgrade cannot
#: silently change response shapes under us.
API_VERSION = "10"
ACCEPT = f"application/json; version={API_VERSION}"

UPLOAD_PATH = "/api/documents/post_document/"
TASKS_PATH = "/api/tasks/"
TAGS_PATH = "/api/tags/"
CORRESPONDENTS_PATH = "/api/correspondents/"
DOCUMENT_TYPES_PATH = "/api/document_types/"

REQUEST_TIMEOUT = 60.0
UPLOAD_TIMEOUT = 300.0


def normalise_base_url(base_url: str) -> str:
    """Strip trailing slashes so path joining is predictable."""
    return base_url.strip().rstrip("/")


def build_upload_fields(meta: DocumentMeta) -> list[tuple[str, str]]:
    """The multipart form fields for a document, excluding the file itself.

    ``tags`` is repeated once per id — that is how the API takes multiple tags,
    so this must be a list of pairs rather than a dict.
    """
    fields: list[tuple[str, str]] = []
    if meta.title:
        fields.append(("title", meta.title))
    if meta.created is not None:
        fields.append(("created", meta.created.isoformat()))
    if meta.correspondent is not None:
        fields.append(("correspondent", str(meta.correspondent)))
    if meta.document_type is not None:
        fields.append(("document_type", str(meta.document_type)))
    if meta.storage_path is not None:
        fields.append(("storage_path", str(meta.storage_path)))
    if meta.archive_serial_number:
        fields.append(("archive_serial_number", meta.archive_serial_number))
    for tag in meta.tags:
        fields.append(("tags", str(tag)))
    return fields


class PaperlessUploader:
    """Talks to a paperless-ngx instance.

    All methods block; the UI calls them from a worker thread.
    """

    def __init__(self, base_url: str, token: str, *, session: Any | None = None) -> None:
        self.base_url = normalise_base_url(base_url)
        self._token = token
        self._session = session if session is not None else self._make_session()
        self._cache: dict[str, list[MetadataItem]] = {}

    def _make_session(self) -> Any:
        import requests

        session = requests.Session()
        session.headers.update(self.headers())
        return session

    def headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Token {self._token}",
            "Accept": ACCEPT,
        }

    def url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    # -- connection -------------------------------------------------------
    def test_connection(self) -> ConnectionInfo:
        if not self.base_url:
            return ConnectionInfo(False, "No Paperless URL is configured.")
        if not self._token:
            return ConnectionInfo(False, "No API token is set.")
        try:
            response = self._session.get(
                self.url(TAGS_PATH), params={"page_size": 1}, timeout=REQUEST_TIMEOUT
            )
        except Exception as exc:  # noqa: BLE001 - requests raises many types
            return ConnectionInfo(
                False, f"Could not reach {self.base_url} — {describe_exception(exc)}."
            )

        if response.status_code in (401, 403):
            return ConnectionInfo(False, "The server rejected the API token.")
        if response.status_code == 404:
            return ConnectionInfo(
                False, "The URL responded but has no Paperless API at /api/."
            )
        if response.status_code >= 400:
            return ConnectionInfo(
                False, f"The server returned HTTP {response.status_code}."
            )
        version = None
        for header in ("x-version", "X-Version"):
            if header in getattr(response, "headers", {}):
                version = response.headers[header]
                break
        detail = f"Connected to {self.base_url}"
        if version:
            detail += f" (paperless-ngx {version})"
        return ConnectionInfo(True, detail, version)

    # -- upload -----------------------------------------------------------
    def upload(self, pdf: Path, meta: DocumentMeta) -> str:
        """POST the PDF and return the consumption task id.

        The endpoint answers 200 with a task UUID, *not* a finished document —
        consumption happens asynchronously and can still fail (a rescanned
        receipt is rejected as a duplicate at that stage), so the caller must
        poll.
        """
        fields = build_upload_fields(meta)
        try:
            with pdf.open("rb") as fh:
                response = self._session.post(
                    self.url(UPLOAD_PATH),
                    files={"document": (pdf.name, fh, "application/pdf")},
                    data=fields,
                    timeout=UPLOAD_TIMEOUT,
                )
        except OSError as exc:
            raise UploadError(f"Could not read {pdf}: {exc}") from exc
        except Exception as exc:  # noqa: BLE001
            raise UploadError(f"Upload failed — {describe_exception(exc)}.") from exc

        if response.status_code >= 400:
            raise UploadError(_http_error(response))

        task_id = _parse_task_id(response)
        if not task_id:
            raise UploadError(
                "The server accepted the upload but did not return a task id."
            )
        return task_id

    def poll(self, task_id: str) -> TaskState:
        try:
            response = self._session.get(
                self.url(TASKS_PATH), params={"task_id": task_id}, timeout=REQUEST_TIMEOUT
            )
        except Exception as exc:  # noqa: BLE001
            raise UploadError(
                f"Could not check the upload status — {describe_exception(exc)}."
            ) from exc

        if response.status_code >= 400:
            raise UploadError(_http_error(response))

        payload = _json(response)
        entries = payload.get("results") if isinstance(payload, dict) else payload
        if not isinstance(entries, list) or not entries:
            # The task may not be registered yet; treat that as still pending.
            return TaskState(TaskOutcome.PENDING, raw_status="UNKNOWN")
        return parse_task(entries[0])

    # -- metadata ---------------------------------------------------------
    def list_tags(self, *, refresh: bool = False) -> list[MetadataItem]:
        return self._list(TAGS_PATH, refresh=refresh)

    def list_correspondents(self, *, refresh: bool = False) -> list[MetadataItem]:
        return self._list(CORRESPONDENTS_PATH, refresh=refresh)

    def list_document_types(self, *, refresh: bool = False) -> list[MetadataItem]:
        return self._list(DOCUMENT_TYPES_PATH, refresh=refresh)

    def _list(self, path: str, *, refresh: bool) -> list[MetadataItem]:
        if not refresh and path in self._cache:
            return self._cache[path]
        items = [
            MetadataItem(id=int(entry["id"]), name=str(entry.get("name", "")))
            for entry in self.paginate(path)
            if isinstance(entry, dict) and isinstance(entry.get("id"), int)
        ]
        items.sort(key=lambda item: item.name.casefold())
        self._cache[path] = items
        return items

    def paginate(self, path: str) -> Iterator[dict[str, Any]]:
        """Yield every result across all pages by following ``next``."""
        url: str | None = self.url(path)
        params: dict[str, Any] | None = {"page_size": 100}
        while url:
            try:
                response = self._session.get(url, params=params, timeout=REQUEST_TIMEOUT)
            except Exception as exc:  # noqa: BLE001
                raise UploadError(
                    f"Could not load {path} — {describe_exception(exc)}."
                ) from exc
            if response.status_code >= 400:
                raise UploadError(_http_error(response))
            payload = _json(response)
            if not isinstance(payload, dict):
                return
            yield from payload.get("results") or []
            url = payload.get("next")
            # `next` is absolute and already carries the query string.
            params = None


def parse_task(entry: dict[str, Any]) -> TaskState:
    """Interpret one /api/tasks/ entry.

    Paperless reports SUCCESS/FAILURE/PENDING/STARTED/RETRY in ``status`` and
    carries the resulting document id in ``related_document`` once consumed.
    """
    status = str(entry.get("status", "")).upper()
    message = str(entry.get("result") or entry.get("task_name") or "").strip()

    if status == "SUCCESS":
        return TaskState(
            TaskOutcome.SUCCESS,
            document_id=_opt_int(entry.get("related_document")),
            message=message,
            raw_status=status,
        )
    if status in ("FAILURE", "REVOKED"):
        return TaskState(
            TaskOutcome.FAILURE,
            message=message or "Paperless could not consume the document.",
            raw_status=status,
        )
    return TaskState(TaskOutcome.PENDING, message=message, raw_status=status or "PENDING")


def _parse_task_id(response: Any) -> str:
    """The upload response body is the task UUID as a bare JSON string."""
    payload = _json(response)
    if isinstance(payload, str):
        return payload.strip()
    if isinstance(payload, dict):
        for key in ("task_id", "taskId", "id"):
            value = payload.get(key)
            if isinstance(value, str) and value:
                return value
    text = getattr(response, "text", "") or ""
    return text.strip().strip('"')


def describe_exception(exc: BaseException) -> str:
    """A short, readable reason for a failed request.

    requests wraps urllib3, whose messages run to several lines of nested
    exception text — unusable in a dialog subtitle.
    """
    import requests.exceptions as re

    if isinstance(exc, re.SSLError):
        return "the server's TLS certificate could not be verified"
    if isinstance(exc, (re.ConnectTimeout, re.ReadTimeout, re.Timeout)):
        return "the server did not respond in time"
    if isinstance(exc, re.ConnectionError):
        return "the server could not be reached"
    if isinstance(exc, re.MissingSchema):
        return "the URL needs an http:// or https:// prefix"
    if isinstance(exc, re.InvalidURL):
        return "the URL is not valid"
    text = str(exc).strip()
    return text.splitlines()[0] if text else exc.__class__.__name__


def _json(response: Any) -> Any:
    try:
        return response.json()
    except (ValueError, json.JSONDecodeError):
        return None


def _http_error(response: Any) -> str:
    body = (getattr(response, "text", "") or "").strip()
    if len(body) > 300:
        body = body[:300] + "…"
    status = getattr(response, "status_code", "?")
    if status in (401, 403):
        return "The server rejected the API token."
    if body:
        return f"HTTP {status}: {body}"
    return f"HTTP {status}"


def _opt_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value
