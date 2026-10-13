# -*- coding: utf-8 -*-
"""
TAIFEX 個股期貨報價抓取
- 熱門個股期貨 Top N（按成交量，-F 結尾 = 個股期貨合約）
- 合約月份跟隨成交量：每檔股票只保留量最大的月份，換月熱點自動偵測、去重

獨立執行：
    .venv/Scripts/python.exe monitor/taifex_fetcher.py
    .venv/Scripts/python.exe monitor/taifex_fetcher.py --interval 30
"""

import argparse, datetime, json, os, time
import urllib.request
from urllib.request import Request

TAIFEX_API  = "https://mis.taifex.com.tw/futures/api/getQuoteList"
REQ_HEADERS = {
    "Content-Type": "application/json",
    "Accept":       "application/json, text/plain, */*",
    "User-Agent":   "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Origin":       "https://mis.taifex.com.tw",
    "Referer":      "https://mis.taifex.com.tw/futures/RegularSession/",
}


def _post(payload: dict, timeout: int = 15) -> dict:
    data = json.dumps(payload, ensure_ascii=False).encode()
    req  = Request(TAIFEX_API, data=data, headers=REQ_HEADERS, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _f(v) -> float | None:
    try:
        return float(str(v).replace(",", "").strip()) if v else None
    except Exception:
        return None


def _parse_month(symbol_id: str) -> str:
    """從 SymbolID（如 CCFH6-F）解出合約月份 'YYYY/MM'：A–L = 1–12 月，末碼 = 西元年個位"""
    core = symbol_id.split("-")[0]
    if len(core) < 2:
        return ""
    mc, yd = core[-2], core[-1]
    month  = ord(mc.upper()) - ord("A") + 1
    if not (1 <= month <= 12) or not yd.isdigit():
        return ""
    y = datetime.date.today().year
    if int(yd) != y % 10:               # 跨年（12月→次年1月）時修正年份
        y += 1
    return f"{y}/{month:02d}"


def _third_wednesday(year: int, month: int) -> datetime.date:
    """該月第三個星期三＝台灣期貨（含個股期）最後結算日"""
    first  = datetime.date(year, month, 1)
    offset = (2 - first.weekday()) % 7          # weekday: 一=0 … 三=2
    return first + datetime.timedelta(days=offset + 14)


def _is_settlement_day(today: datetime.date | None = None) -> bool:
    """今天是否為結算日（第三個星期三）。注意：遇國定假日順延不在此判斷內。"""
    today = today or datetime.date.today()
    return today == _third_wednesday(today.year, today.month)


def _next_month_code(today: datetime.date | None = None) -> str:
    """次月合約月份代碼 YYYYMM（供 ExpireMonth 指定）"""
    today = today or datetime.date.today()
    y, m  = today.year, today.month + 1
    if m == 13:
        m, y = 1, y + 1
    return f"{y}{m:02d}"


def _quote_list(expire_month: str) -> list[dict]:
    """向 TAIFEX 取熱門個股期貨報價；expire_month='' 為全月份，'YYYYMM' 指定單一月份"""
    d = _post({
        "MarketType": "0", "SymbolType": "F", "KindID": "4",
        "Hot": "T", "CID": "", "ExpireMonth": expire_month,
        "SortColumn": "CTotalVolume", "AscDesc": "D",
    })
    return d.get("RtData", {}).get("QuoteList", [])


def fetch_hot_stock_futures(top_n: int = 20) -> list[dict]:
    """熱門個股期貨 Top N（-F 合約，SpotID = 股票代號）。
    平常抓全部月份熱門即可（近月主導）；每檔股票只保留量最大的月份，
    換月熱點自動偵測、同一檔不重複佔位。
    結算日近月將到期、去重後名單變短，再補抓一批次月合併，把名額補回來。"""
    try:
        rows = _quote_list("")                       # 全月份熱門（近月主導）
        if _is_settlement_day():                     # 結算日：補抓次月熱門
            rows += _quote_list(_next_month_code())
        # 每檔 spot_id 只留成交量最大的合約（跨批次比較，換月自動切到量大的月份）
        best: dict[str, dict] = {}
        for item in rows:
            sym = item.get("SymbolID", "")
            sid = item.get("SpotID")
            vol = _f(item.get("CTotalVolume"))
            if not sym.endswith("-F") or not sid or not vol:
                continue
            cur = best.get(sid)
            if cur is None or vol > _f(cur.get("CTotalVolume")):
                best[sid] = item
        ranked = sorted(best.values(),
                        key=lambda x: _f(x.get("CTotalVolume")), reverse=True)[:top_n]
        results = []
        for item in ranked:
            results.append({
                "symbol_id": item.get("SymbolID"),
                "spot_id":   item.get("SpotID"),
                "name":      item.get("DispCName"),
                "expire":    _parse_month(item.get("SymbolID", "")),
                "price":     _f(item.get("CLastPrice")),
                "ref_price": _f(item.get("CRefPrice")),
                "diff":      _f(item.get("CDiff")),
                "diff_pct":  _f(item.get("CDiffRate")),
                "volume":    _f(item.get("CTotalVolume")),
                "bid":       _f(item.get("CBidPrice1")),
                "ask":       _f(item.get("CAskPrice1")),
                "ts":        datetime.datetime.now().isoformat(),
            })
        return results
    except Exception as e:
        print(f"[個股期貨] 抓取失敗: {e}")
        return []


def write_to_redis(redis_cli, stock_futures: list[dict]):
    try:
        if stock_futures:
            redis_cli.set("taifex:stock_futures",
                          json.dumps(stock_futures, ensure_ascii=False))
        redis_cli.set("taifex:timestamp", datetime.datetime.now().isoformat())
    except Exception as e:
        print(f"[Redis] 寫入失敗: {e}")


def print_snapshot(stock_futures: list[dict]):
    now = datetime.datetime.now().strftime("%H:%M:%S")
    if not stock_futures:
        print(f"[{now}] 個股期貨 無資料")
        return
    print(f"[{now}] 熱門個股期貨 Top {len(stock_futures)}：")
    for f in stock_futures:
        sign = "+" if (f.get("diff_pct") or 0) >= 0 else ""
        print(f"  {f.get('spot_id'):6} {f.get('name'):16} [{f.get('expire') or '--'}] "
              f"價={f.get('price'):>10}  {sign}{f.get('diff_pct')}%  "
              f"量={f.get('volume'):.0f}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--interval", type=int, default=30, help="掃描間隔秒數（預設 30）")
    ap.add_argument("--once",     action="store_true", help="只執行一次後退出")
    ap.add_argument("--top",      type=int, default=20, help="熱門個股期貨筆數（預設 20）")
    ap.add_argument("--no-redis", action="store_true", help="不寫入 Redis")
    args = ap.parse_args()

    redis_cli = None
    if not args.no_redis:
        try:
            import redis
            host = os.getenv("REDIS_HOST", "localhost")
            port = int(os.getenv("REDIS_PORT", 6379))
            redis_cli = redis.Redis(host=host, port=port,
                                    db=int(os.getenv("REDIS_DB", 0)),
                                    decode_responses=True)
            redis_cli.ping()
            print(f"[Redis] 連線成功 {host}:{port}")
        except Exception as e:
            print(f"[Redis] 連線失敗: {e}，繼續純輸出模式")
            redis_cli = None

    print(f"TAIFEX 個股期貨監控  interval={args.interval}s  top={args.top}")
    while True:
        stock_futures = fetch_hot_stock_futures(args.top)
        print_snapshot(stock_futures)
        if redis_cli:
            write_to_redis(redis_cli, stock_futures)
        if args.once:
            break
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
