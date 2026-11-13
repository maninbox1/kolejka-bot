"""Print the structure of the raw API responses (no personal data, just keys/types/samples)."""
import asyncio
import json

from fetcher import BrowserFetcher


def describe(obj):
    if isinstance(obj, dict):
        return {k: describe(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return f"list[{len(obj)}] first={json.dumps(obj[0], ensure_ascii=False)[:300]}" if obj else "list[0]"
    return f"{type(obj).__name__}: {str(obj)[:80]}"


async def main():
    f = BrowserFetcher()
    await f.start()
    try:
        q, s = await f.fetch("bruzgi")
    finally:
        await f.stop()
    print("=== monitoring-new ===")
    print(json.dumps(describe(q), ensure_ascii=False, indent=1))
    print("=== statistics ===")
    print(json.dumps(describe(s), ensure_ascii=False, indent=1))


asyncio.run(main())
