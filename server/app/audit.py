from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import Request

from app.config import SERVER_DIR
from app.security import client_ip

AUDIT_FILE = SERVER_DIR / "data" / "admin-audit.log"


def write_audit(request: Request, action: str, success: bool = True, **details: Any) -> None:
    AUDIT_FILE.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "time": datetime.now(timezone.utc).isoformat(),
        "ip": client_ip(request),
        "user": request.session.get("admin_user", "anonymous"),
        "action": action,
        "success": success,
        "details": details,
    }
    with AUDIT_FILE.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
    os.chmod(AUDIT_FILE, 0o600)


def read_audit(limit: int = 100) -> list[dict[str, Any]]:
    if not AUDIT_FILE.exists():
        return []
    lines = AUDIT_FILE.read_text(encoding="utf-8", errors="replace").splitlines()[-limit:]
    records: list[dict[str, Any]] = []
    for line in reversed(lines):
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            records.append(value)
    return records
