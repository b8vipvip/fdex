from __future__ import annotations

import hashlib
import os
import secrets
import sqlite3
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path, PurePosixPath
from typing import Any

from app.config import SERVER_DIR

_MAX_IMAGE = 20 * 1024 * 1024
_MAX_AUDIO = 50 * 1024 * 1024
_MAX_ASSETS = 16
_ALLOWED_IMAGE = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
}
_ALLOWED_AUDIO = {
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/mpeg": ".mp3",
    "audio/mp4": ".m4a",
    "audio/ogg": ".ogg",
}


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


def _safe_id(value: str, prefix: str) -> str:
    clean = str(value or "").strip()
    if not clean.startswith(prefix) or len(clean) < 8 or any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for ch in clean):
        raise ValueError(f"invalid {prefix.rstrip('_')} id")
    return clean


def _safe_name(value: str) -> str:
    name = Path(str(value or "asset")).name.strip()[:180]
    return name or "asset"


def _safe_mention(value: str) -> str:
    clean = str(value or "").strip().replace("\\", "/")
    if not clean or len(clean) > 1000 or any(ord(ch) < 32 for ch in clean):
        raise ValueError("Mention 路径无效")
    path = PurePosixPath(clean)
    if path.is_absolute() or ".." in path.parts or path.parts[0] in {".git", "server/data"}:
        raise ValueError("Mention 只能引用当前任务仓库内的相对路径")
    return str(path)


class CodexTaskInputStore:
    def __init__(self, root: Path | None = None) -> None:
        self.root = (root or (SERVER_DIR / "data" / "codex-input-assets")).resolve()
        self.db_path = self.root / "index.sqlite3"
        self._initialized = False

    def init(self) -> None:
        if self._initialized:
            return
        self.root.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.root, 0o700)
        except OSError:
            pass
        with self.db() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS codex_task_inputs (
                    id TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    display_name TEXT NOT NULL,
                    stored_path TEXT NOT NULL DEFAULT '',
                    value TEXT NOT NULL DEFAULT '',
                    mime_type TEXT NOT NULL DEFAULT '',
                    size_bytes INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_codex_task_inputs_task
                    ON codex_task_inputs(owner_id,task_id,created_at,id);
                """
            )
        self._initialized = True

    def db(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        return conn

    def _task_dir(self, owner_id: str, task_id: str) -> Path:
        owner = _safe_id(owner_id, "usr_")
        task = _safe_id(task_id, "task_")
        owner_hash = hashlib.sha256(owner.encode()).hexdigest()[:24]
        target = (self.root / "owners" / owner_hash / task).resolve()
        base = (self.root / "owners").resolve()
        if base not in target.parents:
            raise ValueError("Codex input asset path escaped root")
        target.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(target, 0o700)
        except OSError:
            pass
        return target

    def list(self, owner_id: str, task_id: str) -> list[dict[str, Any]]:
        self.init()
        _safe_id(owner_id, "usr_")
        _safe_id(task_id, "task_")
        with self.db() as conn:
            rows = conn.execute(
                "SELECT * FROM codex_task_inputs WHERE owner_id=? AND task_id=? ORDER BY created_at,id",
                (owner_id, task_id),
            ).fetchall()
        return [dict(row) for row in rows]

    def _ensure_capacity(self, owner_id: str, task_id: str) -> None:
        if len(self.list(owner_id, task_id)) >= _MAX_ASSETS:
            raise ValueError(f"每个任务最多 {_MAX_ASSETS} 个富输入项")

    def add_binary(
        self,
        owner_id: str,
        task_id: str,
        *,
        kind: str,
        filename: str,
        content_type: str,
        data: bytes,
    ) -> dict[str, Any]:
        self.init()
        self._ensure_capacity(owner_id, task_id)
        clean_kind = str(kind or "").strip()
        mapping = _ALLOWED_IMAGE if clean_kind == "image" else _ALLOWED_AUDIO if clean_kind == "audio" else None
        if mapping is None:
            raise ValueError("只支持 image/audio 二进制富输入")
        mime = str(content_type or "").split(";", 1)[0].strip().lower()
        suffix = mapping.get(mime)
        if suffix is None:
            raise ValueError("该图片/音频 MIME 类型不受支持")
        limit = _MAX_IMAGE if clean_kind == "image" else _MAX_AUDIO
        if not data or len(data) > limit:
            raise ValueError(f"{clean_kind} 文件为空或超过 {limit // (1024 * 1024)} MiB")
        task_dir = self._task_dir(owner_id, task_id)
        asset_id = f"asset_{secrets.token_hex(16)}"
        path = (task_dir / f"{asset_id}{suffix}").resolve()
        if task_dir not in path.parents:
            raise ValueError("Codex input path escaped task directory")
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            os.write(descriptor, data)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        row = {
            "id": asset_id,
            "owner_id": owner_id,
            "task_id": task_id,
            "kind": clean_kind,
            "display_name": _safe_name(filename),
            "stored_path": str(path),
            "value": "",
            "mime_type": mime,
            "size_bytes": len(data),
            "created_at": _now(),
        }
        try:
            with self.db() as conn:
                conn.execute(
                    """
                    INSERT INTO codex_task_inputs(
                        id,owner_id,task_id,kind,display_name,stored_path,value,mime_type,size_bytes,created_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?)
                    """,
                    tuple(row[key] for key in (
                        "id","owner_id","task_id","kind","display_name","stored_path","value","mime_type","size_bytes","created_at"
                    )),
                )
        except Exception:
            path.unlink(missing_ok=True)
            raise
        return row

    def add_mention(self, owner_id: str, task_id: str, relative_path: str) -> dict[str, Any]:
        self.init()
        self._ensure_capacity(owner_id, task_id)
        value = _safe_mention(relative_path)
        row = {
            "id": f"mention_{secrets.token_hex(16)}",
            "owner_id": _safe_id(owner_id, "usr_"),
            "task_id": _safe_id(task_id, "task_"),
            "kind": "mention",
            "display_name": PurePosixPath(value).name,
            "stored_path": "",
            "value": value,
            "mime_type": "",
            "size_bytes": 0,
            "created_at": _now(),
        }
        with self.db() as conn:
            conn.execute(
                """
                INSERT INTO codex_task_inputs(
                    id,owner_id,task_id,kind,display_name,stored_path,value,mime_type,size_bytes,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?)
                """,
                tuple(row[key] for key in (
                    "id","owner_id","task_id","kind","display_name","stored_path","value","mime_type","size_bytes","created_at"
                )),
            )
        return row

    def add_skill(self, owner_id: str, task_id: str, *, name: str, path: str) -> dict[str, Any]:
        """Internal seam for Phase 7.30's owner-scoped skill registry; not a client path API."""
        self.init()
        self._ensure_capacity(owner_id, task_id)
        resolved = Path(path).expanduser().resolve()
        if not resolved.exists():
            raise ValueError("Skill path does not exist")
        row = {
            "id": f"skill_{secrets.token_hex(16)}",
            "owner_id": _safe_id(owner_id, "usr_"),
            "task_id": _safe_id(task_id, "task_"),
            "kind": "skill",
            "display_name": str(name or resolved.name)[:180],
            "stored_path": str(resolved),
            "value": "",
            "mime_type": "",
            "size_bytes": 0,
            "created_at": _now(),
        }
        with self.db() as conn:
            conn.execute(
                """
                INSERT INTO codex_task_inputs(
                    id,owner_id,task_id,kind,display_name,stored_path,value,mime_type,size_bytes,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?)
                """,
                tuple(row[key] for key in (
                    "id","owner_id","task_id","kind","display_name","stored_path","value","mime_type","size_bytes","created_at"
                )),
            )
        return row

    def remove(self, owner_id: str, task_id: str, input_id: str) -> bool:
        self.init()
        with self.db() as conn:
            row = conn.execute(
                "SELECT stored_path FROM codex_task_inputs WHERE owner_id=? AND task_id=? AND id=?",
                (owner_id, task_id, input_id),
            ).fetchone()
            if row is None:
                return False
            conn.execute(
                "DELETE FROM codex_task_inputs WHERE owner_id=? AND task_id=? AND id=?",
                (owner_id, task_id, input_id),
            )
        stored = str(row["stored_path"] or "")
        if stored:
            path = Path(stored).resolve()
            base = self.root.resolve()
            if base in path.parents:
                path.unlink(missing_ok=True)
        return True

    def build_user_input(self, owner_id: str, task_id: str, *, prompt: str, worktree: Path) -> list[dict[str, Any]]:
        worktree_root = worktree.resolve()
        result: list[dict[str, Any]] = [{"type": "text", "text": str(prompt), "text_elements": []}]
        for row in self.list(owner_id, task_id):
            kind = str(row["kind"])
            if kind in {"image", "audio"}:
                path = Path(str(row["stored_path"])).resolve()
                if self.root.resolve() not in path.parents or not path.is_file() or path.is_symlink():
                    raise ValueError("Codex input asset is missing or escaped owner storage")
                result.append({"type": "localImage" if kind == "image" else "localAudio", "path": str(path)})
            elif kind == "mention":
                relative = _safe_mention(str(row["value"]))
                path = (worktree_root / relative).resolve()
                if worktree_root not in path.parents and path != worktree_root:
                    raise ValueError("Mention escaped task worktree")
                if not path.exists():
                    raise ValueError(f"Mention path does not exist in task worktree: {relative}")
                result.append({"type": "mention", "name": str(row["display_name"]), "path": str(path)})
            elif kind == "skill":
                path = Path(str(row["stored_path"])).resolve()
                if not path.exists():
                    raise ValueError("Selected skill disappeared")
                result.append({"type": "skill", "name": str(row["display_name"]), "path": str(path)})
            else:
                raise ValueError(f"unsupported Codex task input kind: {kind}")
        return result

    def delete_owner(self, owner_id: str) -> dict[str, int]:
        self.init()
        rows = self.list(owner_id, "task_placeholder") if False else []
        with self.db() as conn:
            paths = [str(row[0]) for row in conn.execute(
                "SELECT stored_path FROM codex_task_inputs WHERE owner_id=? AND stored_path<>''",
                (owner_id,),
            ).fetchall()]
            cursor = conn.execute("DELETE FROM codex_task_inputs WHERE owner_id=?", (owner_id,))
        removed = 0
        base = self.root.resolve()
        for value in paths:
            path = Path(value).resolve()
            if base in path.parents and path.exists():
                path.unlink(missing_ok=True)
                removed += 1
        owner_hash = hashlib.sha256(_safe_id(owner_id, "usr_").encode()).hexdigest()[:24]
        owner_dir = (self.root / "owners" / owner_hash).resolve()
        if base in owner_dir.parents and owner_dir.exists():
            import shutil
            shutil.rmtree(owner_dir)
        return {"records": max(0, int(cursor.rowcount)), "files": removed}


@lru_cache
def codex_task_input_store() -> CodexTaskInputStore:
    store = CodexTaskInputStore()
    store.init()
    return store
