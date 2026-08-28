from __future__ import annotations

import json
import os
import tempfile
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from app.config import SERVER_DIR
from app.github_egress import parse_vless_uri, vless_summary

_POOL_FILE = SERVER_DIR / "data" / "github-egress" / "vless-nodes.json"
_LOCK = threading.RLock()
_VERSION = 1


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean_name(name: str, uri: str) -> str:
    clean = " ".join((name or "").strip().split())
    if clean:
        return clean[:80]
    try:
        fragment = unquote(urlsplit(uri).fragment or "").strip()
    except ValueError:
        fragment = ""
    if fragment:
        return " ".join(fragment.split())[:80]
    return vless_summary(uri)[:80]


def _read_nodes_unlocked() -> list[dict[str, Any]]:
    if not _POOL_FILE.exists():
        return []
    try:
        payload = json.loads(_POOL_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    raw_nodes = payload.get("nodes") if isinstance(payload, dict) else []
    if not isinstance(raw_nodes, list):
        return []
    nodes: list[dict[str, Any]] = []
    active_seen = False
    for item in raw_nodes:
        if not isinstance(item, dict):
            continue
        node_id = str(item.get("id") or "").strip()
        uri = str(item.get("uri") or "").strip()
        if not node_id or not uri:
            continue
        enabled = bool(item.get("enabled")) and not active_seen
        if enabled:
            active_seen = True
        nodes.append(
            {
                "id": node_id,
                "name": _clean_name(str(item.get("name") or ""), uri),
                "uri": uri,
                "enabled": enabled,
                "created_at": str(item.get("created_at") or ""),
                "updated_at": str(item.get("updated_at") or ""),
            }
        )
    return nodes


def _write_nodes_unlocked(nodes: list[dict[str, Any]]) -> None:
    _POOL_FILE.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(_POOL_FILE.parent, 0o700)
    except OSError:
        pass
    payload = {"version": _VERSION, "nodes": nodes}
    content = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    fd, temp_name = tempfile.mkstemp(prefix=".vless-nodes.", dir=str(_POOL_FILE.parent), text=True)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_path, 0o600)
        os.replace(temp_path, _POOL_FILE)
    finally:
        temp_path.unlink(missing_ok=True)


def _public(node: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(node["id"]),
        "name": str(node["name"]),
        "summary": vless_summary(str(node["uri"])),
        "enabled": bool(node.get("enabled")),
        "created_at": str(node.get("created_at") or ""),
        "updated_at": str(node.get("updated_at") or ""),
    }


def ensure_legacy_vless_node(values: dict[str, str]) -> None:
    """One-time migrate the Phase 7.16 single VLESS secret into the pool.

    The URI remains server-side only. Existing deployments keep their active node after upgrade,
    while a deliberately empty pool file is respected and will not be repopulated on every GET.
    """
    with _LOCK:
        if _POOL_FILE.exists():
            return
        uri = (values.get("FDEX_GITHUB_VLESS_URI") or "").strip()
        if not uri:
            return
        try:
            parse_vless_uri(uri)
        except ValueError:
            return
        stamp = _now()
        enabled = (values.get("FDEX_GITHUB_EGRESS_MODE") or "").strip().lower() == "managed_vless"
        node = {
            "id": str(uuid.uuid4()),
            "name": _clean_name("旧版 VLESS 节点", uri),
            "uri": uri,
            "enabled": enabled,
            "created_at": stamp,
            "updated_at": stamp,
        }
        _write_nodes_unlocked([node])


def list_vless_nodes() -> list[dict[str, Any]]:
    with _LOCK:
        return [_public(node) for node in _read_nodes_unlocked()]


def get_vless_node(node_id: str) -> dict[str, Any] | None:
    clean_id = (node_id or "").strip()
    with _LOCK:
        for node in _read_nodes_unlocked():
            if node["id"] == clean_id:
                return dict(node)
    return None


def active_vless_node() -> dict[str, Any] | None:
    with _LOCK:
        for node in _read_nodes_unlocked():
            if node.get("enabled"):
                return dict(node)
    return None


def add_vless_node(name: str, uri: str) -> dict[str, Any]:
    clean_uri = (uri or "").strip()
    parse_vless_uri(clean_uri)
    with _LOCK:
        nodes = _read_nodes_unlocked()
        if any(str(node.get("uri") or "") == clean_uri for node in nodes):
            raise ValueError("该 VLESS 分享链接已经存在于代理列表")
        stamp = _now()
        node = {
            "id": str(uuid.uuid4()),
            "name": _clean_name(name, clean_uri),
            "uri": clean_uri,
            "enabled": False,
            "created_at": stamp,
            "updated_at": stamp,
        }
        nodes.append(node)
        _write_nodes_unlocked(nodes)
        return _public(node)


def edit_vless_node(node_id: str, name: str, uri: str = "") -> dict[str, Any]:
    clean_id = (node_id or "").strip()
    with _LOCK:
        nodes = _read_nodes_unlocked()
        target: dict[str, Any] | None = None
        for node in nodes:
            if node["id"] == clean_id:
                target = node
                break
        if target is None:
            raise KeyError("VLESS 节点不存在")
        new_uri = (uri or "").strip() or str(target["uri"])
        parse_vless_uri(new_uri)
        for node in nodes:
            if node["id"] != clean_id and str(node.get("uri") or "") == new_uri:
                raise ValueError("该 VLESS 分享链接已经存在于代理列表")
        target["uri"] = new_uri
        target["name"] = _clean_name(name or str(target.get("name") or ""), new_uri)
        target["updated_at"] = _now()
        _write_nodes_unlocked(nodes)
        return _public(target)


def mark_active_vless_node(node_id: str) -> dict[str, Any]:
    clean_id = (node_id or "").strip()
    with _LOCK:
        nodes = _read_nodes_unlocked()
        target: dict[str, Any] | None = None
        stamp = _now()
        for node in nodes:
            active = node["id"] == clean_id
            if active:
                target = node
            if bool(node.get("enabled")) != active:
                node["updated_at"] = stamp
            node["enabled"] = active
        if target is None:
            raise KeyError("VLESS 节点不存在")
        _write_nodes_unlocked(nodes)
        return _public(target)


def disable_vless_node(node_id: str) -> dict[str, Any]:
    clean_id = (node_id or "").strip()
    with _LOCK:
        nodes = _read_nodes_unlocked()
        target: dict[str, Any] | None = None
        for node in nodes:
            if node["id"] == clean_id:
                target = node
                if node.get("enabled"):
                    node["enabled"] = False
                    node["updated_at"] = _now()
                break
        if target is None:
            raise KeyError("VLESS 节点不存在")
        _write_nodes_unlocked(nodes)
        return _public(target)


def disable_all_vless_nodes() -> None:
    with _LOCK:
        nodes = _read_nodes_unlocked()
        changed = False
        stamp = _now()
        for node in nodes:
            if node.get("enabled"):
                node["enabled"] = False
                node["updated_at"] = stamp
                changed = True
        if changed or not _POOL_FILE.exists():
            _write_nodes_unlocked(nodes)


def delete_vless_node(node_id: str) -> dict[str, Any]:
    clean_id = (node_id or "").strip()
    with _LOCK:
        nodes = _read_nodes_unlocked()
        target = next((node for node in nodes if node["id"] == clean_id), None)
        if target is None:
            raise KeyError("VLESS 节点不存在")
        remaining = [node for node in nodes if node["id"] != clean_id]
        _write_nodes_unlocked(remaining)
        return _public(target)
