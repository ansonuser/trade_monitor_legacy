# -*- coding: utf-8 -*-
"""
早盤訊號獨立執行緒 — 從 Redis 讀取 tracker:stocks/themes，計算早盤訊號後寫回 tracker:morning

與 scanner 完全解耦，可以獨立啟動/停止。

執行方式：
    .venv/Scripts/python.exe monitor/morning_runner.py
    .venv/Scripts/python.exe monitor/morning_runner.py --interval 30
    .venv/Scripts/python.exe monitor/morning_runner.py --redis-db 1   # 搭配 mock_scanner 測試
"""

import argparse, datetime, json, logging, sys, time, os
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from monitor.morning_signal import MorningSignal

log = logging.getLogger("morning_runner")


def _setup_logger():
    log_dir = Path(__file__).resolve().parent.parent / "logs"
    log_dir.mkdir(exist_ok=True)
    log_path = log_dir / f"morning_{datetime.date.today():%Y%m%d}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(message)s",
        datefmt="%H:%M:%S",
        handlers=[
            logging.FileHandler(log_path, encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )


def _get_redis(host, port, db):
    import redis
    r = redis.Redis(host=host, port=port, db=db, decode_responses=True,
                    socket_connect_timeout=3)
    r.ping()
    return r


def _print_snapshot(result: dict):
    b = result.get("breadth", {})
    ts = result.get("ts", "")[:19]
    print(f"\n[{ts}]")
    print(f"  多點開花 {b.get('positive_pct',0)*100:.1f}%  "
          f"集中度 {b.get('concentration',0)*100:.1f}%"
          + (f"  ⚠ {b['alert']}" if b.get("alert") else ""))

    vr = result.get("vol_ratio", [])[:5]
    if vr:
        print(f"  量比 Top5: " +
              "  ".join(f"{r['sid']}={r['vol_ratio']:.1f}x" for r in vr))

    lag = result.get("rs_laggards", [])[:5]
    if lag:
        print(f"  RS落後: " +
              "  ".join(f"{r['sid']}(#{r['rs_rank']} 開{r['open_chg_pct']:+.1f}%)" for r in lag))

    inst = result.get("inst_leaders", [])[:3]
    if inst:
        print(f"  法人昨買: " +
              "  ".join(f"{r['sid']} 今{r['day_chg_pct']:+.1f}%" for r in inst))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--interval",   type=int, default=30,        help="更新間隔秒（預設 30）")
    ap.add_argument("--redis-host", default="127.0.0.1")
    ap.add_argument("--redis-port", type=int, default=6379)
    ap.add_argument("--redis-db",   type=int, default=0)
    ap.add_argument("--once",       action="store_true",         help="執行一次後退出")
    a = ap.parse_args()

    _setup_logger()

    try:
        r = _get_redis(a.redis_host, a.redis_port, a.redis_db)
        log.info(f"[morning_runner] Redis={a.redis_host}:{a.redis_port} DB={a.redis_db}  interval={a.interval}s")
    except Exception as e:
        log.error(f"Redis 連線失敗: {e}")
        sys.exit(1)

    ms = MorningSignal(r)

    while True:
        stocks_json = r.get("tracker:stocks")
        themes_json = r.get("tracker:themes")

        if not stocks_json or not themes_json:
            log.info("等待 scanner 資料...")
        else:
            try:
                stocks = json.loads(stocks_json)
                themes = json.loads(themes_json)
                result = ms.update(stocks, themes)
                _print_snapshot(result)
            except Exception as e:
                log.warning(f"計算失敗: {e}")

        if a.once:
            break
        time.sleep(a.interval)


if __name__ == "__main__":
    main()
