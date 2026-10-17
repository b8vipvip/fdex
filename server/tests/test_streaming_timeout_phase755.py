from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from app import codex_provider_smoke as smoke
from app import codex_provider_smoke_report as reports
from app.config import Settings
from app.stream_timeout import upstream_stream_timeout


def test_streaming_read_timeout_is_independent_of_provider_timeout() -> None:
    timeout = upstream_stream_timeout(60, read_timeout_seconds=3600)
    assert isinstance(timeout, httpx.Timeout)
    assert timeout.connect == 15
    assert timeout.write == 60
    assert timeout.pool == 60
    assert timeout.read == 3600
    # A provider configured for 120 seconds no longer disconnects a stream
    # after 120 seconds without data.
    assert upstream_stream_timeout(120, read_timeout_seconds=3600).read == 3600
    assert upstream_stream_timeout(5, read_timeout_seconds=0).read is None
    with pytest.raises(ValueError):
        upstream_stream_timeout(60, read_timeout_seconds=-1)


def test_long_running_stream_and_smoke_settings_are_separate() -> None:
    settings = Settings(_env_file=None)
    assert settings.fdex_ai_stream_read_timeout_seconds == 3600
    assert settings.fdex_codex_smoke_turn_idle_timeout_seconds == 1800
    assert settings.fdex_codex_smoke_turn_max_seconds == 3600
    assert Settings(_env_file=None, fdex_ai_stream_read_timeout_seconds=0).fdex_ai_stream_read_timeout_seconds == 0


class _FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def tick(self, seconds: float) -> None:
        self.now += seconds

    def time(self) -> float:
        return self.now


class _FakeCodex:
    def __init__(self, clock: _FakeClock, steps: list[tuple[float, str, dict]]) -> None:
        self.clock = clock
        self.steps = iter(steps)

    async def request(self, method: str, params: dict, *, timeout: float) -> dict:
        assert method == "turn/start"
        return {"turn": {"id": "turn-long"}}

    async def next_notification(self, *, timeout: float) -> tuple[str, dict]:
        try:
            seconds, method, params = next(self.steps)
        except StopIteration:
            self.clock.tick(timeout)
            raise smoke.CodexRpcError("notification", -32002, "notification wait timed out") from None
        self.clock.tick(seconds)
        return method, params


def _run_fake_turn(
    monkeypatch: pytest.MonkeyPatch,
    clock: _FakeClock,
    steps: list[tuple[float, str, dict]],
    *,
    idle: float,
    maximum: float,
    report: reports.CodexSmokeReport | None = None,
) -> dict:
    monkeypatch.setattr(smoke, "_turn_now", clock.time)
    return asyncio.run(smoke._run_turn(
        _FakeCodex(clock, steps),
        "thread-long",
        "private prompt",
        timeout=idle,
        max_timeout=maximum,
        stage="wire",
        report=report,
    ))


def test_smoke_allows_progressing_turn_longer_than_previous_120s_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = _FakeClock()
    result = _run_fake_turn(monkeypatch, clock, [
        (100, "item/started", {"turnId": "turn-long", "item": {"type": "reasoning"}}),
        (100, "item/completed", {"turnId": "turn-long", "item": {"type": "reasoning"}}),
        (100, "turn/completed", {"turn": {"id": "turn-long", "status": "completed"}}),
    ], idle=120, maximum=600)
    assert result["turn_id"] == "turn-long"
    assert clock.now == 300


def test_smoke_times_out_on_genuine_inactivity_with_diagnostic(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    clock = _FakeClock()
    monkeypatch.setattr(reports, "REPORT_ROOT", tmp_path)
    report = reports.CodexSmokeReport(7, "f" * 32)
    with pytest.raises(smoke.CodexProviderSmokeError, match="no progress for 120s"):
        _run_fake_turn(monkeypatch, clock, [], idle=120, maximum=600, report=report)
    lines = [json.loads(line) for line in report.read().splitlines()]
    assert lines[-1]["event"] == "turn_timeout"
    assert lines[-1]["timeout_kind"] == "no_progress"
    assert lines[-1]["idle_seconds"] >= 120


def test_smoke_max_duration_stops_endlessly_progressing_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = _FakeClock()
    with pytest.raises(smoke.CodexProviderSmokeError, match="maximum duration of 200s"):
        _run_fake_turn(monkeypatch, clock, [
            (100, "item/started", {"turnId": "turn-long", "item": {"type": "reasoning"}}),
            (100, "item/completed", {"turnId": "turn-long", "item": {"type": "reasoning"}}),
        ], idle=120, maximum=200)


def test_unrelated_turn_events_cannot_keep_a_stalled_turn_alive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = _FakeClock()
    with pytest.raises(smoke.CodexProviderSmokeError, match="no progress for 120s"):
        _run_fake_turn(monkeypatch, clock, [
            (100, "item/started", {"turnId": "other-turn", "item": {"type": "reasoning"}}),
            (100, "item/completed", {"turnId": "other-turn", "item": {"type": "reasoning"}}),
        ], idle=120, maximum=600)


def test_streaming_routes_use_separate_read_timeout_not_provider_timeout() -> None:
    root = Path(__file__).resolve().parents[1] / "app"
    main = (root / "client_ai.py").read_text(encoding="utf-8")
    proxy = (root / "memory_provider_proxy.py").read_text(encoding="utf-8")
    smoke_source = (root / "codex_provider_smoke.py").read_text(encoding="utf-8")
    template = (root / "templates" / "codex_provider_rollout.html").read_text(encoding="utf-8")
    assert "upstream_stream_timeout(" in main
    assert "stream_read_timeout_seconds=stream_read_timeout_seconds" in main
    assert "upstream_stream_timeout(" in proxy
    assert "max_timeout=max_turn_seconds" in smoke_source
    assert "fdex_codex_smoke_turn_idle_timeout_seconds" in template
    assert "stage_timeout = max(45.0, min(180.0" not in smoke_source
