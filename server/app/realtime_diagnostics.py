from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import SERVER_DIR

DIAGNOSTIC_FILE = SERVER_DIR / "data" / "realtime-voice.log"


def write_realtime_diagnostic(session_id: str, event: str, **details: Any) -> None:
    """Append one privacy-safe JSONL diagnostic record for a realtime voice session.

    Never pass API keys, auth headers, raw PCM/base64 audio, or message/transcript text here.
    The file is intentionally owner-only because it may contain provider/model names and IPs.
    """
    DIAGNOSTIC_FILE.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "time": datetime.now(timezone.utc).isoformat(),
        "session_id": str(session_id or ""),
        "event": str(event or ""),
        "details": details,
    }
    payload = (json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
    fd = os.open(DIAGNOSTIC_FILE, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.write(fd, payload)
    finally:
        os.close(fd)
    try:
        os.chmod(DIAGNOSTIC_FILE, 0o600)
    except OSError:
        pass
