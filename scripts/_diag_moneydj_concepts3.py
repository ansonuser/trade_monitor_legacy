# -*- coding: utf-8 -*-
"""Find concept list page and stock data in EH000007."""
import re, sys, urllib.request
from urllib.request import Request
sys.stdout.reconfigure(encoding="utf-8")

BASE = "https://www.moneydj.com"

def fetch(url):
    req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as r:
        charset = r.headers.get_content_charset() or "big5"
        return r.read().decode(charset, errors="replace")

# ── 1. 完整 EH000007 頁面（不截斷）──────────────────────────────────────
print("=== EH000007 full HTML length ===")
html = fetch(f"{BASE}/Z/ZG/ZGE/ZGE.djhtm?a=EH000007&b=1")
print(f"HTML length: {len(html)}")

# 找 Link2Stk
stocks = re.findall(r"Link2Stk\('([^']+)'\)", html)
print(f"Link2Stk entries: {len(stocks)}")
for s in stocks[:10]:
    print(f"  {s}")

# 找所有 EH\d+ patterns
eh_ids = re.findall(r"EH\d+", html)
print(f"\nEH IDs found: {set(eh_ids)}")

# 找 a= 參數的連結
a_params = re.findall(r'[?&]a=(EH\d+)', html)
print(f"a= params: {set(a_params)}")

# 找頁碼 b=
b_params = re.findall(r'[?&]b=(\d+)', html)
print(f"b= params: {set(b_params)}")

# 找概念名稱（通常在 table 標題或 h2/h3）
title_match = re.search(r'概念股[^<]{0,50}', html)
if title_match:
    print(f"概念名稱: {title_match.group()}")

# 印 body 中間區段看 table 結構
body_start = html.find('<body')
if body_start >= 0:
    print(f"\nbody snippet (500 chars from body start):")
    print(html[body_start:body_start+500])

# ── 2. 試找概念清單頁 ──────────────────────────────────────
print("\n=== 概念清單入口 ZGE.djhtm ===")
try:
    html2 = fetch(f"{BASE}/Z/ZG/ZGE/ZGE.djhtm")
    print(f"HTML length: {len(html2)}")
    eh_ids2 = re.findall(r"EH\d+", html2)
    print(f"EH IDs count: {len(set(eh_ids2))}")
    print(f"Sample EH IDs: {sorted(set(eh_ids2))[:10]}")
    # 找概念名稱 + ID
    for m in re.finditer(r'a=(EH\d+)[^>]*>([^<]{1,30})<', html2):
        print(f"  {m.group(1)}: {m.group(2).strip()}")
except Exception as e:
    print(f"Error: {e}")

# ── 3. 試找個股代號格式 ──────────────────────────────────────
print("\n=== 個股代號 patterns in EH000007 ===")
# 4-digit numbers that look like stock codes
stock_codes = re.findall(r'\b(\d{4})\b', html)
from collections import Counter
c = Counter(stock_codes)
print(f"4-digit numbers (top 20): {c.most_common(20)}")

# 找 table 行內含股票代號的 JS 或 data
for m in re.finditer(r"'(\d{4,6})'", html):
    code = m.group(1)
    if 1000 <= int(code) <= 9999:
        print(f"  quoted code: {code}")
        break
