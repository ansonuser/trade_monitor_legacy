import requests, json, sys
sys.stdout.reconfigure(encoding="utf-8")

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
BASE = "https://mis.taifex.com.tw/futures/api"
HEADERS = {
    "Content-Type": "application/json",
    "User-Agent": UA,
    "Referer": "https://mis.taifex.com.tw/futures/RegularSession/StockProducts/HotStockFutures/",
}

# HotStockFutures：按成交量排序的熱門個股期貨
r = requests.post(f"{BASE}/getQuoteList", json={
    "MarketType": "0",
    "SymbolType": "F",
    "KindID":     "4",
    "Hot":        "T",
    "CID":        "",
    "ExpireMonth": "",
    "SortColumn": "CTotalVolume",
    "AscDesc":    "D",
}, headers=HEADERS, timeout=15)
d = r.json()
items = d.get("RtData", {}).get("QuoteList", [])
count = d.get("RtData", {}).get("QuoteCount", "?")
print(f"HotStockFutures: count={count}  got={len(items)}")
print()
for item in items:
    sid   = item.get("SymbolID", "")
    spot  = item.get("SpotID", "")
    name  = item.get("DispCName", "")
    price = item.get("CLastPrice", "")
    diff  = item.get("CDiffRate", "")
    vol   = item.get("CTotalVolume", "")
    bid   = item.get("CBidPrice1", "")
    ask   = item.get("CAskPrice1", "")
    print(f"  {sid:16} spot={spot:6} {name:16} 價={price:>10} {diff:>6}% 量={vol}")
