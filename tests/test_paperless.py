"""The Paperless request builder, exercised against a fake session."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from sheaf.upload.base import DocumentMeta, TaskOutcome, UploadError
from sheaf.upload.paperless import (
    ACCEPT,
    PaperlessUploader,
    build_upload_fields,
    normalise_base_url,
    parse_task,
)


class FakeResponse:
    def __init__(self, payload=None, status_code=200, text=None, headers=None):
        self._payload = payload
        self.status_code = status_code
        self.text = text if text is not None else json.dumps(payload)
        self.headers = headers or {}

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class FakeSession:
    """Records calls and replays queued responses."""

    def __init__(self, responses=None):
        self.headers: dict[str, str] = {}
        self.calls: list[dict] = []
        self._responses = list(responses or [])

    def _next(self):
        if not self._responses:
            return FakeResponse({"results": [], "next": None})
        return self._responses.pop(0)

    def get(self, url, **kwargs):
        self.calls.append({"method": "GET", "url": url, **kwargs})
        return self._next()

    def post(self, url, **kwargs):
        self.calls.append({"method": "POST", "url": url, **kwargs})
        return self._next()


def make(responses=None, base_url="https://paperless.example.com/"):
    session = FakeSession(responses)
    uploader = PaperlessUploader(base_url, "secret-token", session=session)
    return uploader, session


# -- url and headers ---------------------------------------------------------
@pytest.mark.parametrize(
    "raw", ["https://p.example.com", "https://p.example.com/", "  https://p.example.com//  "]
)
def test_base_url_is_normalised(raw):
    assert normalise_base_url(raw) == "https://p.example.com"


def test_headers_pin_the_api_version_and_carry_the_token():
    uploader, _ = make()
    headers = uploader.headers()
    assert headers["Authorization"] == "Token secret-token"
    assert headers["Accept"] == ACCEPT
    assert headers["Accept"] == "application/json; version=10"


# -- upload fields -----------------------------------------------------------
def test_tags_are_repeated_rather_than_joined():
    fields = build_upload_fields(DocumentMeta(tags=(1, 2, 3)))
    assert fields == [("tags", "1"), ("tags", "2"), ("tags", "3")]


def test_all_metadata_fields_are_mapped():
    fields = dict(
        build_upload_fields(
            DocumentMeta(
                title="Receipt 2026-08-19",
                created=date(2026, 8, 19),
                correspondent=4,
                document_type=2,
                storage_path=6,
                archive_serial_number="A-17",
            )
        )
    )
    assert fields == {
        "title": "Receipt 2026-08-19",
        "created": "2026-08-19",
        "correspondent": "4",
        "document_type": "2",
        "storage_path": "6",
        "archive_serial_number": "A-17",
    }


def test_unset_metadata_is_omitted():
    assert build_upload_fields(DocumentMeta()) == []


def test_upload_posts_multipart_with_the_document_field(tmp_path: Path):
    pdf = tmp_path / "receipt.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    uploader, session = make([FakeResponse("11111111-2222-3333-4444-555555555555")])

    task_id = uploader.upload(pdf, DocumentMeta(title="Receipt", tags=(5, 6)))

    assert task_id == "11111111-2222-3333-4444-555555555555"
    call = session.calls[0]
    assert call["method"] == "POST"
    assert call["url"] == (
        "https://paperless.example.com/api/documents/post_document/"
    )
    assert "document" in call["files"]
    assert call["files"]["document"][0] == "receipt.pdf"
    assert call["files"]["document"][2] == "application/pdf"
    assert ("tags", "5") in call["data"] and ("tags", "6") in call["data"]


def test_upload_accepts_a_bare_quoted_uuid_body(tmp_path: Path):
    pdf = tmp_path / "r.pdf"
    pdf.write_bytes(b"x")
    uploader, _ = make([FakeResponse(None, text='"abc-123"\n')])
    assert uploader.upload(pdf, DocumentMeta()) == "abc-123"


def test_upload_rejection_becomes_an_upload_error(tmp_path: Path):
    pdf = tmp_path / "r.pdf"
    pdf.write_bytes(b"x")
    uploader, _ = make([FakeResponse(None, status_code=403, text="forbidden")])
    with pytest.raises(UploadError, match="token"):
        uploader.upload(pdf, DocumentMeta())


# -- task polling ------------------------------------------------------------
def test_pending_task_is_not_finished():
    state = parse_task({"status": "STARTED"})
    assert state.outcome is TaskOutcome.PENDING
    assert not state.finished


def test_successful_task_carries_the_document_id():
    state = parse_task({"status": "SUCCESS", "related_document": 123})
    assert state.outcome is TaskOutcome.SUCCESS
    assert state.document_id == 123
    assert state.finished


def test_duplicate_rejection_surfaces_the_server_message():
    # The likely outcome when rescanning a receipt, so the reason must survive.
    state = parse_task(
        {
            "status": "FAILURE",
            "result": "not consuming receipt.pdf: It is a duplicate of Receipt (#42)",
        }
    )
    assert state.outcome is TaskOutcome.FAILURE
    assert "duplicate" in state.message


def test_poll_queries_by_task_id():
    uploader, session = make(
        [FakeResponse({"results": [{"status": "SUCCESS", "related_document": 7}]})]
    )
    state = uploader.poll("task-uuid")
    assert state.document_id == 7
    assert session.calls[0]["params"] == {"task_id": "task-uuid"}


def test_poll_treats_an_unregistered_task_as_pending():
    uploader, _ = make([FakeResponse({"results": []})])
    assert uploader.poll("task-uuid").outcome is TaskOutcome.PENDING


# -- pagination --------------------------------------------------------------
def test_metadata_lists_follow_next_across_pages():
    page1 = FakeResponse(
        {
            "results": [{"id": 2, "name": "Groceries"}],
            "next": "https://paperless.example.com/api/tags/?page=2",
        }
    )
    page2 = FakeResponse({"results": [{"id": 1, "name": "Business"}], "next": None})
    uploader, session = make([page1, page2])

    tags = uploader.list_tags()

    assert [t.id for t in tags] == [1, 2]  # sorted by name
    assert [t.name for t in tags] == ["Business", "Groceries"]
    assert len(session.calls) == 2
    assert session.calls[1]["url"] == "https://paperless.example.com/api/tags/?page=2"
    # the absolute `next` URL already carries its query string
    assert session.calls[1]["params"] is None


def test_metadata_lists_are_cached_until_refreshed():
    uploader, session = make(
        [
            FakeResponse({"results": [{"id": 1, "name": "A"}], "next": None}),
            FakeResponse({"results": [{"id": 1, "name": "A"}], "next": None}),
        ]
    )
    uploader.list_tags()
    uploader.list_tags()
    assert len(session.calls) == 1
    uploader.list_tags(refresh=True)
    assert len(session.calls) == 2


# -- connection test ---------------------------------------------------------
def test_test_connection_reports_success_with_version():
    uploader, _ = make([FakeResponse({"results": []}, headers={"x-version": "2.14.7"})])
    info = uploader.test_connection()
    assert info.ok
    assert "2.14.7" in info.detail


def test_test_connection_reports_a_bad_token():
    uploader, _ = make([FakeResponse(None, status_code=401, text="")])
    info = uploader.test_connection()
    assert not info.ok
    assert "token" in info.detail


def test_test_connection_requires_configuration():
    uploader = PaperlessUploader("", "", session=FakeSession())
    assert not uploader.test_connection().ok


# -- error messages ----------------------------------------------------------
def test_network_failures_are_described_in_one_short_line():
    import requests.exceptions as re

    from sheaf.upload.paperless import describe_exception

    # urllib3's own message runs to several lines of nested exception text.
    assert describe_exception(re.ConnectionError("...long urllib3 text...")) == (
        "the server could not be reached"
    )
    assert "time" in describe_exception(re.ReadTimeout())
    assert "http://" in describe_exception(re.MissingSchema())
    assert "\n" not in describe_exception(ValueError("first line\nsecond line"))


def test_unreachable_server_reports_a_readable_reason():
    import requests.exceptions as re

    class Failing(FakeSession):
        def get(self, url, **kwargs):
            raise re.ConnectionError("HTTPSConnectionPool(...): Max retries exceeded")

    uploader = PaperlessUploader("https://p.invalid", "t", session=Failing())
    info = uploader.test_connection()
    assert not info.ok
    assert info.detail == "Could not reach https://p.invalid — the server could not be reached."
