# -*- coding: utf-8 -*-
"""Test the fut_vol_avg cache system end-to-end."""
import sys, time
sys.path.insert(0, r"d:\Side_projects\stocks\trade_sys_v1")
sys.stdout.reconfigure(encoding="utf-8")

import redis as redis_lib
from monitor.morning_signal import MorningSignal, _FUT_VOL_CACHE_KEY

r = redis_lib.Redis(host="127.0.0.1", port=6379, db=0, decode_responses=True)

# --- Test 1: cold start (delete cache first) ---
print("=== Test 1: cold start (no cache) ===")
r.delete(_FUT_VOL_CACHE_KEY)
ms = MorningSignal(r)
ms._load_fut_vol_avg()
print(f"  _fut_vol_avg immediately after cold start: {len(ms._fut_vol_avg)} entries (expected 0)")
print("  Background rebuild launched. Waiting 180s for it to finish...")

# Poll every 10s
for i in range(18):
    time.sleep(10)
    n = len(ms._fut_vol_avg)
    cached = r.get(_FUT_VOL_CACHE_KEY)
    print(f"  [{i*10+10}s] _fut_vol_avg={n}  cache_in_redis={'yes' if cached else 'no'}")
    if n > 0:
        break

print(f"  Final: {len(ms._fut_vol_avg)} entries in _fut_vol_avg")

# --- Test 2: cache hit (warm restart) ---
print("\n=== Test 2: warm restart (cache exists) ===")
t0 = time.time()
ms2 = MorningSignal(r)
ms2._load_fut_vol_avg()
t1 = time.time()
print(f"  Loaded {len(ms2._fut_vol_avg)} entries in {t1-t0:.2f}s (expected <1s)")

# Show sample
for k, v in list(ms2._fut_vol_avg.items())[:5]:
    print(f"  {k}: avg={v:.0f}")
