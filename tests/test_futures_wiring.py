# -*- coding: utf-8 -*-
import sys, json
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))

import datetime, fakeredis
from monitor.morning_signal import MorningSignal, _calc_vol_ratio

TODAY = datetime.date.today().isoformat()

r = fakeredis.FakeRedis(decode_responses=True)

futures = [
    {"spot_id": "2330", "price": 905.0, "diff_pct": 0.5, "volume": 1200},
    {"spot_id": "2317", "price": 103.5, "diff_pct": -0.3, "volume": 450},
]
r.set("taifex:stock_futures", json.dumps(futures))

stocks = [
    {"sid": "2330", "name": "TSMC",    "price": 905.0,  "net_flow": 5000, "volume_total": 8000, "open_chg_pct": 0.3,  "day_chg_pct": 0.5,  "cum_net_flow": 5000},
    {"sid": "2317", "name": "Foxconn", "price": 103.5,  "net_flow": -200, "volume_total": 3000, "open_chg_pct": -0.2, "day_chg_pct": -0.3, "cum_net_flow": -200},
    {"sid": "2454", "name": "MTK",     "price": 1210.0, "net_flow": 1000, "volume_total": 500,  "open_chg_pct": 0.1,  "day_chg_pct": 0.2,  "cum_net_flow": 1000},
]
r.set("tracker:stocks", json.dumps(stocks))
r.set("tracker:themes", json.dumps([]))

vol_avg = {"2330": 10000, "2317": 5000, "2454": 800}

# Test 1: _calc_vol_ratio with futures_map
print("=== Test 1: _calc_vol_ratio with futures_map ===")
futures_map = {"2330": {"spot_id": "2330", "price": 905.0, "diff_pct": 0.5, "volume": 1200}}
rows = _calc_vol_ratio(stocks, vol_avg, futures_map)
for row in rows:
    print(" ", row)

assert any("fut_vol" in r for r in rows), "2330 should have fut_vol"
assert all("fut_vol" not in r for r in rows if r["sid"] != "2330"), "others should not have fut_vol"
print("PASS")

# Test 2: MorningSignal.update() reads taifex:stock_futures from Redis
print()
print("=== Test 2: MorningSignal.update() futures wiring via Redis ===")
ms = MorningSignal(r)
ms._vol_avg   = vol_avg
ms._rs_data   = {}
ms._inst_data = {}
ms._init_date = TODAY  # skip _load_context (today == today → no reload)

result = ms.update(stocks, [])
for row in result["vol_ratio"]:
    has_fut = "fut_vol" in row
    line = f"  {row['sid']} vol_ratio={row['vol_ratio']}x  futures={has_fut}"
    if has_fut:
        line += f"  fut_vol={row['fut_vol']} fut_diff={row['fut_diff_pct']}%"
    print(line)

# 2330 and 2317 both in Redis taifex data
has_2330 = next((r for r in result["vol_ratio"] if r["sid"] == "2330"), None)
has_2317 = next((r for r in result["vol_ratio"] if r["sid"] == "2317"), None)
assert has_2330 and "fut_vol" in has_2330, "2330 should have fut_vol from Redis"
assert has_2317 and "fut_vol" in has_2317, "2317 should have fut_vol from Redis"
assert has_2330["fut_vol"] == 1200
assert has_2330["fut_diff_pct"] == 0.5
print("PASS")

# Test 3: no taifex key in Redis → futures_map empty → no fut_* fields
print()
print("=== Test 3: no taifex:stock_futures in Redis ===")
r2 = fakeredis.FakeRedis(decode_responses=True)
ms2 = MorningSignal(r2)
ms2._vol_avg   = vol_avg
ms2._rs_data   = {}
ms2._inst_data = {}
ms2._init_date = "2099-01-01"
r2.set("tracker:stocks", json.dumps(stocks))
r2.set("tracker:themes", json.dumps([]))
result2 = ms2.update(stocks, [])
assert all("fut_vol" not in row for row in result2["vol_ratio"]), "no fut_vol when Redis key absent"
print("PASS — no fut_* fields when taifex key absent")

print()
print("ALL TESTS PASSED")
