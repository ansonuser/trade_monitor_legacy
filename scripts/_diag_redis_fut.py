# -*- coding: utf-8 -*-
import redis, json, sys
sys.stdout.reconfigure(encoding="utf-8")

r = redis.Redis(host="127.0.0.1", port=6379, db=0, decode_responses=True)
data = r.get("taifex:stock_futures")
if not data:
    print("taifex:stock_futures is empty")
    sys.exit(0)

items = json.loads(data)
print(f"Total futures: {len(items)}")
for it in items[:10]:
    sid   = it.get("spot_id", "")
    sym   = it.get("symbol_id", "")
    name  = it.get("name", "")
    vol   = it.get("volume", 0)
    # Simulate _fut_capital_code
    if sym:
        base = sym.split("-")[0]
        cap  = base[:-2] + "00"
    else:
        cap = ""
    print(f"  spot={sid:<6}  symbol={sym:<12}  capital={cap:<8}  vol={vol}  {name}")
