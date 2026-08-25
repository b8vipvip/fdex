from __future__ import annotations

import fcntl
import hashlib
import json
import os
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterator

from app.config import fresh_settings


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def account_hash(user_id: str) -> str:
    clean = (user_id or "").strip()
    if not clean.startswith("usr_") or len(clean) < 12:
        raise ValueError("invalid FDEX user id")
    return hashlib.sha256(clean.encode("utf-8")).hexdigest()


def _lock_dir() -> Path:
    root = Path(fresh_settings().app_dir).expanduser().resolve() / "server" / "data" / "account-operations"
    root.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(root, 0o700)
    except OSError:
        pass
    return root


def _validate_owner_hash(owner_hash: str) -> str:
    clean = (owner_hash or "").strip().lower()
    if len(clean) != 64 or any(ch not in "0123456789abcdef" for ch in clean):
        raise ValueError("invalid FDEX account hash")
    return clean


def _lock_path_by_hash(owner_hash: str) -> Path:
    return _lock_dir() / f"{_validate_owner_hash(owner_hash)}.lock"


def _deleted_path_by_hash(owner_hash: str) -> Path:
    return _lock_dir() / f"{_validate_owner_hash(owner_hash)}.deleted"


def _memory_generation_path_by_hash(owner_hash: str) -> Path:
    return _lock_dir() / f"{_validate_owner_hash(owner_hash)}.memory-generation"


@dataclass(frozen=True, slots=True)
class AccountOperationStatus:
    busy: bool
    operation: str = ""
    started_at: str = ""
    owner_hash: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "busy": self.busy,
            "operation": self.operation,
            "started_at": self.started_at,
            "account_hash": self.owner_hash,
        }


class AccountOperationBusy(RuntimeError):
    def __init__(self, status: AccountOperationStatus) -> None:
        self.status = status
        label = status.operation or "account_data_operation"
        super().__init__(f"FDEX account operation already in progress: {label}")


def _read_metadata(handle, owner_hash: str) -> AccountOperationStatus:
    try:
        handle.seek(0)
        payload = json.loads(handle.read() or "{}")
    except Exception:
        payload = {}
    return AccountOperationStatus(
        busy=True,
        operation=str(payload.get("operation") or "account_data_operation")[:80],
        started_at=str(payload.get("started_at") or "")[:64],
        owner_hash=owner_hash,
    )


def account_operation_status_by_hash(owner_hash: str) -> AccountOperationStatus:
    clean_hash = _validate_owner_hash(owner_hash)
    path = _lock_path_by_hash(clean_hash)
    handle = path.open("a+", encoding="utf-8")
    try:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return _read_metadata(handle, clean_hash)
        else:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            return AccountOperationStatus(busy=False, owner_hash=clean_hash)
    finally:
        handle.close()


def account_operation_status(user_id: str) -> AccountOperationStatus:
    return account_operation_status_by_hash(account_hash(user_id))


def memory_generation_by_hash(owner_hash: str) -> int:
    path = _memory_generation_path_by_hash(owner_hash)
    if not path.exists():
        return 0
    try:
        return max(0, int(path.read_text(encoding="utf-8").strip() or "0"))
    except (OSError, ValueError):
        return 0


def memory_generation(user_id: str) -> int:
    return memory_generation_by_hash(account_hash(user_id))


def advance_memory_generation(user_id: str) -> int:
    """Advance the write fence while the caller owns the per-account operation lock.

    Requests/realtime sessions snapshot the generation when they start. A successful clear
    advances it before releasing the lock, so any response that began before the clear can
    no longer repopulate the just-erased remote memory after the lock is gone. New requests
    observe the new generation and may form fresh memory normally.
    """
    owner_hash = account_hash(user_id)
    path = _memory_generation_path_by_hash(owner_hash)
    next_value = memory_generation_by_hash(owner_hash) + 1
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(f"{next_value}\n", encoding="utf-8")
    try:
        os.chmod(temporary, 0o600)
    except OSError:
        pass
    temporary.replace(path)
    return next_value


def mark_account_deleted(user_id: str) -> str:
    """Persist a one-way deletion tombstone while the delete lock is still held.

    Background HTTP/realtime responses can outlive the request that deleted the account.
    The tombstone remains after the flock is released so those stale responses cannot create
    a fresh MemPalace/Letta namespace for an identity that no longer exists. It contains no
    email, token, content or raw user id.
    """
    owner_hash = account_hash(user_id)
    path = _deleted_path_by_hash(owner_hash)
    temporary = path.with_name(path.name + ".tmp")
    payload = {"account_hash": owner_hash, "deleted_at": _now()}
    temporary.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    try:
        os.chmod(temporary, 0o600)
    except OSError:
        pass
    temporary.replace(path)
    return owner_hash


def account_deleted_by_hash(owner_hash: str) -> bool:
    return _deleted_path_by_hash(owner_hash).exists()


@contextmanager
def account_operation(user_id: str, operation: str) -> Iterator[AccountOperationStatus]:
    owner_hash = account_hash(user_id)
    path = _lock_path_by_hash(owner_hash)
    handle = path.open("a+", encoding="utf-8")
    acquired = False
    try:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except BlockingIOError as exc:
            # Never truncate/unlock the metadata written by the process that owns the lock.
            raise AccountOperationBusy(_read_metadata(handle, owner_hash)) from exc

        started = _now()
        status = AccountOperationStatus(
            busy=True,
            operation=(operation or "account_data_operation").strip()[:80],
            started_at=started,
            owner_hash=owner_hash,
        )
        handle.seek(0)
        handle.truncate()
        handle.write(json.dumps(status.to_dict(), ensure_ascii=False, separators=(",", ":")))
        handle.flush()
        os.fsync(handle.fileno())
        yield status
    finally:
        if acquired:
            try:
                handle.seek(0)
                handle.truncate()
                handle.flush()
            except Exception:
                pass
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except Exception:
                pass
        handle.close()
