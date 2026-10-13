import sys
sys.stdout.reconfigure(encoding="utf-8")
from influxdb_client import InfluxDBClient

client = InfluxDBClient(url="http://localhost:8086", token="mytoken123", org="stocks", timeout=30_000)
q = client.query_api()

# 查今天 3105 所有資料的第一筆和最後一筆時間
flux = '''
from(bucket: "intraday")
  |> range(start: 2026-05-15T00:00:00Z)
  |> filter(fn: (r) => r["_measurement"] == "stock")
  |> filter(fn: (r) => r["sid"] == "3105")
  |> filter(fn: (r) => r["_field"] == "cum_net_flow")
  |> sort(columns: ["_time"])
'''
tables = q.query(flux, org="stocks")
rows = [(r.get_time(), r.get_value()) for t in tables for r in t.records]
if rows:
    print(f"共 {len(rows)} 筆")
    print(f"第一筆: {rows[0][0]}  {rows[0][1]/1e4:+,.0f} 萬")
    print(f"最後筆: {rows[-1][0]}  {rows[-1][1]/1e4:+,.0f} 萬")
    # 找 spike
    for i, (ts, val) in enumerate(rows):
        if abs(val) > 500_000_0000:  # > 500億 就是 spike
            print(f"  spike @ {ts}  {val/1e4:+,.0f} 萬")
else:
    print("無資料")
client.close()
