# -*- coding: utf-8 -*-
"""Find concept names from LeftMenuStock JS and ZU10 HTML."""
import re, sys, urllib.request
from urllib.request import Request
sys.stdout.reconfigure(encoding="utf-8")

BASE = "https://www.moneydj.com"

def fetch(url):
    req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as r:
        charset = r.headers.get_content_charset() or "big5"
        return r.read().decode(charset, errors="replace")

# ── 1. LeftMenuStock_zu.js ──────────────────────────────────────
print("=== LeftMenuStock_zu.js ===")
try:
    js = fetch(f"{BASE}/z/js/LeftMenuStock_zu.js")
    print(f"Length: {len(js)}")
    # EH IDs with nearby text
    for m in re.finditer(r'(EH\d+)[^,\n]{0,80}', js):
        print(f"  {m.group()[:100]}")
    # 前500字
    print(f"Snippet:\n{js[:1000]}")
except Exception as e:
    print(f"Error: {e}")

# ── 2. ZU10 頁面完整內容 ──────────────────────────────────────
print("\n=== ZU10.djhtm?a=EH000007 full content ===")
html = fetch(f"{BASE}/Z/ZU/ZU10.djhtm?a=EH000007&b=1")
print(f"Length: {len(html)}")
# 找 Link2Stk
stocks_link2 = re.findall(r"Link2Stk\('([^']+)'\)", html)
print(f"Link2Stk: {stocks_link2[:10]}")
# 找 4-6 digit stock codes
stock_nums = re.findall(r"'([A-Z]+)(\d{4,6})'", html)
print(f"Prefixed codes: {stock_nums[:10]}")
# 印前1500字 body
body_idx = html.find('<body')
print(f"\nBody snippet:\n{html[body_idx:body_idx+2000] if body_idx >= 0 else html[:2000]}")

# ── 3. 試 LeftMenuStock.js（普通版）──────────────────────────────────────
print("\n=== LeftMenuStock.js EH mentions ===")
try:
    js2 = fetch(f"{BASE}/z/js/LeftMenuStock.js")
    print(f"Length: {len(js2)}")
    for m in re.finditer(r'EH\d+', js2):
        # print context
        start = max(0, m.start()-30)
        end   = min(len(js2), m.end()+60)
        print(f"  {js2[start:end]!r}")
    print(f"Snippet:\n{js2[:500]}")
except Exception as e:
    print(f"Error: {e}")
