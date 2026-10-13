# -*- coding: utf-8 -*-
"""Find stock list and concept names in ZGE full HTML body."""
import re, sys, urllib.request
from urllib.request import Request
sys.stdout.reconfigure(encoding="utf-8")

BASE = "https://www.moneydj.com"

def fetch(url):
    req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as r:
        charset = r.headers.get_content_charset() or "big5"
        return r.read().decode(charset, errors="replace")

# Full ZGE page
html = fetch(f"{BASE}/Z/ZG/ZGE/ZGE.djhtm?a=EH000007&b=1")
print(f"Total HTML: {len(html)} chars")

# Show body portion (skip head)
body_start = html.find('<body')
print(f"\n--- BODY (first 3000 chars) ---")
print(html[body_start:body_start+3000])

print(f"\n--- BODY (3000-6000) ---")
print(html[body_start+3000:body_start+6000])

print(f"\n--- BODY (6000-9000) ---")
print(html[body_start+6000:body_start+9000])

print(f"\n--- BODY (9000-end) ---")
print(html[body_start+9000:])
