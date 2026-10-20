"""Atomic raw-input recovery drafts, isolated from other open workbenches.

Each window owns one UUID-named draft and holds an OS file lock for its
session. Discovery offers only drafts whose locks can be acquired. Unlike
a PID check, this also works after a process crashes or its PID is reused.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import errno
import json
import logging
import os
from pathlib import Path
import sys
import tempfile
from typing import Any
import uuid
from version import APP_VERSION


LOGGER = logging.getLogger(__name__)
SCHEMA_VERSION = 1


def default_recovery_directory() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    else:
        base = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
    return base / "lre-workbench" / APP_VERSION / "recovery"


class _SessionLock:
    """Hold a byte lock on Windows and a flock on other desktop systems."""

    def __init__(self, path: Path):
        self.path = path
        self.handle = None

    def acquire(self) -> bool:
        handle = self.path.open("a+b")
        try:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            if sys.platform == "win32":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            handle.close()
            if error.errno in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                return False
            raise
        except BaseException:
            handle.close()
            raise
        self.handle = handle
        return True

    def close(self) -> None:
        handle, self.handle = self.handle, None
        if handle is None:
            return
        try:
            handle.seek(0)
            if sys.platform == "win32":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


@dataclass(frozen=True)
class RecoveryDraft:
    """An abandoned draft; read/discard recheck the session lock."""

    path: Path
    snapshot: dict[str, Any]
    updated_at: str
    session_id: str


class RecoveryStore:
    """Keep raw editor state without requiring valid EngineInputs.

    ``clear()`` removes only this store's draft. ``close()`` releases the
    session lock but leaves unsaved state recoverable. ``discard(draft)``
    removes a previous draft only while its lock is held by this method.
    Lock files stay in place: deleting a lock file can make two Unix
    processes lock different inodes for the same session name.
    """

    def __init__(self, state_directory: str | Path | None = None,
                 *, session_id: str | None = None):
        self.directory = Path(state_directory) if state_directory is not None else default_recovery_directory()
        self.directory = self.directory.resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.session_id = str(uuid.UUID(session_id)) if session_id is not None else str(uuid.uuid4())
        self.path = self.directory / f"{self.session_id}.json"
        self._started_at = datetime.now(timezone.utc).isoformat()
        self._lock = _SessionLock(self.directory / f"{self.session_id}.lock")
        self._closed = False
        if not self._lock.acquire():
            raise RuntimeError("Recovery session is already active")

    def _require_open(self) -> None:
        if self._closed:
            raise RuntimeError("Recovery store is closed")

    def save(self, snapshot: dict[str, Any]) -> None:
        """Replace this window's draft atomically; surface write failures."""
        self._require_open()
        if not isinstance(snapshot, dict):
            raise TypeError("Recovery snapshot must be a dict")
        envelope = {
            "schema_version": SCHEMA_VERSION,
            "session": {"id": self.session_id, "pid": os.getpid(), "started_at": self._started_at},
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "snapshot": snapshot,
        }
        # Serialize first: unsupported data must not overwrite the last draft.
        serialized = json.dumps(envelope, ensure_ascii=False, allow_nan=False, indent=2)
        temporary_path = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n",
                                             prefix=f".{self.session_id}.", suffix=".tmp",
                                             dir=self.directory, delete=False) as temporary:
                temporary_path = Path(temporary.name)
                temporary.write(serialized)
                temporary.flush()
                os.fsync(temporary.fileno())
            os.replace(temporary_path, self.path)
            temporary_path = None
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

    def _read_unlocked(self, path: Path) -> RecoveryDraft | None:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or data.get("schema_version") != SCHEMA_VERSION:
                raise ValueError("Unknown recovery draft format")
            session = data.get("session")
            if not isinstance(session, dict) or session.get("id") != path.stem:
                raise ValueError("Recovery session does not match its filename")
            snapshot = data.get("snapshot")
            timestamp = data.get("updated_at")
            if not isinstance(snapshot, dict) or not isinstance(timestamp, str):
                raise ValueError("Recovery draft is missing its snapshot or timestamp")
            # A validated timestamp keeps sorting and UI labels predictable.
            if datetime.fromisoformat(timestamp).tzinfo is None:
                raise ValueError("Recovery timestamp has no timezone")
            return RecoveryDraft(path, snapshot, timestamp, path.stem)
        except FileNotFoundError:
            return None
        except (OSError, ValueError, TypeError):
            LOGGER.warning("Could not read recovery draft; retained for diagnosis: %s", path,
                           exc_info=True)
            return None

    def _previous_path(self, draft: RecoveryDraft | str | Path) -> Path:
        path = Path(draft.path if isinstance(draft, RecoveryDraft) else draft).resolve()
        if path.parent != self.directory or path.suffix != ".json":
            raise ValueError("Recovery draft must belong to this recovery directory")
        try:
            identifier = str(uuid.UUID(path.stem))
        except ValueError as error:
            raise ValueError("Invalid recovery session filename") from error
        if identifier != path.stem:
            raise ValueError("Invalid recovery session filename")
        return path

    def read(self, draft: RecoveryDraft | str | Path | None = None) -> dict[str, Any] | None:
        """Read own draft or an abandoned one; never read another live session."""
        self._require_open()
        path = self.path if draft is None else self._previous_path(draft)
        if path == self.path:
            record = self._read_unlocked(path)
            return record.snapshot if record is not None else None
        lock = _SessionLock(path.with_suffix(".lock"))
        if not lock.acquire():
            return None
        try:
            record = self._read_unlocked(path)
            return record.snapshot if record is not None else None
        finally:
            lock.close()

    def discover(self) -> list[RecoveryDraft]:
        """List intact, abandoned sessions, most recently updated first."""
        self._require_open()
        drafts = []
        for path in self.directory.glob("*.json"):
            if path == self.path:
                continue
            try:
                path = self._previous_path(path)
                lock = _SessionLock(path.with_suffix(".lock"))
                if not lock.acquire():
                    continue
                try:
                    record = self._read_unlocked(path)
                    if record is not None:
                        drafts.append(record)
                finally:
                    lock.close()
            except ValueError:
                # This folder may contain unrelated files; do not alter them.
                continue
            except OSError:
                LOGGER.warning("Could not inspect recovery session: %s", path, exc_info=True)
        drafts.sort(key=lambda draft: datetime.fromisoformat(draft.updated_at), reverse=True)
        return drafts

    def clear(self) -> None:
        """Remove only this window's draft, retaining its live session lock."""
        self._require_open()
        self.path.unlink(missing_ok=True)

    def discard(self, draft: RecoveryDraft | str | Path) -> bool:
        """Discard an abandoned draft, returning False if it is now active."""
        self._require_open()
        path = self._previous_path(draft)
        if path == self.path:
            self.clear()
            return True
        lock = _SessionLock(path.with_suffix(".lock"))
        if not lock.acquire():
            return False
        try:
            path.unlink(missing_ok=True)
            return True
        finally:
            lock.close()

    def close(self) -> None:
        if not self._closed:
            self._lock.close()
            self._closed = True

    def __enter__(self) -> RecoveryStore:
        self._require_open()
        return self

    def __exit__(self, kind, value, traceback) -> None:
        self.close()
