# -*- coding: utf-8 -*-
"""
rs_monitor.py  -  尾盤 RS 族群輪動監控

運作方式:
  啟動時: 從 PostgreSQL 載入歷史資料（5日前收盤、MA20/60、均量）
  每輪:   從 Redis 讀 scanner.py 的即時快照 (tracker:stocks)
          計算 5日RS、量比、均線條件
          輸出族群分數 >= cluster-min 的候選清單

用法:
  .venv/Scripts/python.exe monitor/rs_monitor.py
  .venv/Scripts/python.exe monitor/rs_monitor.py --once
  .venv/Scripts/python.exe monitor/rs_monitor.py --interval 30 --rs-min 5 --cluster-min 3
"""

import argparse
import json
import os
import time
import datetime
from pathlib import Path
from collections import defaultdict

import psycopg2
import pandas as pd
import redis

THEMES_FILE = Path(__file__).resolve().parent.parent / "data" / "themes.json"

RS_DAYS     = 5     # 回顧天數（與回測一致）
MA_SHORT    = 20
MA_LONG     = 60
SLOPE_SHORT = 5     # slope = MA_SHORT - MA_SHORT.shift(SLOPE_SHORT)
SLOPE_LONG  = 10


def parse_args():
    p = argparse.ArgumentParser(description="尾盤 RS 族群輪動監控")
    p.add_argument("--interval",    default=60,   type=int,   help="掃描間隔秒數（預設60）")
    p.add_argument("--once",        action="store_true",      help="掃描一次即退出")
    p.add_argument("--rs-min",      default=10.0, type=float, help="RS門檻%%（預設10）")
    p.add_argument("--vol-ratio",   default=1.2,  type=float, help="量比門檻（預設1.2）")
    p.add_argument("--min-vol",     default=1000, type=float, help="20日均量門檻張數（預設1000）")
    p.add_argument("--cluster-min", default=3,    type=int,   help="最低族群分數（預設3）")
    p.add_argument("--top-n",       default=30,   type=int,   help="顯示前N支（預設30）")
    p.add_argument("--no-ma",       action="store_true",      help="關閉均線過濾")
    return p.parse_args()


# ── PostgreSQL 歷史資料 ───────────────────────────────────────────────────────

def load_history() -> tuple[dict, float, float]:
    """
    從 PostgreSQL 載入近 90 交易日資料，回傳:
      stock_ref: {sid: {prev_close, price_5d, ma20, ma60, slope20, slope60, avg_vol}}
      tsea_prev:  TSEA 昨收
      tsea_5d:    TSEA 5日前收盤
    """
    print("[PostgreSQL] 載入歷史資料...")
    conn = psycopg2.connect(
        host="localhost", port=5432,
        dbname="postgres", user="postgres", password="12345678"
    )
    cur = conn.cursor()

    since = (datetime.date.today() - datetime.timedelta(days=150)).isoformat()

    # TSEA
    cur.execute("""
        SELECT trade_date, close FROM daily_kbar_table
        WHERE stock_id = 'TSEA' AND trade_date >= %s ORDER BY trade_date
    """, (since,))
    tsea_rows = cur.fetchall()

    # 全部個股
    cur.execute("""
        SELECT stock_id, trade_date, close, vol
        FROM daily_kbar_table
        WHERE stock_id ~ '^[0-9]{4,5}$' AND trade_date >= %s
        ORDER BY stock_id, trade_date
    """, (since,))
    stock_rows = cur.fetchall()
    cur.close(); conn.close()

    print(f"[PostgreSQL] TSEA {len(tsea_rows)} 筆 / 個股 {len(stock_rows):,} 筆")

    # ── TSEA baseline ──
    tsea_s = pd.Series({r[0]: float(r[1]) for r in tsea_rows})
    tsea_s.index = pd.to_datetime(tsea_s.index)
    tsea_s = tsea_s.sort_index()

    tsea_prev = float(tsea_s.iloc[-1])          # 昨收（最新一日）
    tsea_5d   = float(tsea_s.iloc[-1 - RS_DAYS]) if len(tsea_s) > RS_DAYS else tsea_prev
    tsea_5d_ret = (tsea_prev - tsea_5d) / tsea_5d * 100   # 純歷史（不含今日）

    # ── 個股 baseline ──
    df = pd.DataFrame(stock_rows, columns=["stock_id", "trade_date", "close", "vol"])
    df["close"]      = df["close"].astype(float)
    df["vol"]        = pd.to_numeric(df["vol"], errors="coerce").fillna(0)
    df["trade_date"] = pd.to_datetime(df["trade_date"])

    stock_ref = {}
    for sid, grp in df.groupby("stock_id"):
        grp = grp.sort_values("trade_date")
        ps  = grp["close"].values
        vs  = grp["vol"].values

        if len(ps) < MA_LONG + SLOPE_LONG + 2:
            continue

        prev_close = float(ps[-1])
        price_5d   = float(ps[-1 - RS_DAYS]) if len(ps) > RS_DAYS else prev_close

        ma20_now   = ps[-MA_SHORT:].mean()
        ma60_now   = ps[-MA_LONG:].mean()
        ma20_prev5 = ps[-MA_SHORT - SLOPE_SHORT: -SLOPE_SHORT].mean()
        ma60_prev10= ps[-MA_LONG  - SLOPE_LONG:  -SLOPE_LONG ].mean()
        slope20    = ma20_now - ma20_prev5
        slope60    = ma60_now - ma60_prev10

        avg_vol = float(vs[-20:].mean()) if len(vs) >= 20 else float(vs.mean())

        stock_ref[sid] = {
            "prev_close": prev_close,
            "price_5d":   price_5d,
            "ma20":       ma20_now,
            "ma60":       ma60_now,
            "slope20":    slope20,
            "slope60":    slope60,
            "avg_vol":    avg_vol,
            "tsea_5d_ret": tsea_5d_ret,
        }

    print(f"[PostgreSQL] 完成，{len(stock_ref)} 支股票基準資料，TSEA 5d ret={tsea_5d_ret:+.2f}%")
    return stock_ref, tsea_prev, tsea_5d_ret


# ── 主題載入 ─────────────────────────────────────────────────────────────────

def load_themes(min_stocks: int = 3) -> tuple[dict, dict]:
    with THEMES_FILE.open(encoding="utf-8") as f:
        raw = json.load(f)
    stock_to_themes = defaultdict(list)
    theme_to_stocks = {}
    for theme, sids in raw.items():
        if len(sids) < min_stocks:
            continue
        theme_to_stocks[theme] = set(sids)
        for sid in sids:
            stock_to_themes[sid].append(theme)
    return dict(stock_to_themes), theme_to_stocks


# ── Redis 讀取 ────────────────────────────────────────────────────────────────

def read_redis(r: redis.Redis) -> dict:
    """讀 tracker:stocks，回傳 {sid: {price, day_chg_pct, volume_total, name}}"""
    raw = r.get("tracker:stocks")
    if not raw:
        return {}
    data = json.loads(raw)
    return {s["sid"]: s for s in data if s.get("sid") and s.get("price")}


# ── 掃描 ─────────────────────────────────────────────────────────────────────

def scan(live: dict, stock_ref: dict, tsea_5d_ret: float,
         stock_to_themes: dict, theme_to_stocks: dict, args) -> list[dict]:
    """
    計算每支股票的 RS、量比、均線，回傳候選清單
    """
    # 用今日大盤即時估算 TSEA 當日變動（用加權 day_chg 近似）
    # 簡化：直接用 PostgreSQL 的 tsea_5d_ret（純歷史），誤差在合理範圍
    tsea_ret = tsea_5d_ret   # 可在後續改為含今日盤中的估算

    candidates = []
    for sid, live_s in live.items():
        ref = stock_ref.get(sid)
        if ref is None:
            continue

        live_price = float(live_s.get("price", 0) or 0)
        if live_price <= 0:
            continue

        # 5日 RS（用即時價 vs 5日前收盤）
        price_5d = ref["price_5d"]
        if price_5d <= 0:
            continue
        stock_5d_ret = (live_price / price_5d - 1) * 100
        rs = stock_5d_ret - tsea_ret

        if rs < args.rs_min:
            continue

        # 量比（今日累計量 vs 20日均量）
        vol_today = float(live_s.get("volume_total", 0) or 0)
        avg_vol   = ref["avg_vol"]
        if avg_vol < args.min_vol:
            continue
        vol_ratio = vol_today / avg_vol if avg_vol > 0 else 0
        if vol_ratio < args.vol_ratio:
            continue

        # 均線
        if not args.no_ma:
            ma20, ma60 = ref["ma20"], ref["ma60"]
            if not (live_price > ma20 > ma60):
                continue
            if ref["slope20"] <= 0 or ref["slope60"] <= 0:
                continue

        candidates.append({
            "sid":       sid,
            "name":      live_s.get("name", ""),
            "price":     round(live_price, 2),
            "rs":        round(rs, 2),
            "stock_ret": round(stock_5d_ret, 2),
            "vol_ratio": round(vol_ratio, 2),
            "vol_today": int(vol_today),
            "day_chg":   round(float(live_s.get("day_chg_pct", 0) or 0), 2),
        })

    if not candidates:
        return []

    # 族群分數
    trigger_set = {c["sid"] for c in candidates}
    for c in candidates:
        best = 0
        best_theme = ""
        for theme in stock_to_themes.get(c["sid"], []):
            co = len(trigger_set & theme_to_stocks[theme]) - 1
            if co > best:
                best = co
                best_theme = theme
        c["cluster"]    = best
        c["best_theme"] = best_theme

    # 過濾 + 排序
    result = [c for c in candidates if c["cluster"] >= args.cluster_min]
    result.sort(key=lambda x: (-x["cluster"], -x["rs"]))
    return result


# ── 顯示 ─────────────────────────────────────────────────────────────────────

def display(candidates: list[dict], tsea_5d_ret: float, args):
    now = datetime.datetime.now().strftime("%H:%M:%S")
    sep = "=" * 88

    print(f"\n{sep}")
    print(f"  RS 族群監控  {now}  |  RS>={args.rs_min:.0f}%/5日  "
          f"|  量比>={args.vol_ratio}  |  族群>={args.cluster_min}支")
    print(f"  TSEA 5日歷史報酬: {tsea_5d_ret:+.2f}%")
    print(sep)

    if not candidates:
        print("  目前無符合條件的股票")
        return

    # 族群彙整
    theme_summary: dict[str, list] = defaultdict(list)
    for c in candidates:
        if c["best_theme"]:
            theme_summary[c["best_theme"]].append(c)

    # 先印族群摘要
    if theme_summary:
        print(f"  {'族群名稱':<16}  {'支數':>4}  {'平均RS':>7}  {'股票清單'}")
        print(f"  {'-'*70}")
        for theme, stocks in sorted(theme_summary.items(),
                                    key=lambda x: -len(x[1]))[:10]:
            avg_rs   = sum(s["rs"] for s in stocks) / len(stocks)
            sid_list = " ".join(f"{s['sid']}{s['name']}" for s in stocks[:6])
            print(f"  {theme:<16}  {len(stocks):>4}支  {avg_rs:>+6.1f}%  {sid_list}")

    LIMIT_UP = 9.5   # 視為漲停的門檻

    # 可買入候選（今日漲幅 < 漲停，族群 >= cluster_min，RS 10-20% 甜蜜點優先）
    buyable = [c for c in candidates if c["day_chg"] < LIMIT_UP]
    buyable_sweet = [c for c in buyable if 10 <= c["rs"] <= 20]

    if buyable_sweet:
        print(f"\n  *** 可買入候選（RS 10-20%，未漲停）***")
        print(f"  {'代號':<6}{'名稱':<8}{'現價':>7}{'今日':>7}{'5日RS':>8}"
              f"{'量比':>7}{'族群':>5}  {'最強題材'}")
        print(f"  {'-'*76}")
        for c in sorted(buyable_sweet, key=lambda x: (-x["cluster"], -x["rs"]))[:15]:
            print(f"  {c['sid']:<6}{c['name']:<8}{c['price']:>7.2f}"
                  f"{c['day_chg']:>+6.1f}%{c['rs']:>+7.1f}%"
                  f"{c['vol_ratio']:>6.1f}x{c['cluster']:>5}支"
                  f"  {c['best_theme']}")

    # 全部明細（漲停標記 [鎖]）
    print(f"\n  --- 全部候選 ---")
    print(f"  {'代號':<6}{'名稱':<8}{'現價':>7}{'今日':>7}{'5日RS':>8}"
          f"{'量比':>7}{'族群':>5}  {'最強題材'}")
    print(f"  {'-'*76}")
    for c in candidates[:args.top_n]:
        locked = "[鎖]" if c["day_chg"] >= LIMIT_UP else "    "
        print(f"  {locked}{c['sid']:<6}{c['name']:<8}{c['price']:>7.2f}"
              f"{c['day_chg']:>+6.1f}%{c['rs']:>+7.1f}%"
              f"{c['vol_ratio']:>6.1f}x{c['cluster']:>5}支"
              f"  {c['best_theme']}")

    print(f"\n  共 {len(candidates)} 支候選，可買入 {len(buyable)} 支"
          f"（其中甜蜜點 RS 10-20%: {len(buyable_sweet)} 支）"
          f"\n  建議進場時間: 13:00-13:25 確認族群仍強後尾盤掛價")


# ── 主程式 ────────────────────────────────────────────────────────────────────

def main():
    args = parse_args()

    stock_ref, tsea_prev, tsea_5d_ret = load_history()
    stock_to_themes, theme_to_stocks  = load_themes()
    print(f"[主題] {len(theme_to_stocks)} 個主題")

    r = redis.Redis(
        host=os.getenv("REDIS_HOST", "localhost"),
        port=int(os.getenv("REDIS_PORT", 6379)),
        db=int(os.getenv("REDIS_DB", 0)),
        decode_responses=True,
    )
    try:
        r.ping()
        print("[Redis] 連線成功")
    except Exception as e:
        print(f"[Redis] 連線失敗: {e}"); return

    while True:
        try:
            live       = read_redis(r)
            candidates = scan(live, stock_ref, tsea_5d_ret,
                              stock_to_themes, theme_to_stocks, args)
            display(candidates, tsea_5d_ret, args)
        except Exception as e:
            print(f"[錯誤] {e}")

        if args.once:
            break
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
