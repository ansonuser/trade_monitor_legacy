"""
從 MoneyDJ 抓取細產業分類（大產業 → 細產業 → 個股）
輸出：
  data/moneydj_industries.json  完整層級（大→細→個股代號）
  data/themes_moneydj.json      扁平化 {細產業名: [sid, ...]}

用法:
    .venv/Scripts/python.exe scripts/fetch_moneydj_industries.py
    .venv/Scripts/python.exe scripts/fetch_moneydj_industries.py --delay 0.8
"""

import argparse, json, re, time, sys
import urllib.request
from urllib.request import Request
from pathlib import Path
from lxml import etree

BASE     = "https://www.moneydj.com"
ZHA_URL  = f"{BASE}/Z/ZH/ZHA/ZHA.djhtm"
STOCK_URL = f"{BASE}/z/zh/zha/zh00.djhtm"
OUT_DIR  = Path(__file__).resolve().parent.parent / "data"


def fetch(url: str, retry: int = 3) -> str:
    for attempt in range(retry):
        try:
            req = Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"})
            with urllib.request.urlopen(req, timeout=15) as r:
                charset = r.headers.get_content_charset() or "big5"
                return r.read().decode(charset, errors="replace")
        except Exception as e:
            if attempt == retry - 1:
                raise
            print(f"  [retry {attempt+1}] {e}")
            time.sleep(2)


def txt(el) -> str:
    return "".join(el.itertext()).strip()


def parse_main_hierarchy(html: str) -> dict:
    """從 ZHA.djhtm 解析大產業 → 細產業層級（不含個股）"""
    tree = etree.HTML(html)
    target_table = None
    for t in tree.xpath("//table"):
        if len(t.xpath(".//a[contains(@href,'zh00.djhtm')]")) > 100:
            target_table = t
            break
    if target_table is None:
        raise RuntimeError("找不到產業 table，頁面結構可能已改變")

    hierarchy = {}
    current_main = None

    for tr in target_table.xpath(".//tr"):
        tds = tr.findall("td") or tr.findall("th")
        all_links = tr.xpath(".//a[contains(@href,'zh00.djhtm')]")
        if not all_links:
            continue

        first_links  = tds[0].xpath(".//a[contains(@href,'zh00.djhtm')]") if tds else []
        second_links = tds[1].xpath(".//a[contains(@href,'zh00.djhtm')]") if len(tds) > 1 else []

        def extract(a):
            m = re.search(r'[Aa]=([Cc]\d+)', a.get("href", ""))
            return (m.group(1).upper(), txt(a)) if m else None

        # 大產業 row：td[0] 一個連結 + td[1] 多個細產業連結
        if len(first_links) == 1 and len(second_links) > 1:
            info = extract(first_links[0])
            if info:
                current_main, main_name = info
                if current_main not in hierarchy:
                    hierarchy[current_main] = {"name": main_name, "subs": []}
                for sa in second_links:
                    sub = extract(sa)
                    if sub:
                        hierarchy[current_main]["subs"].append({"code": sub[0], "name": sub[1], "stocks": []})
            continue

        # 補充 row：各 td 一個細產業
        if current_main and len(tds) > 1 and len(first_links) == 1:
            existing = {s["code"] for s in hierarchy.get(current_main, {}).get("subs", [])}
            for td in tds:
                td_links = td.xpath(".//a[contains(@href,'zh00.djhtm')]")
                if len(td_links) == 1:
                    sub = extract(td_links[0])
                    if sub and sub[0] not in existing:
                        hierarchy.setdefault(current_main, {"name": "", "subs": []})["subs"].append(
                            {"code": sub[0], "name": sub[1], "stocks": []})
                        existing.add(sub[0])

    return hierarchy


def parse_stocks(html: str) -> list[str]:
    """從細產業頁面取出個股代號（4碼）"""
    # Link2Stk('AS1102') → 1102 / Link2Stk('AO6869') → 6869
    return re.findall(r"Link2Stk\('[AO][SQRTM]?(\d{4,6})'\)", html)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--delay", type=float, default=0.6, help="每次 request 間隔（秒）")
    parser.add_argument("--resume", action="store_true", help="從上次中斷點繼續（讀取已存在的 JSON）")
    args = parser.parse_args()

    OUT_DIR.mkdir(exist_ok=True)
    out_file = OUT_DIR / "moneydj_industries.json"

    # Step 1: 抓大產業結構
    print("Step 1: 抓大產業 / 細產業層級...")
    html = fetch(ZHA_URL)
    hierarchy = parse_main_hierarchy(html)

    main_count = len(hierarchy)
    sub_count  = sum(len(v["subs"]) for v in hierarchy.values())
    print(f"  大產業: {main_count}  細產業: {sub_count}")

    # 若 resume，讀取已有資料
    if args.resume and out_file.exists():
        existing = json.loads(out_file.read_text(encoding="utf-8"))
        # 把已抓到個股的細產業合併進去
        for mc, mv in hierarchy.items():
            if mc in existing:
                ex_subs = {s["code"]: s for s in existing[mc].get("subs", [])}
                for sub in mv["subs"]:
                    if sub["code"] in ex_subs and ex_subs[sub["code"]].get("stocks"):
                        sub["stocks"] = ex_subs[sub["code"]]["stocks"]
        print("  已合併上次進度")

    # Step 2: 逐一抓細產業個股
    total_subs = sub_count
    done = 0
    skipped = 0

    for mc, mv in hierarchy.items():
        for sub in mv["subs"]:
            done += 1
            if sub["stocks"]:
                skipped += 1
                continue  # 已有資料

            try:
                url  = f"{STOCK_URL}?A={sub['code']}"
                html = fetch(url)
                sub["stocks"] = parse_stocks(html)
                print(f"  [{done:4d}/{total_subs}] {sub['code']} {sub['name'][:12]:12s} → {len(sub['stocks'])} 支")
            except Exception as e:
                print(f"  [{done:4d}/{total_subs}] {sub['code']} ERROR: {e}")

            # 每 50 支存一次進度
            if done % 50 == 0:
                out_file.write_text(json.dumps(hierarchy, ensure_ascii=False, indent=2), encoding="utf-8")

            time.sleep(args.delay)

    # Step 3: 存完整層級 JSON
    out_file.write_text(json.dumps(hierarchy, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n寫入 {out_file}")

    # Step 4: 輸出扁平化 themes_moneydj.json {細產業名: [sid]}
    flat = {}
    for mv in hierarchy.values():
        for sub in mv["subs"]:
            if sub["stocks"]:
                flat[sub["name"]] = sub["stocks"]

    flat_file = OUT_DIR / "themes_moneydj.json"
    flat_file.write_text(json.dumps(flat, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"寫入 {flat_file}  ({len(flat)} 個細產業有個股資料)")

    if skipped:
        print(f"（跳過 {skipped} 個已有資料的細產業）")


if __name__ == "__main__":
    main()
