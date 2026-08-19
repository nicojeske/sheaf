"""Storing the Paperless API token.

The token goes in the keyring (libsecret) and never into config.toml or the
repository. If no keyring backend is available the token falls back to a 0600
file, and :func:`storage_note` says so, so the UI can tell the user rather than
silently downgrading their security.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

from .config import config_dir

#: Kept from before the rename to Sheaf, for the same reason as
#: config.APP_DIR_NAME: the token is already stored in the keyring under it.
SERVICE = "scanner-paperless"
_FALLBACK_NAME = "token"


class TokenStore:
    def __init__(self, fallback_path: Path | None = None) -> None:
        self._fallback = fallback_path or (config_dir() / _FALLBACK_NAME)
        self._keyring_error: str | None = None

    # -- keyring ----------------------------------------------------------
    def _keyring(self):
        try:
            import keyring
        except ImportError as exc:  # pragma: no cover - dependency is declared
            self._keyring_error = str(exc)
            return None
        try:
            backend = keyring.get_keyring()
        except Exception as exc:  # noqa: BLE001 - any backend failure is a fallback
            self._keyring_error = str(exc)
            return None
        name = type(backend).__name__
        if "Fail" in name:
            self._keyring_error = "no usable keyring backend"
            return None
        return keyring

    @property
    def uses_keyring(self) -> bool:
        return self._keyring() is not None

    def storage_note(self) -> str:
        """A short sentence for the UI describing where the token lives."""
        if self.uses_keyring:
            return "Stored in your keyring."
        reason = self._keyring_error or "keyring unavailable"
        return f"Stored in {self._fallback} with 0600 permissions ({reason})."

    # -- api --------------------------------------------------------------
    def get(self, account: str) -> str | None:
        keyring = self._keyring()
        if keyring is not None:
            try:
                token = keyring.get_password(SERVICE, account)
            except Exception:  # noqa: BLE001
                token = None
            if token:
                return token
        return self._read_fallback()

    def set(self, account: str, token: str) -> None:
        keyring = self._keyring()
        if keyring is not None:
            try:
                keyring.set_password(SERVICE, account, token)
                return
            except Exception:  # noqa: BLE001
                pass
        self._write_fallback(token)

    def delete(self, account: str) -> None:
        keyring = self._keyring()
        if keyring is not None:
            try:
                keyring.delete_password(SERVICE, account)
            except Exception:  # noqa: BLE001
                pass
        self._fallback.unlink(missing_ok=True)

    # -- 0600 file fallback ----------------------------------------------
    def _read_fallback(self) -> str | None:
        try:
            token = self._fallback.read_text(encoding="utf-8").strip()
        except OSError:
            return None
        return token or None

    def _write_fallback(self, token: str) -> None:
        self._fallback.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(
            self._fallback, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, stat.S_IRUSR | stat.S_IWUSR
        )
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(token)
        os.chmod(self._fallback, stat.S_IRUSR | stat.S_IWUSR)


def redact(token: str | None, text: str) -> str:
    """Remove a token from text destined for a log or the UI."""
    if not token:
        return text
    return text.replace(token, "***")
