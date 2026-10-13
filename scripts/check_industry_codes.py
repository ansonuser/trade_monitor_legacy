"""
印出 API 實際回傳的 i 欄位值與對應股票範例
用法: venv/Scripts/python.exe scripts/check_industry_codes.py
"""
import asyncio, aiohttp, sys
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from monitor.scanner import fetch_stock_list, poll_all

async def main():
    print("抓股票清單...")
    stocks = fetch_stock_list()
    ex_chs = [s["ex_ch"] for s in stocks]

    print(f"抓 {len(ex_chs)} 支即時報價...")
    raw = await poll_all(ex_chs)

    # group by i code
    by_code: dict[str, list[str]] = defaultdict(list)
    for d in raw:
        code = str(d.get("i", "?")).strip()
        name = d.get("n", "")
        sid  = d.get("c", "")
        by_code[code].append(f"{sid} {name}")

    print(f"\n{'代碼':<6} {'股票數':>6}  範例股票")
    print("-" * 60)
    for code in sorted(by_code.keys(), key=lambda x: (len(x), x)):
        samples = ", ".join(by_code[code][:3])
        print(f"{code:<6} {len(by_code[code]):>6}  {samples}")

asyncio.run(main())
