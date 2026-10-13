# -*- coding: utf-8 -*-
import configparser, psycopg2, sys
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8")

cfg = configparser.ConfigParser()
cfg.read(r"D:\Side_projects\stocks\stock_crawler\codes\tw_stock\tw_crawl\configs\cfg.ini")
conn = psycopg2.connect(dbname="postgres",
    user=cfg.get("Postgres_Account", "user"),
    password=cfg.get("Postgres_Account", "password"),
    host=cfg.get("Account", "host"), port=5432)
cur = conn.cursor()

print("=== 近1日 stock_id 樣本 ===")
cur.execute("""
    SELECT DISTINCT stock_id, MAX(tick_time) as latest
    FROM fut_1min_table
    WHERE tick_time >= CURRENT_DATE - 1
    GROUP BY stock_id
    ORDER BY latest DESC
    LIMIT 20
""")
for r in cur.fetchall():
    print(f"  {r[0]:<16} latest: {str(r[1])[:16]}")

print("\n=== LIKE '%F00' 今日有幾支 ===")
cur.execute("""
    SELECT COUNT(DISTINCT stock_id)
    FROM fut_1min_table
    WHERE tick_time >= CURRENT_DATE - 1
      AND stock_id LIKE '%F00'
""")
print(f"  %F00: {cur.fetchone()[0]} 支")

print("\n=== LIKE '%00' 今日有幾支（各結尾） ===")
cur.execute("""
    SELECT RIGHT(stock_id, 3) as suffix, COUNT(DISTINCT stock_id)
    FROM fut_1min_table
    WHERE tick_time >= CURRENT_DATE - 1
    GROUP BY suffix
    ORDER BY COUNT(DISTINCT stock_id) DESC
    LIMIT 10
""")
for r in cur.fetchall():
    print(f"  suffix={r[0]}  count={r[1]}")

cur.close(); conn.close()
