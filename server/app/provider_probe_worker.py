from __future__ import annotations

import asyncio
import json

from app.provider_manager import run_due_provider_tests


async def main() -> None:
    results = await run_due_provider_tests()
    if not results:
        print("FDEX AI 供应商：当前没有到期的自动深度测试任务。")
        return
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
