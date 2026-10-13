"""
從 MoneyDJ 抓取概念股分類（概念名稱 → 個股清單）
輸出：
  data/concepts_moneydj.json   {概念名稱: [sid, ...]}，共 170+ 個概念

用法:
    .venv/Scripts/python.exe scripts/fetch_moneydj_concepts.py
    .venv/Scripts/python.exe scripts/fetch_moneydj_concepts.py --delay 0.8
    .venv/Scripts/python.exe scripts/fetch_moneydj_concepts.py --resume
"""
import argparse, json, re, time, sys
import urllib.request
from urllib.request import Request
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

BASE     = "https://www.moneydj.com"
# 任意一個概念股頁面即可取得完整概念清單（<select> dropdown）
INDEX_URL = f"{BASE}/z/zg/zge_EH000007_1.djhtm"
OUT_DIR   = Path(__file__).resolve().parent.parent / "data"


def fetch(url: str, retry: int = 3) -> str:
    for attempt in range(retry):
        try:
            req = Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
            with urllib.request.urlopen(req, timeout=15) as r:
                charset = r.headers.get_content_charset() or "big5"
                return r.read().decode(charset, errors="replace")
        except Exception as e:
            if attempt == retry - 1:
                raise
            print(f"  [retry {attempt+1}] {e}")
            time.sleep(2)


def parse_concept_list(html: str) -> list[tuple[str, str]]:
    """
    從 <select> dropdown 解析所有概念：[(EH_ID, 概念名稱), ...]
    格式：<option value="EH000007">磷酸鋰鐵電池概念股</option>
    """
    return re.findall(r'<option\s+value="(EH\d+)"[^>]*>([^<]+)</option>', html)


def parse_stocks(html: str) -> list[str]:
    """
    從概念股頁面取出個股代號。
    格式：GenLink2stk('AS4739','康普') → '4739'
    """
    # AS 前綴 = 上市；有時有 OA/OT 等前綴也以同樣方式處理
    raw = re.findall(r"GenLink2stk\('[A-Z]{1,3}(\d{4,6})'", html)
    return raw


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--delay",  type=float, default=0.6,
                        help="每次 request 間隔（秒），預設 0.6")
    parser.add_argument("--resume", action="store_true",
                        help="從上次中斷點繼續（讀取已存在的 JSON）")
    args = parser.parse_args()

    OUT_DIR.mkdir(exist_ok=True)
    out_file = OUT_DIR / "concepts_moneydj.json"

    # Step 1: 取得完整概念清單
    print("Step 1: 取得概念清單...")
    html = fetch(INDEX_URL)
    concepts = parse_concept_list(html)
    print(f"  找到 {len(concepts)} 個概念")

    # Step 2: 載入已有進度（resume 模式）
    existing: dict[str, list[str]] = {}
    if args.resume and out_file.exists():
        existing = json.loads(out_file.read_text(encoding="utf-8"))
        print(f"  已有 {len(existing)} 個概念的資料（resume 模式）")

    # Step 3: 逐一抓各概念的個股
    result: dict[str, list[str]] = {}
    total = len(concepts)

    for i, (eh_id, concept_name) in enumerate(concepts, 1):
        concept_name = concept_name.strip()

        # resume：已有資料則跳過
        if args.resume and concept_name in existing:
            result[concept_name] = existing[concept_name]
            continue

        url = f"{BASE}/z/zg/zge_{eh_id}_1.djhtm"
        try:
            page_html = fetch(url)
            stocks = parse_stocks(page_html)
            result[concept_name] = stocks
            print(f"  [{i:4d}/{total}] {eh_id}  {concept_name[:20]:20s}  → {len(stocks)} 支")
        except Exception as e:
            print(f"  [{i:4d}/{total}] {eh_id}  ERROR: {e}")
            result[concept_name] = []

        # 每 50 筆存一次進度
        if i % 50 == 0:
            out_file.write_text(
                json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

        time.sleep(args.delay)

    # Step 4: 寫出最終結果
    out_file.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    total_stocks = sum(len(v) for v in result.values())
    print(f"\n完成！寫入 {out_file}")
    print(f"共 {len(result)} 個概念，{total_stocks} 筆個股對應（含重複）")


if __name__ == "__main__":
    main()
