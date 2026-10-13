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

# 近5日所有不重複的 stock_id，看實際格式
cur.execute("""
    SELECT DISTINCT stock_id, MAX(tick_time) as latest
    FROM   fut_1min_table
    WHERE  tick_time >= CURRENT_DATE - 5
    GROUP  BY stock_id
    ORDER  BY latest DESC
    LIMIT  30
""")
rows = cur.fetchall()
print(f"近5日 stock_id 樣本（共 {len(rows)} 支）:")
for r in rows:
    print(f"  {r[0]:<12} 最新: {str(r[1])[:16]}")

# 總共有幾種格式
cur.execute("""
    SELECT COUNT(DISTINCT stock_id) FROM fut_1min_table
    WHERE  tick_time >= CURRENT_DATE - 5
""")
print(f"\n近5日總共: {cur.fetchone()[0]} 支")

cur.close(); conn.close()
