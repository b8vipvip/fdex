from __future__ import annotations

import hashlib
import os
import re
import shutil
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterator

from app.config import SERVER_DIR, fresh_settings

_OWNER = re.compile(r"^[A-Za-z0-9_.@-]{1,80}$")
_TASK = re.compile(r"^[0-9a-f]{32}$")
_SKILL = re.compile(r"^[A-Za-z0-9_.-]{1,80}$")
_MAX_ITEMS = 24
_MAX_IMAGE_BYTES = 20 * 1024 * 1024
_MAX_AUDIO_BYTES = 50 * 1024 * 1024
_ALLOWED_IMAGE = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}
_ALLOWED_AUDIO = {
    "audio/mpeg": ".mp3",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/mp4": ".m4a",
    "audio/m4a": ".m4a",
}


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


def _owner(value: str) -> str:
    clean = str(value or "").strip()
    if not _OWNER.fullmatch(clean) or clean in {".", ".."}:
        raise ValueError("invalid Codex input owner")
    return clean


def _task(value: str) -> str:
    clean = str(value or "").strip().lower()
    if not _TASK.fullmatch(clean):
        raise ValueError("invalid Codex input task id")
    return clean


def _owner_hash(owner_id: str) -> str:
    return hashlib.sha256(owner_id.encode("utf-8")).hexdigest()[:24]


def _clean_name(value: str, fallback: str) -> str:
    name = Path(str(value or "")).name.strip() or fallback
    return name[:180]


def _image_magic(data: bytes) -> bool:
    return (
        data.startswith(b"\x89PNG\r\n\x1a\n")
        or data.startswith(b"\xff\xd8\xff")
        or (len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP")
    )


def _audio_magic(data: bytes) -> bool:
    return (
        data.startswith(b"ID3")
        or (len(data) >= 2 and data[0] == 0xFF and (data[1] & 0xE0) == 0xE0)
        or (len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WAVE")
        or (len(data) >= 12 and data[4:8] == b"ftyp")
    )


def _safe_relative(value: str) -> str:
    text = str(value or "").strip().replace("\\", "/")
    if not text or text.startswith("/") or text.startswith("~"):
        raise ValueError("Mention path 必须是当前仓库内的相对路径")
    parts = [part for part in text.split("/") if part not in {"", "."}]
    if not parts or any(part == ".." for part in parts):
        raise ValueError("Mention path 不能逃出当前仓库")
    return "/".join(parts)[:2000]


class CodexTaskInputStore:
    """Durable owner/task-scoped metadata for official Codex ``UserInput[]``.

    Browser supplied paths are never persisted as trusted server paths. Binary media is copied
    into an FDEX-owned asset directory with generated filenames. Mention paths remain repository
    relative until the task worktree exists; skill names are resolved only below that owner's
    CODEX_HOME/skills directory when the Turn starts.
    """

    def __init__(self, path: Path | None = None, asset_root: Path | None = None) -> None:
        data = SERVER_DIR / "data"
        self.path = (path or data / "codex-inputs.db").resolve()
        self.asset_root = (asset_root or data / "codex-input-assets").resolve()
        self._initialized = False

    @contextmanager
    def db(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.path), timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def init(self) -> None:
        if self._initialized:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.asset_root.mkdir(parents=True, exist_ok=True)
        for item in (self.path.parent, self.asset_root):
            try:
                os.chmod(item, 0o700)
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
                    name TEXT NOT NULL DEFAULT '',
                    ref TEXT NOT NULL DEFAULT '',
                    path TEXT NOT NULL DEFAULT '',
                    mime TEXT NOT NULL DEFAULT '',
                    size_bytes INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_codex_task_inputs_owner_task
                    ON codex_task_inputs(owner_id,task_id,created_at,id);
                """
            )
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass
        self._initialized = True

    def _count(self, owner_id: str, task_id: str) -> int:
        self.init()
        with self.db() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM codex_task_inputs WHERE owner_id=? AND task_id=?",
                (_owner(owner_id), _task(task_id)),
            ).fetchone()
        return int(row[0]) if row else 0

    def _ensure_room(self, owner_id: str, task_id: str) -> None:
        if self._count(owner_id, task_id) >= _MAX_ITEMS:
            raise ValueError(f"单个 Coding Agent 任务最多 {_MAX_ITEMS} 个附加输入")

    def _asset_dir(self, owner_id: str, task_id: str) -> Path:
        owner_id = _owner(owner_id)
        task_id = _task(task_id)
        root = self.asset_root.resolve()
        target = (root / _owner_hash(owner_id) / task_id).resolve()
        if root not in target.parents:
            raise ValueError("Codex input asset path escaped root")
        target.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(target.parent, 0o700)
            os.chmod(target, 0o700)
        except OSError:
            pass
        return target

    def add_media(
        self,
        owner_id: str,
        task_id: str,
        *,
        kind: str,
        filename: str,
        mime: str,
        data: bytes,
    ) -> dict[str, Any]:
        owner_id = _owner(owner_id)
        task_id = _task(task_id)
        kind = str(kind or "").strip()
        if kind not in {"localImage", "localAudio"}:
            raise ValueError("unsupported Codex media input")
        self._ensure_room(owner_id, task_id)
        allowed = _ALLOWED_IMAGE if kind == "localImage" else _ALLOWED_AUDIO
        limit = _MAX_IMAGE_BYTES if kind == "localImage" else _MAX_AUDIO_BYTES
        clean_mime = str(mime or "").split(";", 1)[0].strip().lower()
        suffix = allowed.get(clean_mime)
        if suffix is None:
            raise ValueError("不支持的附件类型")
        if not data or len(data) > limit:
            raise ValueError(f"附件大小必须在 1 到 {limit // (1024 * 1024)} MiB 之间")
        if kind == "localImage" and not _image_magic(data[:32]):
            raise ValueError("图片文件内容与声明类型不匹配")
        if kind == "localAudio" and not _audio_magic(data[:32]):
            raise ValueError("音频文件内容与声明类型不匹配")
        item_id = f"inp_{uuid.uuid4().hex}"
        target = self._asset_dir(owner_id, task_id) / f"{item_id}{suffix}"
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            os.write(descriptor, data)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        row = {
            "id": item_id,
            "owner_id": owner_id,
            "task_id": task_id,
            "kind": kind,
            "name": _clean_name(filename, f"attachment{suffix}"),
            "ref": "",
            "path": str(target),
            "mime": clean_mime,
            "size_bytes": len(data),
            "created_at": _now(),
        }
        with self.db() as conn:
            conn.execute(
                """INSERT INTO codex_task_inputs(
                       id,owner_id,task_id,kind,name,ref,path,mime,size_bytes,created_at
                   ) VALUES(:id,:owner_id,:task_id,:kind,:name,:ref,:path,:mime,:size_bytes,:created_at)""",
                row,
            )
        return row

    def add_mention(self, owner_id: str, task_id: str, relative_path: str) -> dict[str, Any]:
        owner_id = _owner(owner_id)
        task_id = _task(task_id)
        self._ensure_room(owner_id, task_id)
        ref = _safe_relative(relative_path)
        return self._add_reference(owner_id, task_id, "mention", Path(ref).name or ref, ref)

    def add_skill(self, owner_id: str, task_id: str, name: str) -> dict[str, Any]:
        owner_id = _owner(owner_id)
        task_id = _task(task_id)
        self._ensure_room(owner_id, task_id)
        clean = str(name or "").strip()
        if not _SKILL.fullmatch(clean) or clean in {".", ".."}:
            raise ValueError("Skill 名称无效")
        return self._add_reference(owner_id, task_id, "skill", clean, clean)

    def _add_reference(self, owner_id: str, task_id: str, kind: str, name: str, ref: str) -> dict[str, Any]:
        row = {
            "id": f"inp_{uuid.uuid4().hex}",
            "owner_id": owner_id,
            "task_id": task_id,
            "kind": kind,
            "name": name[:180],
            "ref": ref[:2000],
            "path": "",
            "mime": "",
            "size_bytes": 0,
            "created_at": _now(),
        }
        with self.db() as conn:
            conn.execute(
                """INSERT INTO codex_task_inputs(
                       id,owner_id,task_id,kind,name,ref,path,mime,size_bytes,created_at
                   ) VALUES(:id,:owner_id,:task_id,:kind,:name,:ref,:path,:mime,:size_bytes,:created_at)""",
                row,
            )
        return row

    def list(self, owner_id: str, task_id: str) -> list[dict[str, Any]]:
        self.init()
        with self.db() as conn:
            rows = conn.execute(
                "SELECT * FROM codex_task_inputs WHERE owner_id=? AND task_id=? ORDER BY created_at,id",
                (_owner(owner_id), _task(task_id)),
            ).fetchall()
        return [dict(row) for row in rows]

    def delete(self, owner_id: str, task_id: str, item_id: str) -> bool:
        self.init()
        owner_id = _owner(owner_id)
        task_id = _task(task_id)
        with self.db() as conn:
            row = conn.execute(
                "SELECT * FROM codex_task_inputs WHERE owner_id=? AND task_id=? AND id=?",
                (owner_id, task_id, item_id),
            ).fetchone()
            if row is None:
                return False
            conn.execute(
                "DELETE FROM codex_task_inputs WHERE owner_id=? AND task_id=? AND id=?",
                (owner_id, task_id, item_id),
            )
        path = str(row["path"] or "")
        if path:
            self._unlink_asset(owner_id, task_id, Path(path))
        return True

    def _unlink_asset(self, owner_id: str, task_id: str, path: Path) -> None:
        root = self._asset_dir(owner_id, task_id).resolve()
        target = path.resolve()
        if target.parent != root:
            raise ValueError("Codex input asset escaped task directory")
        try:
            target.unlink()
        except FileNotFoundError:
            pass

    def clone_task(self, owner_id: str, source_task_id: str, target_task_id: str) -> int:
        count = 0
        for row in self.list(owner_id, source_task_id):
            kind = str(row["kind"])
            if kind in {"skill", "mention"}:
                self._add_reference(_owner(owner_id), _task(target_task_id), kind, str(row["name"]), str(row["ref"]))
                count += 1
                continue
            source = Path(str(row["path"])).resolve()
            if not source.is_file():
                continue
            self.add_media(
                owner_id,
                target_task_id,
                kind=kind,
                filename=str(row["name"]),
                mime=str(row["mime"]),
                data=source.read_bytes(),
            )
            count += 1
        return count

    def build_user_inputs(
        self,
        owner_id: str,
        task_id: str,
        *,
        prompt: str,
        worktree: Path,
        codex_home: Path,
    ) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = [{"type": "text", "text": str(prompt), "text_elements": []}]
        worktree = worktree.resolve()
        codex_home = codex_home.resolve()
        skill_root = (codex_home / "skills").resolve()
        for row in self.list(owner_id, task_id):
            kind = str(row["kind"])
            if kind in {"localImage", "localAudio"}:
                path = Path(str(row["path"])).resolve()
                task_asset_root = self._asset_dir(owner_id, task_id).resolve()
                if path.parent != task_asset_root or not path.is_file():
                    raise ValueError("Codex task attachment is missing or outside owner/task asset scope")
                result.append({"type": kind, "path": str(path)})
            elif kind == "mention":
                relative = _safe_relative(str(row["ref"]))
                path = (worktree / relative).resolve()
                if worktree not in path.parents or not path.exists():
                    raise ValueError(f"Mention 路径不存在或越界：{relative}")
                result.append({"type": "mention", "name": str(row["name"]), "path": str(path)})
            elif kind == "skill":
                name = str(row["ref"])
                if not _SKILL.fullmatch(name):
                    raise ValueError("Skill 名称无效")
                directory = (skill_root / name).resolve()
                if skill_root not in directory.parents:
                    raise ValueError("Skill 路径越界")
                candidate = directory / "SKILL.md"
                if not candidate.is_file():
                    raise ValueError(f"Skill 未安装：{name}")
                result.append({"type": "skill", "name": name, "path": str(candidate)})
        return result

    def delete_owner(self, owner_id: str) -> dict[str, int]:
        self.init()
        owner_id = _owner(owner_id)
        with self.db() as conn:
            count = int(
                conn.execute("SELECT COUNT(*) FROM codex_task_inputs WHERE owner_id=?", (owner_id,)).fetchone()[0]
            )
            conn.execute("DELETE FROM codex_task_inputs WHERE owner_id=?", (owner_id,))
        owner_dir = (self.asset_root / _owner_hash(owner_id)).resolve()
        root = self.asset_root.resolve()
        removed = 0
        if owner_dir != root and root in owner_dir.parents and owner_dir.exists():
            shutil.rmtree(owner_dir)
            removed = 1
        return {"records": count, "asset_directories": removed}


@lru_cache
def codex_task_input_store() -> CodexTaskInputStore:
    store = CodexTaskInputStore()
    store.init()
    return store
