# -*- coding: utf-8 -*-
"""Dump raw HTML to understand page structure."""
import re, sys, urllib.request
from urllib.request import Request
sys.stdout.reconfigure(encoding="utf-8")

BASE = "https://www.moneydj.com"

def fetch(url):
    req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as r:
        charset = r.headers.get_content_charset() or "big5"
        raw = r.read()
        return raw, raw.decode(charset, errors="replace")

# ── 概念股主頁 ──────────────────────────────────────
print("=== zg.djhtm raw snippet (first 3000 chars) ===")
raw, html = fetch(f"{BASE}/z/zg/zg.djhtm")
print(html[:3000])

print("\n\n=== All <a href=...> in main page ===")
for m in re.finditer(r'<a\s[^>]*href=["\']([^"\']*)["\'][^>]*>(.*?)</a>', html, re.DOTALL | re.IGNORECASE):
    href, text = m.group(1), re.sub(r'<[^>]+>', '', m.group(2)).strip()
    if href.strip():
        print(f"  {href}  →  {text[:40]}")

print("\n\n=== EH000007 page raw snippet (first 3000 chars) ===")
raw2, html2 = fetch(f"{BASE}/z/zg/zge_EH000007_1.djhtm")
print(html2[:3000])

print("\n\n=== All href in EH000007 page ===")
for m in re.finditer(r'href=["\']([^"\']*)["\']', html2, re.IGNORECASE):
    href = m.group(1).strip()
    if href:
        print(f"  {href}")
