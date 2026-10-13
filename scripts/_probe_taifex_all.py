# -*- coding: utf-8 -*-
"""查 TAIFEX 股期商品代碼 ↔ SpotID 對應（不限盤中）"""
import sys, json, urllib.request
from urllib.request import Request
sys.stdout.reconfigure(encoding="utf-8")

API = "https://mis.taifex.com.tw/futures/api/getQuoteList"
HEADERS = {
    "Content-Type": "application/json",
    "User-Agent": "Mozilla/5.0",
    "Origin":  "https://mis.taifex.com.tw",
    "Referer": "https://mis.taifex.com.tw/futures/RegularSession/",
}

# 試不同的 Hot 值和 ExpireMonth
for hot, em in [("T", ""), ("T", "0"), ("F", ""), ("", ""), ("", "0")]:
    payload = {
        "MarketType": "0", "SymbolType": "F", "KindID": "4",
        "Hot": hot, "CID": "", "ExpireMonth": em,
        "SortColumn": "CTotalVolume", "AscDesc": "D",
    }
    data = json.dumps(payload).encode()
    req  = Request(API, data=data, headers=HEADERS, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            d = json.loads(r.read().decode())
        items = d.get("RtData", {}).get("QuoteList", [])
        print(f"Hot={hot!r:3} ExpireMonth={em!r:3} → {len(items)} 筆")
        if items:
            print("  第一筆全欄位:")
            for k, v in items[0].items():
                print(f"    {k}: {v}")
            break
    except Exception as e:
        print(f"Hot={hot!r:3} ExpireMonth={em!r:3} → 錯誤: {e}")
