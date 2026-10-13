# -*- coding: utf-8 -*-
"""
模擬掃描器資金流計算，不需要市場開盤、Redis 或 InfluxDB。

執行：
    .venv/Scripts/python.exe -m pytest tests/test_scanner_flow.py -v
    .venv/Scripts/python.exe tests/test_scanner_flow.py
"""
import sys
sys.stdout.reconfigure(encoding="utf-8")
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from monitor.scanner import calc_flow


def make_tick(sid, vol, price, prev_close=None, open_price=None):
    """建立一筆模擬 MIS tick（只填必要欄位）"""
    return {
        "c": sid, "n": f"股票{sid}", "i": "24",
        "v": str(vol),
        "z": str(price),
        "y": str(prev_close or price),
        "o": str(open_price or price),
        "h": str(price),
        "l": str(price),
    }


# ─── 輔助 ──────────────────────────────────────────────────────────────────────

def simulate_restart_spike(use_init_scan_fix: bool):
    """
    模擬盤中重啟：prev_snap 是空的，第一掃的 delta_vol 會等於全天累計量。
    use_init_scan_fix=True 代表把第一掃流量歸零（修正版）。
    回傳第一掃和第二掃的 cum_net_flow。
    """
    # 3105 實際情境：開盤 480，現在跌到 457，全天量 19927 張
    # 重啟後 prev is None → prev_price = open_price = 480
    # delta_p = 457 - 480 = -23 < 0 → flow_out = 全天量 * 457 * 1000 → 巨大 spike
    tick_scan1 = make_tick("3105", vol=19927, price=457, prev_close=483, open_price=480)
    tick_scan2 = make_tick("3105", vol=20050, price=456, prev_close=483, open_price=480)  # 增加 123 張

    prev_snap = {}   # 重啟後是空的

    # ── 第一掃 ────────────────────────────────────────────────────────────────
    f1 = calc_flow(tick_scan1, prev_snap.get("3105"))
    if use_init_scan_fix:
        f1["delta_vol"] = 0
        f1["flow_in"]   = 0
        f1["flow_out"]  = 0
        f1["net_flow"]  = 0

    cum_flows = {}
    cum_flows["3105"] = cum_flows.get("3105", 0.0) + f1["net_flow"]
    f1["cum_net_flow"] = cum_flows["3105"]

    prev_snap_after_scan1 = {"3105": tick_scan1}

    # ── 第二掃 ────────────────────────────────────────────────────────────────
    f2 = calc_flow(tick_scan2, prev_snap_after_scan1.get("3105"))
    cum_flows["3105"] = cum_flows.get("3105", 0.0) + f2["net_flow"]
    f2["cum_net_flow"] = cum_flows["3105"]

    return f1, f2


def simulate_normal_two_scans():
    """正常兩次掃描：第一掃有 prev_snap（不是空的）"""
    tick_scan0 = make_tick("3105", vol=19800, price=460, prev_close=483)
    tick_scan1 = make_tick("3105", vol=19927, price=457, prev_close=483)
    tick_scan2 = make_tick("3105", vol=20050, price=456, prev_close=483)

    prev_snap = {"3105": tick_scan0}
    cum_flows = {"3105": -5_000_000.0}   # 假設已有累計 -500 萬

    f1 = calc_flow(tick_scan1, prev_snap.get("3105"))
    cum_flows["3105"] += f1["net_flow"]
    f1["cum_net_flow"] = cum_flows["3105"]

    prev_snap = {"3105": tick_scan1}
    f2 = calc_flow(tick_scan2, prev_snap.get("3105"))
    cum_flows["3105"] += f2["net_flow"]
    f2["cum_net_flow"] = cum_flows["3105"]

    return f1, f2, cum_flows


# ─── 測試 ──────────────────────────────────────────────────────────────────────

def test_restart_spike_without_fix():
    """舊 bug 重現：沒有修正時，第一掃 cum_net_flow 應該極大（spike）"""
    f1, f2 = simulate_restart_spike(use_init_scan_fix=False)
    spike_wan = f1["cum_net_flow"] / 1e4
    print(f"\n[舊行為] 第一掃 cum_net_flow = {spike_wan:+,.0f} 萬  (delta_vol={f1['delta_vol']})")
    # 預期：delta_vol ≈ 19927（全天量），net_flow 極大
    assert f1["delta_vol"] == 19927, f"預期 delta_vol=19927，得到 {f1['delta_vol']}"
    assert abs(f1["cum_net_flow"]) > 5_000_000_000, \
        f"預期 spike > 50億，得到 {f1['cum_net_flow']}"
    print("  ✓ spike 重現正確")


def test_restart_spike_with_fix():
    """修正後：第一掃歸零，第二掃只算增量"""
    f1, f2 = simulate_restart_spike(use_init_scan_fix=True)
    print(f"\n[修正後] 第一掃 delta_vol={f1['delta_vol']}  cum={f1['cum_net_flow']/1e4:+,.0f} 萬")
    print(f"         第二掃 delta_vol={f2['delta_vol']}  cum={f2['cum_net_flow']/1e4:+,.0f} 萬")

    # 第一掃：歸零
    assert f1["delta_vol"] == 0, f"init_scan delta_vol 應為 0，得到 {f1['delta_vol']}"
    assert f1["net_flow"]  == 0, f"init_scan net_flow 應為 0，得到 {f1['net_flow']}"
    assert f1["cum_net_flow"] == 0.0, f"init_scan cum 應為 0，得到 {f1['cum_net_flow']}"

    # 第二掃：delta_vol = 20050 - 19927 = 123 張
    assert f2["delta_vol"] == 123, f"第二掃 delta_vol 應為 123，得到 {f2['delta_vol']}"
    # 跌 1 元 → flow_out = 123 * 456 * 1000 = 56,088,000
    expected_net = -123 * 456 * 1000
    assert f2["net_flow"] == expected_net, \
        f"第二掃 net_flow 應為 {expected_net}，得到 {f2['net_flow']}"
    assert f2["cum_net_flow"] == expected_net, "第二掃 cum 應等於 net_flow（從 0 開始）"
    print(f"  ✓ 修正正確，第二掃 net_flow = {f2['net_flow']/1e4:+,.1f} 萬")


def test_normal_two_scans():
    """正常情境：有 prev_snap 時兩掃的增量計算正確"""
    f1, f2, cum = simulate_normal_two_scans()
    print(f"\n[正常] 第一掃 delta={f1['delta_vol']}  net={f1['net_flow']/1e4:+,.1f} 萬  cum={f1['cum_net_flow']/1e4:+,.1f} 萬")
    print(f"       第二掃 delta={f2['delta_vol']}  net={f2['net_flow']/1e4:+,.1f} 萬  cum={f2['cum_net_flow']/1e4:+,.1f} 萬")

    # 第一掃：19927 - 19800 = 127 張，跌 3 元 → flow_out
    assert f1["delta_vol"] == 127
    assert f1["flow_out"] == 127 * 457 * 1000
    assert f1["flow_in"]  == 0

    # 第二掃：20050 - 19927 = 123 張，跌 1 元 → flow_out
    assert f2["delta_vol"] == 123
    assert f2["flow_out"] == 123 * 456 * 1000
    print("  ✓ 正常兩掃增量正確")


def test_no_spike_after_fresh_redis_clear():
    """
    模擬最常見的場景：盤中清掉 Redis + InfluxDB，重啟 scanner。
    新 scanner 的 prev_snap 是空的，is_init_scan=True → 第一掃不應有 spike。
    接下來第 2、3 掃的 cum_net_flow 應該是合理小數字。
    """
    ticks = [
        make_tick("3105", vol=19927, price=457, prev_close=483, open_price=480),   # 掃 1（基準）
        make_tick("3105", vol=20050, price=456, prev_close=483, open_price=480),   # 掃 2
        make_tick("3105", vol=20180, price=455, prev_close=483, open_price=480),   # 掃 3
    ]
    prev_snap = {}
    cum_flows = {}
    is_init_scan = not prev_snap  # True，模擬 scanner 啟動後的 flag

    results = []
    for i, tick in enumerate(ticks):
        f = calc_flow(tick, prev_snap.get("3105"))
        if is_init_scan:
            f["delta_vol"] = f["flow_in"] = f["flow_out"] = f["net_flow"] = 0
            is_init_scan = False
        cum_flows["3105"] = cum_flows.get("3105", 0.0) + f["net_flow"]
        f["cum_net_flow"] = cum_flows["3105"]
        prev_snap = {"3105": tick}
        results.append(f)
        print(f"  掃 {i+1}: delta={f['delta_vol']:5.0f}  net={f['net_flow']/1e4:+10.1f} 萬  cum={f['cum_net_flow']/1e4:+,.1f} 萬")

    # 掃 1：歸零
    assert results[0]["cum_net_flow"] == 0
    # 掃 2 和 3：cum_net_flow 在合理範圍（< 1億萬）
    max_reasonable = 1_000_000_000   # 10 億
    assert abs(results[1]["cum_net_flow"]) < max_reasonable, \
        f"掃2 cum={results[1]['cum_net_flow']} 超出合理範圍"
    assert abs(results[2]["cum_net_flow"]) < max_reasonable, \
        f"掃3 cum={results[2]['cum_net_flow']} 超出合理範圍"
    print("  ✓ 清空 Redis 後重啟無 spike")


if __name__ == "__main__":
    print("=" * 60)
    print("測試 1：舊 bug spike 重現")
    test_restart_spike_without_fix()

    print("\n測試 2：is_init_scan 修正")
    test_restart_spike_with_fix()

    print("\n測試 3：正常兩掃增量計算")
    test_normal_two_scans()

    print("\n測試 4：清空 Redis 後重啟三掃模擬")
    test_no_spike_after_fresh_redis_clear()

    print("\n" + "=" * 60)
    print("全部通過")
