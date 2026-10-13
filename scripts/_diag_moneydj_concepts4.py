# -*- coding: utf-8 -*-
"""Find concept names + stock API endpoint."""
import re, sys, urllib.request
from urllib.request import Request
sys.stdout.reconfigure(encoding="utf-8")

BASE = "https://www.moneydj.com"

def fetch(url):
    req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as r:
        charset = r.headers.get_content_charset() or "big5"
        return r.read().decode(charset, errors="replace")

# ── 1. 從 EH000007 頁面擷取所有概念名稱 ──────────────────────────────────────
html = fetch(f"{BASE}/Z/ZG/ZGE/ZGE.djhtm?a=EH000007&b=1")

print("=== EH ID + 概念名稱 ===")
# 找 a=EH... 連結旁邊的文字
matches = re.findall(r'a=(EH\d+)[^>]*>([^<]{1,40})</a>', html)
for m in matches[:30]:
    print(f"  {m[0]}: {m[1].strip()}")

print(f"\nTotal EH matches: {len(matches)}")

# ── 2. 找 ZU10 API URL ──────────────────────────────────────
print("\n=== ZU10 / API patterns ===")
# 找所有 .djhtm 或 .djjs 中包含 UStock 或 ZU 的
for m in re.finditer(r'["\']([^"\']*(?:UStock|ZU10|GetStock)[^"\']*)["\']', html, re.IGNORECASE):
    print(f"  {m.group(1)}")

# 找 fetch/ajax/xmlhttp 模式
for m in re.finditer(r'(?:url|src|href)\s*[=:]\s*["\']([^"\']{10,100})["\']', html, re.IGNORECASE):
    val = m.group(1)
    if any(x in val.lower() for x in ['zg', 'zu', 'stock', 'api', 'data']):
        print(f"  URL ref: {val}")

# ── 3. 試抓 ZU10 API 端點 ──────────────────────────────────────
print("\n=== 試 ZU10 直接 API ===")
# MoneyDJ 的 stock list API 常見格式
test_urls = [
    f"{BASE}/Z/ZU/ZU10.djhtm?a=EH000007&b=1",
    f"{BASE}/Z/ZU/ZUA.djhtm?a=EH000007",
    f"{BASE}/z/zu/zu10.djhtm?a=EH000007&b=1",
]
for url in test_urls:
    try:
        h = fetch(url)
        # 找股票代號
        codes = re.findall(r"Link2Stk\('([^']+)'\)", h)
        # 找4碼數字
        nums = re.findall(r"'(\d{4})'", h)
        print(f"  {url}")
        print(f"    Link2Stk: {codes[:5]}  4-digit nums: {nums[:5]}")
        print(f"    Length: {len(h)}")
    except Exception as e:
        print(f"  {url} → {e}")

# ── 4. 抓 zu10GetUStock.djjs 本身 ──────────────────────────────────────
print("\n=== zu10GetUStock.djjs content ===")
try:
    js = fetch(f"{BASE}/z/zu/js/zu10GetUStock.djjs")
    print(f"  Length: {len(js)}")
    # 找 URL patterns in JS
    for m in re.finditer(r'["\']([^"\']*(?:djhtm|djjs|api)[^"\']*)["\']', js):
        print(f"  JS URL: {m.group(1)}")
    print(f"  JS snippet: {js[:500]}")
except Exception as e:
    print(f"  Error: {e}")
