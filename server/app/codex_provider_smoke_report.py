from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.config import SERVER_DIR


REPORT_ROOT = SERVER_DIR / "data" / "codex-provider-smoke-reports"
MAX_REPORT_BYTES = 2_000_000
MAX_REPORTS_PER_PROVIDER = 30
logger = logging.getLogger("fdex.codex_provider_smoke_report")


class CodexSmokeReport:
    """Bounded, durable, credential-redacted JSONL diagnostic for one real smoke run.

    The report deliberately stores protocol event *metadata*, never raw prompts,
    response text, tool arguments, environment variables or authentication headers.
    It lives outside the disposable smoke workspace and survives worker restarts.
    """

    def __init__(self, provider_id: int, run_id: str, *, api_key: str = "") -> None:
        if int(provider_id) <= 0 or not re.fullmatch(r"[0-9a-f]{32}", str(run_id)):
            raise ValueError("Invalid Codex smoke report identifier")
        self.provider_id = int(provider_id)
        self.run_id = str(run_id)
        self.api_key = api_key
        self.path = REPORT_ROOT / str(self.provider_id) / (self.run_id + ".jsonl")

    def _redact(self, value: str) -> str:
        if self.api_key:
            value = value.replace(self.api_key, "[REDACTED]")
        value = re.sub(r"(?i)(bearer\s+)[^\s\"',;]+", r"\1[REDACTED]", value)
        value = re.sub(r"\bsk-[A-Za-z0-9_-]{8,}\b", "sk-[REDACTED]", value)
        value = re.sub(
            r"(?i)((?:api[_-]?key|authorization|access[_-]?token)\s*[:=]\s*)[^\s,\"']+",
            r"\1[REDACTED]",
            value,
        )
        return value[:600]

    def _safe_value(self, value: Any) -> str | int | float | bool | list[str] | None:
        if value is None or isinstance(value, (int, float, bool)):
            return value
        if isinstance(value, (list, tuple, set)):
            return [self._redact(str(item)) for item in list(value)[:40]]
        return self._redact(str(value))

    def write(self, event: str, **fields: Any) -> None:
        data: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "provider_id": self.provider_id,
            "run_id": self.run_id,
            "event": self._redact(event)[:80],
        }
        for key, value in fields.items():
            if re.search(r"(?i)(secret|api_key|password|authorization|prompt|response_text)", key):
                continue
            data[key] = self._safe_value(value)
        line = json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n"
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            if self.path.exists() and self.path.stat().st_size + len(line.encode("utf-8")) > MAX_REPORT_BYTES:
                return
            with self.path.open("a", encoding="utf-8") as stream:
                stream.write(line)
            self.path.chmod(0o600)
        except OSError:
            # Observability must never change a provider's compatibility result.
            logger.warning("Cannot append Codex smoke diagnostic provider_id=%s run_id=%s", self.provider_id, self.run_id)

    def exists(self) -> bool:
        return self.path.is_file()

    def read(self) -> str:
        return self.path.read_text(encoding="utf-8")

    @classmethod
    def prune(cls, provider_id: int) -> None:
        """Keep a bounded history; never remove the newest 30 reports."""
        folder = REPORT_ROOT / str(int(provider_id))
        if not folder.is_dir():
            return
        try:
            reports = sorted(folder.glob("*.jsonl"), key=lambda path: path.stat().st_mtime, reverse=True)
            for old in reports[MAX_REPORTS_PER_PROVIDER:]:
                old.unlink(missing_ok=True)
        except OSError:
            logger.warning("Cannot prune Codex smoke diagnostics provider_id=%s", provider_id)
