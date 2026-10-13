# -*- coding: utf-8 -*-
"""診斷股期量比為何只有少數股票出現"""
import sys, json, os, configparser
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8")

CFG = Path(r"D:\Side_projects\stocks\stock_crawler\codes\tw_stock\tw_crawl\configs\cfg.ini")
cfg = configparser.ConfigParser()
cfg.read(CFG)
host = cfg.get("Account",          "host",     fallback="localhost")
user = cfg.get("Postgres_Account", "user",     fallback="postgres")
pwd  = cfg.get("Postgres_Account", "password", fallback="")

import psycopg2
conn = psycopg2.connect(dbname="postgres", user=user, password=pwd, host=host, port=5432)
cur  = conn.cursor()

# 1. fut_1min_table 有哪些 %F00 stock_id，近5日各有幾天
cur.execute("""
    SELECT stock_id,
           COUNT(DISTINCT DATE(tick_time AT TIME ZONE 'Asia/Taipei')) AS days,
           SUM(vol) AS total_vol
    FROM   fut_1min_table
    WHERE  tick_time >= CURRENT_DATE - 30
      AND  stock_id LIKE '%F00'
      AND  EXTRACT(HOUR FROM tick_time AT TIME ZONE 'Asia/Taipei') BETWEEN 8 AND 13
    GROUP  BY stock_id
    ORDER  BY days DESC, total_vol DESC
""")
rows = cur.fetchall()
print(f"\n=== fut_1min_table 近30日 %F00 資料（共 {len(rows)} 支）===")
for r in rows[:30]:
    print(f"  {r[0]:<10} {r[1]} 天  總量 {r[2]:,}")
if len(rows) > 30:
    print(f"  ... 共 {len(rows)} 支")

cur.close(); conn.close()

# 2. Redis taifex:stock_futures 的 symbol_id → 轉換後 Capital code
try:
    import redis
    r = redis.Redis(host="127.0.0.1", port=6379, db=0, decode_responses=True)
    fj = r.get("taifex:stock_futures")
    if fj:
        futures = json.loads(fj)
        print(f"\n=== taifex:stock_futures（共 {len(futures)} 筆）===")
        # 套用相同轉換邏輯
        def _fut_capital_code(symbol_id: str) -> str:
            base = symbol_id.split("-")[0]
            return base[:-2] + "00"

        for f in futures[:20]:
            sid    = f.get("spot_id", "")
            sym    = f.get("symbol_id", "")
            cap    = _fut_capital_code(sym) if sym else ""
            vol    = f.get("volume", 0)
            print(f"  spot={sid:<6} symbol={sym:<12} → capital={cap:<10} vol={vol}")
        if len(futures) > 20:
            print(f"  ... 共 {len(futures)} 筆")
    else:
        print("\n[Redis] taifex:stock_futures 無資料（taifex_fetcher 尚未寫入？）")
except Exception as e:
    print(f"\n[Redis] 連線失敗: {e}")
