# -*- coding: utf-8 -*-
"""
模擬掃描器服務：持續生成假市場資料，寫入 Redis DB=1，供 GUI --test 模式使用。

啟動方式：
    .venv/Scripts/python.exe tests/mock_scanner.py
    .venv/Scripts/python.exe tests/mock_scanner.py --interval 5
    .venv/Scripts/python.exe tests/mock_scanner.py --scenario restart

搭配 GUI：
    .venv/Scripts/python.exe monitor/gui.py --test --interval 5

情境（--scenario）：
    normal   預設，正常盤中模擬
    restart  每 10 掃後清空 Redis 重啟，驗證 is_init_scan 不 spike
"""
import sys, json, time, math, random, argparse, datetime, os
sys.stdout.reconfigure(encoding="utf-8")
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from monitor.scanner import calc_flow, aggregate_sectors, aggregate_themes

# ─── 假股票清單 ──────────────────────────────────────────────────────────────

MOCK_STOCKS = [
    {"sid": "2330", "name": "台積電", "industry": "24", "prev_close": 1000.0, "weight": 0.35},
    {"sid": "2454", "name": "聯發科", "industry": "24", "prev_close": 800.0,  "weight": 0.10},
    {"sid": "3105", "name": "穩懋",   "industry": "24", "prev_close": 483.0,  "weight": 0.01},
    {"sid": "2317", "name": "鴻海",   "industry": "25", "prev_close": 120.0,  "weight": 0.05},
    {"sid": "2308", "name": "台達電", "industry": "13", "prev_close": 300.0,  "weight": 0.04},
    {"sid": "2412", "name": "中華電", "industry": "27", "prev_close": 130.0,  "weight": 0.06},
    {"sid": "2881", "name": "富邦金", "industry": "17", "prev_close": 90.0,   "weight": 0.05},
    {"sid": "1301", "name": "台塑",   "industry": "03", "prev_close": 80.0,   "weight": 0.03},
    {"sid": "2882", "name": "國泰金", "industry": "17", "prev_close": 75.0,   "weight": 0.04},
    {"sid": "2886", "name": "兆豐金", "industry": "17", "prev_close": 45.0,   "weight": 0.03},
    {"sid": "2303", "name": "聯電",   "industry": "24", "prev_close": 55.0,   "weight": 0.03},
    {"sid": "2002", "name": "中鋼",   "industry": "10", "prev_close": 30.0,   "weight": 0.02},
    {"sid": "2382", "name": "廣達",   "industry": "25", "prev_close": 220.0,  "weight": 0.03},
    {"sid": "2891", "name": "中信金", "industry": "17", "prev_close": 40.0,   "weight": 0.03},
    {"sid": "3008", "name": "大立光", "industry": "26", "prev_close": 2000.0, "weight": 0.02},
    {"sid": "2345", "name": "智邦",   "industry": "27", "prev_close": 480.0,  "weight": 0.01},
    {"sid": "2357", "name": "華碩",   "industry": "25", "prev_close": 380.0,  "weight": 0.02},
    {"sid": "2615", "name": "萬海",   "industry": "15", "prev_close": 60.0,   "weight": 0.01},
    {"sid": "2609", "name": "陽明",   "industry": "15", "prev_close": 55.0,   "weight": 0.01},
    {"sid": "6505", "name": "台塑化", "industry": "03", "prev_close": 90.0,   "weight": 0.03},
]

MOCK_THEMES = {
    "AI伺服器": ["2330", "2454", "2382"],
    "半導體":   ["2330", "2454", "3105", "2303"],
    "航運":     ["2615", "2609"],
    "金融":     ["2881", "2882", "2886", "2891", "2891"],
    "石化":     ["1301", "6505"],
    "網通":     ["2412", "2345"],
}


# ─── 市場狀態模擬 ─────────────────────────────────────────────────────────────

class MockMarket:
    def __init__(self):
        self.scan_count = 0
        # 每支股票的狀態
        self.states = {}
        for s in MOCK_STOCKS:
            sid = s["sid"]
            # 開盤價：prev_close ±1%
            open_p = s["prev_close"] * (1 + random.uniform(-0.01, 0.01))
            self.states[sid] = {
                "sid":        sid,
                "name":       s["name"],
                "industry":   s["industry"],
                "prev_close": s["prev_close"],
                "open":       round(open_p, 1),
                "price":      round(open_p, 1),
                "high":       round(open_p, 1),
                "low":        round(open_p, 1),
                "volume":     0,
                # 個股的「趨勢」：決定是漲還是跌（隨機，每次重置時重新設）
                "trend":      random.choice([-1, -1, 0, 1]),
            }

    def tick(self, interval_secs: int) -> list[dict]:
        """產生一次掃描的 tick 資料（模擬 MIS API 回傳格式）"""
        self.scan_count += 1
        ticks = []
        time_fraction = min(self.scan_count / 30, 1.0)   # 模擬從開盤到尾盤的成交量增長

        for s in MOCK_STOCKS:
            sid = s["sid"]
            st  = self.states[sid]

            # 這次掃描新增的成交量（張）
            # 目標：每支股票日成交 ~10,000 張，一天 ~3240 次掃（5 秒 interval）
            # 每掃約 3 張基準，上限 30 張避免低價股爆炸
            base_vol = 3
            new_vol  = int(random.randint(base_vol, base_vol * 3) * time_fraction + 1)
            st["volume"] += new_vol

            # 價格變動：趨勢 + 隨機雜訊
            tick_size = max(0.1, round(st["prev_close"] * 0.001, 1))
            noise     = random.uniform(-2, 2) * tick_size
            trend_mv  = st["trend"] * tick_size * random.uniform(0, 1.5)
            new_price = st["price"] + noise + trend_mv

            # 漲跌停限制 (±10%)
            limit_up   = st["prev_close"] * 1.10
            limit_down = st["prev_close"] * 0.90
            new_price  = max(limit_down, min(limit_up, new_price))
            new_price  = round(new_price, 1)

            st["price"] = new_price
            st["high"]  = max(st["high"],  new_price)
            st["low"]   = min(st["low"],   new_price)

            ticks.append({
                "c": sid,
                "n": s["name"],
                "i": s["industry"],
                "v": str(st["volume"]),
                "z": str(new_price),
                "y": str(st["prev_close"]),
                "o": str(round(st["open"], 1)),
                "h": str(st["high"]),
                "l": str(st["low"]),
            })
        return ticks

    def reset(self):
        """重置市場狀態（模擬重啟）"""
        self.__init__()


# ─── Redis 寫入 ──────────────────────────────────────────────────────────────

def write_redis(r, flows, sectors, themes):
    r.set("tracker:stocks", json.dumps([
        {k: (round(v, 2) if k == "price" else v) for k, v in f.items() if v is not None}
        for f in flows
    ], ensure_ascii=False))
    r.set("tracker:sectors", json.dumps([
        {k: (round(v, 4) if isinstance(v, float) else v) for k, v in s.items()}
        for s in sectors
    ], ensure_ascii=False))
    r.set("tracker:themes", json.dumps([
        {k: (round(v, 4) if isinstance(v, float) else v) for k, v in t.items()}
        for t in themes
    ], ensure_ascii=False))
    r.set("tracker:timestamp", datetime.datetime.now().isoformat())


def write_snap(r, now_snap):
    snap_min = {sid: {"v": s.get("v", "0"), "z": s.get("z", "")}
                for sid, s in now_snap.items() if s.get("z")}
    r.set("tracker:snap", json.dumps(snap_min, ensure_ascii=False))


# ─── 主迴圈 ──────────────────────────────────────────────────────────────────

def run(interval: int, scenario: str, redis_host: str, redis_port: int, redis_db: int):
    import redis as redis_lib

    r = redis_lib.Redis(host=redis_host, port=redis_port, db=redis_db,
                        decode_responses=True)
    try:
        r.ping()
    except Exception as e:
        print(f"[錯誤] Redis 連線失敗: {e}")
        sys.exit(1)

    print(f"[mock_scanner] Redis={redis_host}:{redis_port} DB={redis_db}")
    print(f"               interval={interval}s  scenario={scenario}")
    print(f"               股票={len(MOCK_STOCKS)} 支  主題={len(MOCK_THEMES)} 個")
    print("               Ctrl+C 停止\n")

    # 建立股票→主題的反查表（sid → [theme, ...]）
    from collections import defaultdict
    theme_map: dict[str, list[str]] = defaultdict(list)
    for theme, sids in MOCK_THEMES.items():
        for sid in sids:
            theme_map[sid].append(theme)
    weight_map = {s["sid"]: s["weight"] for s in MOCK_STOCKS}

    market    = MockMarket()
    prev_snap: dict = {}
    cum_flows: dict = {}
    cum_theme_flows: dict = {}
    is_init_scan = True   # 模擬 scanner 啟動
    scan_num = 0

    while True:
        scan_num += 1

        # scenario=restart：每 10 掃清空 Redis 模擬重啟
        if scenario == "restart" and scan_num % 10 == 1 and scan_num > 1:
            print(f"\n[scenario=restart] 掃 {scan_num}：清空 Redis，模擬重啟...\n")
            r.delete("tracker:stocks", "tracker:themes", "tracker:sectors",
                     "tracker:timestamp", "tracker:snap")
            prev_snap = {}
            cum_flows = {}
            cum_theme_flows = {}
            is_init_scan = True
            market.reset()

        ticks    = market.tick(interval)
        now_snap = {t["c"]: t for t in ticks}
        flows    = [calc_flow(t, prev_snap.get(t["c"])) for t in ticks]

        if is_init_scan:
            for f in flows:
                f["delta_vol"] = f["flow_in"] = f["flow_out"] = f["net_flow"] = 0
            is_init_scan = False
            print(f"[掃 {scan_num:3d}] 初始掃描，流量歸零（基準點）")
        else:
            for f in flows:
                sid = f["sid"]
                cum_flows[sid] = cum_flows.get(sid, 0.0) + f["net_flow"]
                f["cum_net_flow"] = cum_flows[sid]
                f["index_contrib"] = f.get("day_chg_pct", 0) * weight_map.get(sid, 0.0) / 100

        sectors = aggregate_sectors(flows)
        themes  = aggregate_themes(flows, dict(theme_map))
        for t in themes:
            name = t["theme"]
            cum_theme_flows[name] = cum_theme_flows.get(name, 0.0) + t["net_flow"]
            t["cum_net_flow"] = cum_theme_flows[name]

        write_redis(r, flows, sectors, themes)
        prev_snap = now_snap
        write_snap(r, now_snap)

        # 印出快照（3105 穩懋 + 整體統計）
        top = sorted(flows, key=lambda f: abs(f.get("cum_net_flow", 0)), reverse=True)
        s3105 = next((f for f in flows if f["sid"] == "3105"), None)
        total_net = sum(f.get("net_flow", 0) for f in flows)
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        print(f"[掃 {scan_num:3d}] {ts}  "
              f"全市場淨流 {total_net/1e4:+,.0f}萬  "
              f"3105={s3105['price'] if s3105 else '?'} "
              f"cum={s3105.get('cum_net_flow', 0)/1e4:+,.1f}萬  "
              f"Top: {top[0]['sid']}={top[0].get('cum_net_flow',0)/1e4:+,.0f}萬")

        time.sleep(interval)


# ─── CLI ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--interval",   type=int, default=5,        help="掃描間隔秒（預設 5）")
    ap.add_argument("--scenario",   default="normal",
                    choices=["normal", "restart"],              help="測試情境")
    ap.add_argument("--redis-host", default="127.0.0.1")
    ap.add_argument("--redis-port", type=int, default=6379)
    ap.add_argument("--redis-db",   type=int, default=1,        help="Redis DB（預設 1，測試隔離）")
    a = ap.parse_args()
    run(a.interval, a.scenario, a.redis_host, a.redis_port, a.redis_db)
