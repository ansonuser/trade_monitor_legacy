# -*- coding: utf-8 -*-
"""
端對端 pipeline 整合測試：模擬市場 tick → scanner 計算 → Redis 寫入 → GUI 讀取

不需要真實市場、不需要 InfluxDB，使用 fakeredis 模擬 Redis。

執行：
    .venv/Scripts/python.exe -m pytest tests/test_pipeline.py -v
    .venv/Scripts/python.exe tests/test_pipeline.py
"""
import sys, json
sys.stdout.reconfigure(encoding="utf-8")
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import fakeredis
from monitor.scanner import calc_flow, aggregate_sectors, aggregate_themes, load_themes, load_weights
from monitor.gui import _fetch_from_redis


# ─── 假資料工廠 ──────────────────────────────────────────────────────────────

def make_tick(sid, vol, price, prev_close, open_price=None, name="test", industry="24"):
    return {
        "c": sid, "n": name, "i": industry,
        "v": str(vol), "z": str(price), "y": str(prev_close),
        "o": str(open_price or prev_close),
        "h": str(max(price, open_price or prev_close)),
        "l": str(min(price, open_price or prev_close)),
    }


def scanner_write_redis(redis_cli, flows, themes):
    """模擬 scanner 寫 Redis（與 scanner.py 相同格式）"""
    import datetime
    redis_cli.set("tracker:stocks", json.dumps([
        {k: (round(v, 2) if k == "price" else v) for k, v in s.items() if v is not None}
        for s in flows
    ], ensure_ascii=False))
    redis_cli.set("tracker:themes", json.dumps([
        {k: (round(v, 4) if isinstance(v, float) else v) for k, v in t.items()}
        for t in themes
    ], ensure_ascii=False))
    redis_cli.set("tracker:timestamp", datetime.datetime.now().isoformat())


def run_scan_cycle(ticks, prev_snap, cum_flows, cum_theme_flows, theme_map,
                   weight_map, is_init_scan=False):
    """執行一次掃描週期，回傳 (flows, sectors, themes, new_prev_snap)"""
    flows = [calc_flow(tick, prev_snap.get(tick["c"])) for tick in ticks]

    if is_init_scan:
        for f in flows:
            f["delta_vol"] = f["flow_in"] = f["flow_out"] = f["net_flow"] = 0

    for f in flows:
        sid = f["sid"]
        cum_flows[sid] = cum_flows.get(sid, 0.0) + f["net_flow"]
        f["cum_net_flow"] = cum_flows[sid]
        w = weight_map.get(sid, 0.0)
        f["index_contrib"] = f.get("day_chg_pct", 0) * w / 100

    sectors = aggregate_sectors(flows)

    themes_list = aggregate_themes(flows, theme_map)
    for t in themes_list:
        name = t["theme"]
        cum_theme_flows[name] = cum_theme_flows.get(name, 0.0) + t["net_flow"]
        t["cum_net_flow"] = cum_theme_flows[name]

    new_prev_snap = {tick["c"]: tick for tick in ticks}
    return flows, sectors, themes_list, new_prev_snap


# ─── 測試 ──────────────────────────────────────────────────────────────────────

def test_normal_pipeline_3_scans():
    """
    正常情境：3 次掃描，每次增量流量正確，GUI 能讀到正確 cum_net_flow。
    股票 3105：開盤 480，每次跌 1 元，每次新增 ~100 張成交量
    """
    r = fakeredis.FakeRedis(decode_responses=True)
    theme_map  = {"3105": ["半導體"]}
    weight_map = {"3105": 0.02}

    ticks_data = [
        make_tick("3105", vol=19800, price=460, prev_close=483, open_price=480),  # 掃 0 (baseline)
        make_tick("3105", vol=19927, price=457, prev_close=483, open_price=480),  # 掃 1
        make_tick("3105", vol=20050, price=456, prev_close=483, open_price=480),  # 掃 2
    ]

    prev_snap = {ticks_data[0]["c"]: ticks_data[0]}   # 有 baseline，非空啟動
    cum_flows: dict = {}
    cum_theme_flows: dict = {}

    for i, tick in enumerate(ticks_data[1:], start=1):
        flows, sectors, themes, prev_snap = run_scan_cycle(
            [tick], prev_snap, cum_flows, cum_theme_flows, theme_map, weight_map
        )
        scanner_write_redis(r, flows, themes)
        stocks_gui, themes_gui = _fetch_from_redis(r)

        assert stocks_gui is not None, f"掃 {i}: GUI 讀不到資料"
        row = next(s for s in stocks_gui if s["sid"] == "3105")

        print(f"  掃 {i}: delta={flows[0]['delta_vol']:.0f}  "
              f"net={flows[0]['net_flow']/1e4:+.1f}萬  "
              f"cum={row['cum_net_flow']/1e4:+,.1f}萬")

        assert row["cum_net_flow"] == cum_flows["3105"], \
            f"掃{i}: GUI cum_net_flow 與計算值不符"

    print("  ✓ 正常三掃 pipeline 通過")


def test_restart_spike_fix_full_pipeline():
    """
    模擬盤中清空 Redis + 重啟 scanner：
    - 第一掃（is_init_scan=True）：流量應歸零，GUI 看到 cum=0
    - 第二掃：只計算增量，無 spike
    """
    r = fakeredis.FakeRedis(decode_responses=True)
    theme_map  = {"3105": ["半導體"]}
    weight_map = {"3105": 0.02}

    # 模擬盤中狀態：3105 已成交 19927 張，現價 457，開 480
    tick_current = make_tick("3105", vol=19927, price=457, prev_close=483, open_price=480)
    tick_next    = make_tick("3105", vol=20050, price=456, prev_close=483, open_price=480)

    # ── 重啟：Redis 已清空，prev_snap 是空的 ──
    prev_snap = {}
    cum_flows: dict = {}
    cum_theme_flows: dict = {}
    is_init_scan = not prev_snap   # True

    # 掃 1（init）
    flows1, sectors1, themes1, prev_snap = run_scan_cycle(
        [tick_current], prev_snap, cum_flows, cum_theme_flows,
        theme_map, weight_map, is_init_scan=is_init_scan
    )
    is_init_scan = False
    scanner_write_redis(r, flows1, themes1)

    stocks_gui, _ = _fetch_from_redis(r)
    row1 = next(s for s in stocks_gui if s["sid"] == "3105")
    print(f"\n  掃 1 (init): delta={flows1[0]['delta_vol']}  cum={row1['cum_net_flow']/1e4:+,.1f}萬")

    assert flows1[0]["delta_vol"] == 0, "init_scan delta_vol 應為 0"
    assert row1["cum_net_flow"] == 0.0, f"init_scan GUI cum 應為 0，得到 {row1['cum_net_flow']}"

    # 掃 2（正常）
    flows2, sectors2, themes2, prev_snap = run_scan_cycle(
        [tick_next], prev_snap, cum_flows, cum_theme_flows,
        theme_map, weight_map, is_init_scan=False
    )
    scanner_write_redis(r, flows2, themes2)

    stocks_gui2, _ = _fetch_from_redis(r)
    row2 = next(s for s in stocks_gui2 if s["sid"] == "3105")
    print(f"  掃 2 (normal): delta={flows2[0]['delta_vol']:.0f}  "
          f"net={flows2[0]['net_flow']/1e4:+.1f}萬  "
          f"cum={row2['cum_net_flow']/1e4:+,.1f}萬")

    # delta_vol = 20050 - 19927 = 123 張
    assert flows2[0]["delta_vol"] == 123, f"第二掃 delta_vol 應為 123，得到 {flows2[0]['delta_vol']}"
    # cum 應在合理範圍（<1億）
    assert abs(row2["cum_net_flow"]) < 100_000_000, \
        f"第二掃 cum_net_flow={row2['cum_net_flow']/1e4:.0f}萬 超出合理範圍（spike！）"
    print("  ✓ 重啟後無 spike")


def test_redis_empty_returns_none():
    """GUI 讀空 Redis 應回傳 None，不會顯示舊資料"""
    r = fakeredis.FakeRedis(decode_responses=True)
    stocks, themes = _fetch_from_redis(r)
    assert stocks is None, "空 Redis 應回傳 None stocks"
    assert themes is None, "空 Redis 應回傳 None themes"
    print("\n  ✓ 空 Redis 回傳 None，GUI 不顯示舊資料")


def test_redis_partial_keys_returns_none():
    """只有 tracker:stocks 沒有 tracker:themes → 應回傳 None（不用半舊資料）"""
    r = fakeredis.FakeRedis(decode_responses=True)
    r.set("tracker:stocks", json.dumps([{"sid": "3105", "cum_net_flow": -999}]))
    stocks, themes = _fetch_from_redis(r)
    assert stocks is None, "partial Redis（缺 themes）應回傳 None"
    print("\n  ✓ 缺 themes key 時 GUI 不顯示資料")


def test_cum_net_flow_accumulates_correctly_across_multiple_stocks():
    """
    多支股票同時跑：確認每支的 cum_net_flow 獨立累計不互相干擾
    """
    r = fakeredis.FakeRedis(decode_responses=True)
    theme_map  = {}
    weight_map = {}

    # 股票 A 漲，B 跌，兩者 cum_net_flow 方向應相反
    baseline = [
        make_tick("A", vol=1000, price=100, prev_close=95),
        make_tick("B", vol=2000, price=50,  prev_close=55),
    ]
    prev_snap = {t["c"]: t for t in baseline}
    cum_flows: dict = {}
    cum_theme_flows: dict = {}

    scan1 = [
        make_tick("A", vol=1100, price=102, prev_close=95),  # 漲 → flow_in
        make_tick("B", vol=2150, price=49,  prev_close=55),  # 跌 → flow_out
    ]
    scan2 = [
        make_tick("A", vol=1200, price=103, prev_close=95),  # 繼續漲
        make_tick("B", vol=2300, price=48,  prev_close=55),  # 繼續跌
    ]

    for scan in [scan1, scan2]:
        flows, sectors, themes, prev_snap = run_scan_cycle(
            scan, prev_snap, cum_flows, cum_theme_flows, theme_map, weight_map
        )

    scanner_write_redis(r, flows, themes)
    stocks_gui, _ = _fetch_from_redis(r)

    row_a = next(s for s in stocks_gui if s["sid"] == "A")
    row_b = next(s for s in stocks_gui if s["sid"] == "B")
    print(f"\n  A cum={row_a['cum_net_flow']/1e4:+.2f}萬  B cum={row_b['cum_net_flow']/1e4:+.2f}萬")

    assert row_a["cum_net_flow"] > 0, "A 累計應為正（flow_in）"
    assert row_b["cum_net_flow"] < 0, "B 累計應為負（flow_out）"
    assert row_a["cum_net_flow"] == cum_flows["A"]
    assert row_b["cum_net_flow"] == cum_flows["B"]
    print("  ✓ 多股票 cum_net_flow 獨立正確")


if __name__ == "__main__":
    print("=" * 60)
    print("測試 1：正常三掃 pipeline")
    test_normal_pipeline_3_scans()

    print("\n測試 2：清空 Redis 重啟無 spike")
    test_restart_spike_fix_full_pipeline()

    print("\n測試 3：空 Redis 回傳 None")
    test_redis_empty_returns_none()

    print("\n測試 4：缺 themes key 回傳 None")
    test_redis_partial_keys_returns_none()

    print("\n測試 5：多股票獨立累計")
    test_cum_net_flow_accumulates_correctly_across_multiple_stocks()

    print("\n" + "=" * 60)
    print("全部通過")
