# -*- coding: utf-8 -*-
import re, sys, urllib.request, time
from urllib.request import Request
sys.stdout.reconfigure(encoding="utf-8")

BASE = "https://www.moneydj.com"

def fetch(url):
    req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as r:
        charset = r.headers.get_content_charset() or "big5"
        return r.read().decode(charset, errors="replace")

html = fetch(f"{BASE}/z/zg/zge_EH000007_1.djhtm")
concepts = re.findall(r'<option\s+value="(EH\d+)"[^>]*>([^<]+)</option>', html)
print(f"Concept list: {len(concepts)} items")

for eh_id, name in concepts[:5]:
    page = fetch(f"{BASE}/z/zg/zge_{eh_id}_1.djhtm")
    stocks = re.findall(r"GenLink2stk\('[A-Z]{1,3}(\d{4,6})'", page)
    print(f"  {eh_id}  {name.strip()[:25]:25s}  {stocks[:5]}  ({len(stocks)} stocks)")
    time.sleep(0.3)
