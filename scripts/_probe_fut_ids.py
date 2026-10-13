# -*- coding: utf-8 -*-
"""查 fut_1min_table 的 stock_id 格式"""
import sys, configparser
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8")

CFG = Path(r"D:\Side_projects\stocks\stock_crawler\codes\tw_stock\tw_crawl\configs\cfg.ini")
cfg = configparser.ConfigParser()
cfg.read(CFG)
host = cfg.get("Account",          "host",     fallback="localhost")
user = cfg.get("Postgres_Account", "user",     fallback="postgres")
pwd  = cfg.get("Postgres_Account", "password", fallback="")

import psycopg2
conn = psycopg2.connect(dbname="postgres", user=user, password=pwd,
                        host=host, port=5432, connect_timeout=10)
cur = conn.cursor()

# 看近期有哪些 stock_id 格式
cur.execute("""
    SELECT DISTINCT stock_id
    FROM   fut_1min_table
    WHERE  tick_time >= NOW() - INTERVAL '5 days'
    ORDER  BY stock_id
    LIMIT  30
""")
rows = cur.fetchall()
print(f"近5日 stock_id 樣本 ({len(rows)} 筆):")
for r in rows:
    print(f"  {r[0]}")

cur.close(); conn.close()
