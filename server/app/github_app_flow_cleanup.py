from __future__ import annotations

import asyncio
from contextlib import suppress

from app.github_app_flow import GitHubAppInstallationFlowStore

_cleanup_task: asyncio.Task[None] | None = None


async def _cleanup_loop() -> None:
    while True:
        try:
            await asyncio.to_thread(GitHubAppInstallationFlowStore().scrub_expired)
        except Exception:
            # Flow requests remain fail-closed even if a best-effort janitor iteration fails.
            # Do not crash the API worker; the next minute and every flow access retry cleanup.
            pass
        await asyncio.sleep(60)


async def start_github_app_flow_cleanup() -> None:
    global _cleanup_task
    if _cleanup_task is None or _cleanup_task.done():
        _cleanup_task = asyncio.create_task(_cleanup_loop(), name="fdex-github-app-flow-cleanup")


async def stop_github_app_flow_cleanup() -> None:
    global _cleanup_task
    task = _cleanup_task
    _cleanup_task = None
    if task is None:
        return
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task
