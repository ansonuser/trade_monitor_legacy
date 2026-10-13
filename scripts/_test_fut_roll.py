# -*- coding: utf-8 -*-
"""Test rolling update: simulate cache from yesterday, check it updates to today."""
import sys, json, datetime, time
sys.path.insert(0, r"d:\Side_projects\stocks\trade_sys_v1")
sys.stdout.reconfigure(encoding="utf-8")

import redis as redis_lib
from monitor.morning_signal import MorningSignal, _FUT_VOL_CACHE_KEY, _FUT_VOL_LOOKBACK

r = redis_lib.Redis(host="127.0.0.1", port=6379, db=0, decode_responses=True)

# Read actual cache built by previous test
cj = r.get(_FUT_VOL_CACHE_KEY)
if not cj:
    print("ERROR: no cache in Redis — run _test_fut_cache.py first")
    sys.exit(1)

real_cache = json.loads(cj)
print(f"Real cache: date={real_cache['date']}  entries={len(real_cache['avg'])}")

# Simulate cache from 2 days ago (so rolling update triggers for yesterday's data)
two_days_ago = (datetime.date.today() - datetime.timedelta(days=2)).isoformat()
fake_cache = {"date": two_days_ago, "avg": real_cache["avg"]}
r.set(_FUT_VOL_CACHE_KEY, json.dumps(fake_cache))
print(f"Injected fake cache with date={two_days_ago} (2 days ago)")

# Now _load_fut_vol_avg should detect stale cache and run _roll_fut_vol_avg
print("\n=== Rolling update test ===")
t0 = time.time()
ms = MorningSignal(r)
ms._load_fut_vol_avg()
t1 = time.time()

today = datetime.date.today().isoformat()
updated = json.loads(r.get(_FUT_VOL_CACHE_KEY) or "{}")
print(f"  Completed in {t1-t0:.2f}s")
yesterday = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
print(f"  Cache date after update: {updated.get('date')} (expected {yesterday})")
print(f"  Entries: {len(ms._fut_vol_avg)}")

# Verify a few values changed (rolling update adds yesterday's volume)
sample_codes = list(real_cache["avg"].keys())[:3]
for c in sample_codes:
    old_v = real_cache["avg"].get(c, 0)
    new_v = ms._fut_vol_avg.get(c, 0)
    print(f"  {c}: old_avg={old_v:.0f}  new_avg={new_v:.0f}  delta={new_v-old_v:+.0f}")

print("\nDone. Restoring real cache...")
r.set(_FUT_VOL_CACHE_KEY, cj)
