"""The uploader interface.

The scanning layer produces a PDF path plus metadata; an Uploader consumes it.
That is the whole contract, so a second destination can be added later without
touching any scan code.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum
from pathlib import Path
from typing import Protocol, runtime_checkable


class UploadError(RuntimeError):
    """Anything that went wrong talking to the document store."""


@dataclass(frozen=True, slots=True)
class MetadataItem:
    """A tag, correspondent or document type as offered by the server."""

    id: int
    name: str


@dataclass(frozen=True, slots=True)
class DocumentMeta:
    title: str | None = None
    created: date | None = None
    correspondent: int | None = None
    document_type: int | None = None
    storage_path: int | None = None
    tags: tuple[int, ...] = ()
    archive_serial_number: str | None = None


@dataclass(frozen=True, slots=True)
class ConnectionInfo:
    ok: bool
    detail: str
    version: str | None = None


class TaskOutcome(Enum):
    PENDING = "pending"
    SUCCESS = "success"
    FAILURE = "failure"


@dataclass(frozen=True, slots=True)
class TaskState:
    outcome: TaskOutcome
    #: Set once the document has been consumed.
    document_id: int | None = None
    message: str = ""
    raw_status: str = ""

    @property
    def finished(self) -> bool:
        return self.outcome is not TaskOutcome.PENDING


@runtime_checkable
class Uploader(Protocol):
    """What the UI needs from a document destination."""

    def test_connection(self) -> ConnectionInfo: ...

    def upload(self, pdf: Path, meta: DocumentMeta) -> str:
        """Send the document. Returns an opaque task id to poll."""

    def poll(self, task_id: str) -> TaskState: ...

    def list_tags(self, *, refresh: bool = False) -> list[MetadataItem]: ...

    def list_correspondents(self, *, refresh: bool = False) -> list[MetadataItem]: ...

    def list_document_types(self, *, refresh: bool = False) -> list[MetadataItem]: ...
