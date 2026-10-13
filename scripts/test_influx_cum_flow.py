"""
測試 InfluxDB cum_net_flow 資料正確性

原理：
  net_flow     = 每次掃描週期的淨流增量（delta，本身正確）
  cum_net_flow = 掃描器累加的當日總計（若跨日未重置則膨脹）

  正確當日累計 = sum(net_flow, group by sid+date)
  若 stored_cum ≠ rebuilt_cum，代表有跨日累積 bug

用法：
  .venv/Scripts/python.exe scripts/test_influx_cum_flow.py
  .venv/Scripts/python.exe scripts/test_influx_cum_flow.py --sid 3105 2330
  .venv/Scripts/python.exe scripts/test_influx_cum_flow.py --days 7 --detail 3105
"""

import argparse, os, sys
from collections import defaultdict
from datetime import timedelta, timezone
from pathlib import Path

_env = Path(__file__).parent.parent / ".env"
if _env.exists():
    try:
        from dotenv import load_dotenv
        load_dotenv(_env)
    except ImportError:
        pass

try:
    from influxdb_client import InfluxDBClient
except ImportError:
    print("需要 influxdb-client：pip install influxdb-client")
    sys.exit(1)

URL    = os.getenv("INFLUX_URL",    "http://localhost:8086")
TOKEN  = os.getenv("INFLUX_TOKEN",  "mytoken123")
ORG    = os.getenv("INFLUX_ORG",    "stocks")
BUCKET = os.getenv("INFLUX_BUCKET", "intraday")
TZ_TW  = timezone(timedelta(hours=8))


def tw_date(ts) -> str:
    return ts.astimezone(TZ_TW).strftime("%Y-%m-%d")

def tw_time(ts) -> str:
    return ts.astimezone(TZ_TW).strftime("%H:%M")


def fetch_top30_sids(qapi, days: int) -> list[str]:
    flux = f"""
from(bucket: "{BUCKET}")
  |> range(start: -{days}d)
  |> filter(fn: (r) => r._measurement == "top30_stocks" and r._field == "cum_net_flow")
  |> group(columns: ["sid"])
  |> last()
  |> keep(columns: ["sid"])
"""
    sids = set()
    for tbl in qapi.query(flux, org=ORG):
        for rec in tbl.records:
            sid = rec.values.get("sid")
            if sid:
                sids.add(sid)
    return sorted(sids)


def fetch_flow_series(qapi, sids: list[str], days: int) -> dict:
    """
    回傳 {(sid, date): {"net": [(hh:mm, value), ...], "cum": [(hh:mm, value), ...]}}
    net  = net_flow per scan interval
    cum  = cum_net_flow stored value
    """
    sid_expr = " or ".join(f'r["sid"] == "{s}"' for s in sids)
    flux = f"""
from(bucket: "{BUCKET}")
  |> range(start: -{days}d)
  |> filter(fn: (r) => r._measurement == "stock")
  |> filter(fn: (r) => r._field == "net_flow" or r._field == "cum_net_flow")
  |> filter(fn: (r) => {sid_expr})
  |> group(columns: ["sid", "_field"])
  |> sort(columns: ["_time"])
"""
    # (sid, date) → {"net": [...], "cum": [...]}
    data: dict = defaultdict(lambda: {"net": [], "cum": []})

    for tbl in qapi.query(flux, org=ORG):
        for rec in tbl.records:
            sid   = rec.values.get("sid")
            field = rec.get_field()
            date  = tw_date(rec.get_time())
            t     = tw_time(rec.get_time())
            val   = rec.get_value()
            if sid is None or val is None:
                continue
            key = (sid, date)
            if field == "net_flow":
                data[key]["net"].append((t, val))
            elif field == "cum_net_flow":
                data[key]["cum"].append((t, val))

    return dict(data)


def analyse(data: dict) -> tuple[list, int]:
    print(f"\n{'股票':>6}  {'日期':>12}  {'重建累計(億)':>13}  {'儲存累計(億)':>13}  {'差異(億)':>12}  {'狀態'}")
    print("─" * 80)

    bugs = 0
    results = []

    for (sid, date), d in sorted(data.items()):
        net_vals = [v for _, v in d["net"]]
        cum_vals = [v for _, v in d["cum"]]
        if not net_vals or not cum_vals:
            continue

        rebuilt = sum(net_vals)   # 正確的當日累計（net_flow 本身不累跨日）
        stored  = cum_vals[-1]    # 儲存的最後一個 cum_net_flow
        diff    = stored - rebuilt

        # 容忍：5% 或 100萬元（掃描頻率不同造成的微小誤差）
        tol = max(abs(rebuilt) * 0.05, 1_000_000)
        is_ok = abs(diff) < tol
        status = "OK" if is_ok else "BUG"
        if not is_ok:
            bugs += 1

        results.append((sid, date, rebuilt, stored, diff, status))
        r_b = rebuilt / 1e8
        s_b = stored  / 1e8
        d_b = diff    / 1e8
        note = "  ← 跨日累積" if not is_ok else ""
        print(f"{sid:>6}  {date:>12}  {r_b:>+13.2f}  {s_b:>+13.2f}  {d_b:>+12.2f}  {status}{note}")

    print()
    return results, bugs


def print_timeseries(data: dict, sid: str):
    """印出單一股票完整時序，方便肉眼看跨日跳點"""
    rows = [(date, t, v_net, v_cum)
            for (s, date), d in sorted(data.items()) if s == sid
            for (t, v_net), (_, v_cum) in zip(d["net"], d["cum"])]
    if not rows:
        # net/cum 筆數可能不一致，分開印
        print(f"\n─── {sid} net_flow 時序 ───")
        prev = None
        for (s, date), d in sorted(data.items()):
            if s != sid:
                continue
            if prev and prev != date:
                print(f"  ──── 日期切換 {prev} → {date} ────")
            prev = date
            cum_by_time = dict(d["cum"])
            for t, v in d["net"]:
                c = cum_by_time.get(t)
                c_str = f"{c/1e4:>+.1f}" if c is not None else "       -"
                print(f"  {date}  {t}  net={v/1e4:>+8.1f}萬  cum={c_str}萬")
        return

    print(f"\n─── {sid} 時序明細 ───")
    print(f"  {'日期':>12}  {'時間':>5}  {'net(萬)':>10}  {'cum(萬)':>12}")
    prev = None
    for date, t, v_net, v_cum in rows:
        if prev and prev != date:
            print(f"  ──── 日期切換 {prev} → {date} ────")
        prev = date
        print(f"  {date:>12}  {t:>5}  {v_net/1e4:>+10.1f}  {v_cum/1e4:>+12.1f}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sid",  nargs="+", help="指定股票代號（不指定則用 top30）")
    ap.add_argument("--days", type=int, default=5, help="回溯天數（預設 5）")
    ap.add_argument("--detail", nargs="*", metavar="SID",
                    help="印出時序明細，不加代號則全部，加代號只印指定的")
    args = ap.parse_args()

    client = InfluxDBClient(url=URL, token=TOKEN, org=ORG)
    qapi   = client.query_api()

    if args.sid:
        sids = args.sid
        print(f"查詢指定股票：{sids}")
    else:
        print(f"從 top30_stocks 取近 {args.days} 天出現的股票...")
        sids = fetch_top30_sids(qapi, args.days)
        if not sids:
            print("top30_stocks 查不到資料，請用 --sid 指定，或先讓 scanner 跑一段時間")
            sys.exit(1)
        print(f"  找到 {len(sids)} 支：{sids}")

    print(f"查詢近 {args.days} 天 net_flow / cum_net_flow...")
    data = fetch_flow_series(qapi, sids, args.days)
    if not data:
        print("查無資料，請確認 InfluxDB bucket / measurement 名稱正確")
        sys.exit(1)

    results, bugs = analyse(data)

    if bugs:
        print(f"⚠  發現 {bugs} 筆跨日累積異常")
        print("   建議清除 Redis 舊資料後重啟：")
        print("     redis-cli DEL tracker:stocks tracker:themes tracker:timestamp")
    else:
        print("✓  資料正確，cum_net_flow 與 sum(net_flow) 吻合")

    if args.detail is not None:
        detail_sids = args.detail if args.detail else sids
        for s in detail_sids:
            print_timeseries(data, s)


if __name__ == "__main__":
    main()
