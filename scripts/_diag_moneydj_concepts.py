# -*- coding: utf-8 -*-
"""Diagnose MoneyDJ concept stocks page structure."""
import re, sys, urllib.request
from urllib.request import Request
from lxml import etree
sys.stdout.reconfigure(encoding="utf-8")

BASE = "https://www.moneydj.com"

def fetch(url):
    req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as r:
        charset = r.headers.get_content_charset() or "big5"
        return r.read().decode(charset, errors="replace")

# ── 1. 概念股列表主頁 ──────────────────────────────────────
print("=== 概念股主頁 ===")
html = fetch(f"{BASE}/z/zg/zg.djhtm")
tree = etree.HTML(html)

# 找所有含 zge 的連結
zge_links = tree.xpath("//a[contains(@href,'zge')]")
print(f"zge links: {len(zge_links)}")
for a in zge_links[:10]:
    print(f"  href={a.get('href')}  text={''.join(a.itertext()).strip()}")

# 找所有含 EH 的連結
eh_links = tree.xpath("//a[contains(@href,'EH')]")
print(f"\nEH links: {len(eh_links)}")
for a in eh_links[:10]:
    print(f"  href={a.get('href')}  text={''.join(a.itertext()).strip()}")

# 所有 a href 樣本
all_hrefs = [a.get("href","") for a in tree.xpath("//a[@href]")]
print(f"\nAll hrefs sample (first 20):")
for h in all_hrefs[:20]:
    print(f"  {h}")

# ── 2. 特定概念股頁 ──────────────────────────────────────
print("\n=== 特定概念頁 EH000007 ===")
html2 = fetch(f"{BASE}/z/zg/zge_EH000007_1.djhtm")
tree2 = etree.HTML(html2)

# Link2Stk pattern
stocks = re.findall(r"Link2Stk\('[AO][SQRTM]?(\d{4,6})'\)", html2)
print(f"Link2Stk stocks: {stocks[:20]}")

# 頁碼連結
page_links = tree2.xpath("//a[contains(@href,'zge_EH')]")
print(f"Page links: {len(page_links)}")
for a in page_links[:10]:
    print(f"  href={a.get('href')}  text={''.join(a.itertext()).strip()}")

# 概念名稱
title_els = tree2.xpath("//title | //h1 | //h2")
for el in title_els:
    print(f"  title/h: {''.join(el.itertext()).strip()[:80]}")

# 找概念 ID / 名稱 mapping
print("\n所有含 EH 的 href:")
for a in tree2.xpath("//a[contains(@href,'EH')]"):
    print(f"  {a.get('href')}  → {''.join(a.itertext()).strip()}")
