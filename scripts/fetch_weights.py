"""
從台期所抓取各股市值佔大盤比重，存成 data/weights.json
用法: venv/Scripts/python.exe scripts/fetch_weights.py
"""
import json, re, sys, urllib.request
from pathlib import Path
from urllib.request import Request
from lxml import etree

OUT = Path(__file__).resolve().parent.parent / "data" / "weights.json"

def fetch():
    url = "https://www.taifex.com.tw/cht/9/futuresQADetail"
    req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as r:
        charset = r.headers.get_content_charset() or "big5"
        html = r.read().decode(charset, errors="replace")

    tree = etree.HTML(html)
    weights = {}

    for tr in tree.xpath("//tr"):
        tds = tr.findall("td")
        if len(tds) < 4:
            continue

        def cell(td):
            return (td.text or "").strip()

        # 每列有兩組 (左 a: td[0..3], 右 b: td[4..7])
        groups = []
        if len(tds) >= 4:
            groups.append(tds[0:4])
        if len(tds) >= 8:
            groups.append(tds[4:8])

        for g in groups:
            rank_td, code_td, name_td, pct_td = g
            sid  = cell(code_td)
            name = cell(name_td)
            pct  = cell(pct_td).replace("%", "").replace(",", "")
            if not re.match(r"^\d{4}$", sid):
                continue
            try:
                weights[sid] = {
                    "name":   name,
                    "rank":   int(cell(rank_td).replace(",", "")),
                    "weight": float(pct),
                }
            except ValueError:
                continue

    if not weights:
        print("ERROR: 解析失敗，請確認頁面格式")
        sys.exit(1)

    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(weights, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"寫入 {len(weights)} 支股票權重 → {OUT}")
    top5 = sorted(weights.items(), key=lambda x: x[1]["rank"])[:5]
    for sid, v in top5:
        print(f"  {v['rank']:>4}. {sid} {v['name']:<10} {v['weight']:.4f}%")

if __name__ == "__main__":
    fetch()
