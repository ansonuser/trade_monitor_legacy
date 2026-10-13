# -*- coding: utf-8 -*-
"""Test _query_fut_vol_avg two-step query performance and correctness."""
import configparser, psycopg2, sys, time
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8")

cfg = configparser.ConfigParser()
cfg.read(r"D:\Side_projects\stocks\stock_crawler\codes\tw_stock\tw_crawl\configs\cfg.ini")
conn = psycopg2.connect(
    dbname="postgres",
    user=cfg.get("Postgres_Account", "user"),
    password=cfg.get("Postgres_Account", "password"),
    host=cfg.get("Account", "host"), port=5432,
    connect_timeout=10,
)
with conn.cursor() as _c:
    _c.execute("SET statement_timeout = '30s'")
conn.commit()

cur = conn.cursor()
lookback_days = 20

print("=== Step 1: fetch distinct %F00 stock_ids (last 3 days) ===")
t0 = time.time()
cur.execute("""
    SELECT DISTINCT stock_id
    FROM fut_1min_table
    WHERE tick_time >= CURRENT_DATE - 3
      AND stock_id LIKE '%F00'
""")
fut_ids = [row[0] for row in cur.fetchall()]
t1 = time.time()
print(f"  Got {len(fut_ids)} IDs in {t1-t0:.2f}s")
print(f"  Sample: {fut_ids[:5]}")

if not fut_ids:
    print("  No IDs found — cannot proceed")
    sys.exit(1)

print("\n=== Step 2: per-stock index range scan for 20-day avg ===")
t2 = time.time()
cur.execute("""
    SELECT stock_id, AVG(daily_vol)::float
    FROM (
        SELECT stock_id,
               DATE(tick_time) AS d,
               SUM(vol) AS daily_vol
        FROM   fut_1min_table
        WHERE  stock_id = ANY(%s)
          AND  tick_time >= CURRENT_DATE - %s
          AND  tick_time <  CURRENT_DATE
        GROUP  BY stock_id, d
        HAVING SUM(vol) > 0
    ) sub
    GROUP  BY stock_id
    HAVING COUNT(*) >= 10
""", (fut_ids, lookback_days + 7))
rows = cur.fetchall()
t3 = time.time()
result = {row[0]: row[1] for row in rows}
print(f"  Got {len(result)} stocks with avg vol in {t3-t2:.2f}s")
print(f"  Total time: {t3-t0:.2f}s")
print(f"  Sample:")
for k, v in list(result.items())[:5]:
    print(f"    {k}: avg={v:.0f}")

cur.close(); conn.close()
