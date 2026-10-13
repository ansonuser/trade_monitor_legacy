"""
快速測試 TWSE mis API 是否正常
用法: venv/Scripts/python.exe scripts/test_api.py
"""
import asyncio, aiohttp

MIS_URL = "https://mis.twse.com.tw/stock/api/getStockInfo.jsp"
TEST_STOCK = "tse_2330.tw"  # 台積電

async def main():
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://mis.twse.com.tw/stock/index.jsp",
    }
    async with aiohttp.ClientSession(headers=headers) as session:
        # Step 1: 拿 cookie
        print("1. 取得 JSESSIONID...")
        async with session.get("https://mis.twse.com.tw/stock/index.jsp",
                               timeout=aiohttp.ClientTimeout(total=10)) as r:
            print(f"   index.jsp status={r.status}  cookies={dict(session.cookie_jar.filter_cookies('https://mis.twse.com.tw'))}")

        # Step 2: 拿台積電報價
        print("2. 抓台積電報價...")
        params = {"ex_ch": TEST_STOCK, "json": "1", "delay": "0"}
        async with session.get(MIS_URL, params=params,
                               timeout=aiohttp.ClientTimeout(total=10)) as r:
            text = await r.text()
            print(f"   status={r.status}")
            print(f"   body={text[:300]}")

asyncio.run(main())
