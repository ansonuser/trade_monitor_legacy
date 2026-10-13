"""
盤後測試：模擬多個 15 分鐘切點，確認 InfluxDB 時序資料正確
用法: venv/Scripts/python.exe scripts/test_influx_timeseries.py [--rounds 5]

每次執行會從 Redis 讀取上次的 cum_flows，累加後寫入 InfluxDB，
模擬連續多輪掃描的效果，不影響盤中 scanner.py 邏輯。
"""
import asyncio, argparse, datetime, json, os, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from monitor.scanner import (
    fetch_stock_list, poll_all, calc_flow,
    aggregate_themes, load_themes,
    write_to_influx, _get_influx_writer, _get_redis_client,
)

INFLUX_INTERVAL = 15 * 60  # 每輪寫入間隔（秒），這裡直接每輪都寫


async def main(rounds: int):
    redis_cli = _get_redis_client()
    influx_writer, influx_org = _get_influx_writer()
    if not influx_writer:
        print("ERROR: 無法連線 InfluxDB")
        return

    # 從 Redis 恢復上次的 cum_flows
    cum_flows: dict[str, float] = {}
    if redis_cli:
        saved = redis_cli.get("tracker:cum_flows_test")
        if saved:
            cum_flows = json.loads(saved)
            print(f"從 Redis 恢復 {len(cum_flows)} 支股票的累計淨流")

    theme_map = load_themes()
    stocks = fetch_stock_list()
    ex_chs = [s["ex_ch"] for s in stocks]
    prev_snap: dict = {}

    for i in range(rounds):
        print(f"\n=== 第 {i+1}/{rounds} 輪 ===")
        raw = await poll_all(ex_chs)
        now_snap = {s["c"]: s for s in raw if s.get("c")}
        flows = [calc_flow(s, prev_snap.get(s["c"])) for s in raw if s.get("c")]

        for f in flows:
            sid = f["sid"]
            cum_flows[sid] = cum_flows.get(sid, 0.0) + f["net_flow"]
            f["cum_net_flow"] = cum_flows[sid]

        themes = aggregate_themes(flows, theme_map)
        ts = datetime.datetime.now(datetime.timezone.utc)

        write_to_influx(influx_writer, influx_org, flows, themes, ts)
        print(f"  寫入完成  ts={ts.strftime('%H:%M:%S UTC')}")

        # 存回 Redis 供下次繼續累加
        if redis_cli:
            redis_cli.set("tracker:cum_flows_test", json.dumps(cum_flows))

        prev_snap = now_snap
        if i < rounds - 1:
            print(f"  等待 {INFLUX_INTERVAL}s...")
            await asyncio.sleep(INFLUX_INTERVAL)

    print("\n完成。去 Grafana 查看時序走勢。")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--rounds", type=int, default=3, help="模擬幾個切點（預設3）")
    args = parser.parse_args()
    asyncio.run(main(args.rounds))
