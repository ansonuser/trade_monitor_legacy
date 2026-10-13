# -*- coding: utf-8 -*-
"""
盤中 5 分鐘快照掃描器核心
- 全市場量價快照（mis.twse.com.tw）
- 估算個股/族群資金流向（量 × 價 × 漲跌方向）

CLI 使用:
    venv/Scripts/python.exe monitor/scanner.py
    venv/Scripts/python.exe monitor/scanner.py --interval 60 --once
"""

import asyncio, time, datetime, re, logging, json, os
import urllib.request
from urllib.request import Request
from collections import defaultdict
from pathlib import Path
from zoneinfo import ZoneInfo

TAIPEI_TZ = ZoneInfo("Asia/Taipei")

def _setup_logger():
    log_dir = Path(__file__).resolve().parent.parent / "logs"
    log_dir.mkdir(exist_ok=True)
    log_path = log_dir / f"scanner_{datetime.date.today():%Y%m%d}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(message)s",
        datefmt="%H:%M:%S",
        handlers=[
            logging.FileHandler(log_path, encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )
    return logging.getLogger("scanner")

def _get_redis_client():
    """取得 Redis 連線（支援 Docker + 本地）"""
    try:
        import redis
        host = os.getenv("REDIS_HOST", "localhost")
        port = int(os.getenv("REDIS_PORT", 6379))
        db = int(os.getenv("REDIS_DB", 0))
        r = redis.Redis(host=host, port=port, db=db, decode_responses=True)
        r.ping()
        return r
    except Exception as e:
        return None


def _get_influx_client():
    """取得 InfluxDB write_api + query_api，失敗回傳 (None, None, None)"""
    try:
        from influxdb_client import InfluxDBClient
        from influxdb_client.client.write_api import SYNCHRONOUS
        url   = os.getenv("INFLUX_URL",   "http://localhost:8086")
        token = os.getenv("INFLUX_TOKEN", "mytoken123")
        org   = os.getenv("INFLUX_ORG",   "stocks")
        client = InfluxDBClient(url=url, token=token, org=org)
        return client.write_api(write_options=SYNCHRONOUS), client.query_api(), org
    except Exception:
        return None, None, None


def _recover_from_influx(query_api, org: str, today_str: str) -> tuple[dict, dict, dict]:
    """
    從 InfluxDB 查詢今日最後一筆快照，供重啟時回補：
      cum_flows       {sid: cum_net_flow}
      cum_theme_flows {theme: cum_net_flow}
      prev_snap       {sid: {"c":sid, "n":name, "v":vol_str, "z":price_str}}
    """
    bucket = os.getenv("INFLUX_BUCKET", "intraday")
    cum_flows: dict[str, float] = {}
    cum_theme_flows: dict[str, float] = {}
    prev_snap: dict[str, dict] = {}
    try:
        # 每股：cum_net_flow、volume_total、price（一次查三個 field）
        flux_stocks = f'''
from(bucket: "{bucket}")
  |> range(start: {today_str}T00:00:00+08:00)
  |> filter(fn: (r) => r._measurement == "stock")
  |> filter(fn: (r) => r._field == "cum_net_flow" or r._field == "volume_total" or r._field == "price")
  |> group(columns: ["sid", "name", "_field"])
  |> last()
  |> keep(columns: ["sid", "name", "_field", "_value"])
'''
        # 先收集每股每 field 的最後值
        stock_data: dict[str, dict] = {}   # {sid: {field: value, name: name}}
        for table in query_api.query(flux_stocks, org=org):
            for rec in table.records:
                sid   = rec.values.get("sid")
                name  = rec.values.get("name", "")
                field = rec.get_field()
                val   = rec.get_value()
                if sid is None or val is None:
                    continue
                d = stock_data.setdefault(sid, {"name": name})
                d[field] = val

        for sid, d in stock_data.items():
            if "cum_net_flow" in d:
                cum_flows[sid] = d["cum_net_flow"]
            vol   = d.get("volume_total")
            price = d.get("price")
            if vol is not None and price is not None:
                prev_snap[sid] = {"c": sid, "n": d["name"],
                                  "v": str(int(vol)), "z": str(price)}

        flux_themes = f'''
from(bucket: "{bucket}")
  |> range(start: {today_str}T00:00:00+08:00)
  |> filter(fn: (r) => r._measurement == "theme" and r._field == "cum_net_flow")
  |> group(columns: ["theme"])
  |> last()
  |> keep(columns: ["theme", "_value"])
'''
        for table in query_api.query(flux_themes, org=org):
            for rec in table.records:
                theme = rec.values.get("theme")
                val   = rec.get_value()
                if theme and val is not None:
                    cum_theme_flows[theme] = val

    except Exception as e:
        log.warning(f"[累計] InfluxDB 恢復查詢失敗: {e}")
    return cum_flows, cum_theme_flows, prev_snap


def write_to_influx(write_api, org: str, flows: list[dict],
                    themes: list[dict], ts: datetime.datetime,
                    is_restart: bool = False):
    from influxdb_client import Point
    bucket = os.getenv("INFLUX_BUCKET", "intraday")
    points = []
    restart_val = 1 if is_restart else 0

    for f in flows:
        if f.get("price") is None:
            continue
        points.append(
            Point("stock")
            .tag("sid",  f["sid"])
            .tag("name", f["name"])
            .field("price",        float(f["price"]))
            .field("day_chg_pct",  float(f.get("day_chg_pct",  0)))
            .field("net_flow",     float(f.get("net_flow",     0)))
            .field("cum_net_flow", float(f.get("cum_net_flow", 0)))
            .field("volume_total", float(f.get("volume_total", 0)))
            .field("is_restart",   restart_val)
            .time(ts)
        )

    for t in themes:
        points.append(
            Point("theme")
            .tag("theme", t["theme"])
            .field("avg_chg_pct",  float(t["avg_chg_pct"]))
            .field("net_flow",     float(t["net_flow"]))
            .field("flow_in",      float(t["flow_in"]))
            .field("flow_out",     float(t["flow_out"]))
            .field("volume_total", float(t["volume_total"]))
            .field("cum_net_flow", float(t.get("cum_net_flow", 0)))
            .time(ts)
        )

    # 前30名快照（供 Grafana 預設表格用，避免全市場 series 超限）
    top30 = sorted(
        [f for f in flows if f.get("price") is not None],
        key=lambda f: abs(f.get("cum_net_flow", 0)),
        reverse=True
    )[:30]
    for f in top30:
        points.append(
            Point("top30_stocks")
            .tag("sid",  f["sid"])
            .tag("name", f["name"])
            .field("price",        float(f["price"]))
            .field("day_chg_pct",  float(f.get("day_chg_pct",  0)))
            .field("cum_net_flow", float(f.get("cum_net_flow", 0)))
            .time(ts)
        )

    write_api.write(bucket=bucket, org=org, record=points)

MIS_URL        = "https://mis.twse.com.tw/stock/api/getStockInfo.jsp"
BATCH          = 100          # 每 request 100 支，~20 個 request 涵蓋全市場
REQ_CONCURRENCY = 3          # 每組併發請求數，符合 sliding window ≤ 3 req/5s
REQ_WINDOW     = 5.0          # 每組請求之間至少間隔的秒數（限流窗口）
BATCH_RETRIES  = 2            # 單批逾時/失敗時的重試次數
POLL_TIMEOUT   = 100.0       # poll_all 整輪逾時上限，避免單輪卡死拖垮整個迴圈
                              # TWSE 伺服器端回應時間（TTFB）2026 下半年起明顯變慢，
                              # 原序列模式（每 1.67s 發 1 個請求）全市場需 65-100s+，
                              # 常態逼近/超過舊有的 60s 上限，導致 asyncio.wait_for
                              # 頻繁強制取消、curl_cffi 底層連線未必乾淨釋放，
                              # 連續取消多次後曾讓整個 process 無聲消失。
                              # 改為每組併發 3 個請求（見 poll_all）後縮短到 ~30-40s，
                              # 100s 仍保留足夠安全邊際。
MARKET_OPEN  = datetime.time(8, 45)
MARKET_CLOSE = datetime.time(13, 30)
THEMES_FILE  = Path(__file__).resolve().parent.parent / "data" / "themes.json"
WEIGHTS_FILE = Path(__file__).resolve().parent.parent / "data" / "weights.json"

INDUSTRY_MAP = {
    "01": "水泥",     "02": "食品",     "03": "塑膠",     "04": "紡織",
    "05": "電機機械", "06": "電器電纜", "07": "化學",     "08": "玻璃陶瓷",
    "09": "造紙",     "10": "鋼鐵",     "11": "橡膠",     "12": "汽車",
    "13": "電子",     "14": "建材",     "15": "航運",     "16": "觀光",
    "17": "金融",     "18": "貿易",     "19": "綜合",     "20": "其他",
    "21": "化工",     "22": "生技醫療", "23": "油電燃氣",
    "24": "半導體",   "25": "電腦周邊", "26": "光電",
    "27": "通信網路", "28": "電子零組件","29": "電子通路",
    "30": "資訊服務", "31": "其他電子",
    "32": "文化創意", "33": "農業科技", "34": "電子商務",
    "35": "綠能環保", "36": "數位雲端", "37": "運動休閒",
    "38": "管理股票",
    "X":  "上櫃",    "?":  "ETF",
}


# ── 股票清單 ──────────────────────────────────────────────────────────────────

def fetch_stock_list() -> list[dict]:
    from lxml import etree
    url_map = {
        "tse": "https://isin.twse.com.tw/isin/C_public.jsp?strMode=2",
        "otc": "https://isin.twse.com.tw/isin/C_public.jsp?strMode=4",
    }
    results = []
    for market, url in url_map.items():
        req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            html = resp.read().decode(resp.headers.get_content_charset() or "cp950")
        tree = etree.HTML(html)
        table = tree.xpath("//table[@class='h4']")[0]
        for i, tr in enumerate(table.findall("tr")):
            if i == 0:
                continue
            tds = tr.findall("td")
            if len(tds) < 7:
                continue
            parts = (tds[0].text or "").split()
            if len(parts) != 2 or not re.match(r"^\d{4}$", parts[0]):
                continue
            results.append({"sid": parts[0], "market": market,
                            "ex_ch": f"{market}_{parts[0]}.tw"})
        time.sleep(0.5)
    print(f"[清單] 共 {len(results)} 支")
    return results


# ── 非同步批次抓取 ────────────────────────────────────────────────────────────

async def fetch_batch(session, ex_ch: str, retries: int = BATCH_RETRIES) -> list[dict]:
    params = {"ex_ch": ex_ch, "json": "1", "delay": "0"}
    for attempt in range(retries + 1):
        try:
            r = await session.get(MIS_URL, params=params, timeout=20)
            if r.status_code != 200:
                continue
            return r.json().get("msgArray", [])
        except Exception:
            continue
    return []


async def poll_all(codes: list[str]) -> list[dict]:
    """
    每組併發 REQ_CONCURRENCY 個請求，組間至少間隔 REQ_WINDOW 秒，
    符合台交所 sliding window ≤ 3 req/5s 限流，同時讓單批等待時間重疊，
    大幅縮短全市場輪詢總耗時（詳見 POLL_TIMEOUT 註解）。
    """
    from curl_cffi.requests import AsyncSession
    batches = [codes[i:i+BATCH] for i in range(0, len(codes), BATCH)]
    results = []
    async with AsyncSession(impersonate="chrome120") as session:
        try:
            await session.get("https://mis.twse.com.tw/stock/index.jsp", timeout=10)
        except Exception:
            pass  # 建立 cookie 失敗不影響後續請求，避免偶發連線重置讓整個 process 崩潰
        for i in range(0, len(batches), REQ_CONCURRENCY):
            group = batches[i:i+REQ_CONCURRENCY]
            t0 = time.time()
            group_results = await asyncio.gather(
                *(fetch_batch(session, "|".join(b)) for b in group)
            )
            for r in group_results:
                results.extend(r)
            remaining = REQ_WINDOW - (time.time() - t0)
            if remaining > 0 and i + REQ_CONCURRENCY < len(batches):
                await asyncio.sleep(remaining)
    return results


# ── 資金流計算 ────────────────────────────────────────────────────────────────

def _f(s) -> float | None:
    try:
        return float(str(s).replace(",", "").strip())
    except Exception:
        return None


def _price(d: dict) -> float | None:
    """成交價 → 買一非零（漲停鎖死）→ 賣一非零（跌停鎖死）→ 昨收"""
    z = _f(d.get("z"))
    if z:
        return z
    for p in (d.get("b") or "").split("_"):
        b = _f(p)
        if b:
            return b
    for p in (d.get("a") or "").split("_"):
        a = _f(p)
        if a:
            return a
    return _f(d.get("y"))


def calc_flow(now: dict, prev: dict | None) -> dict:
    sid   = now.get("c", "")
    name  = now.get("n", "")
    ind   = now.get("i", "X")
    price = _price(now)
    vol   = _f(now.get("v")) or 0

    if price is None:
        return {"sid": sid, "name": name, "industry": ind,
                "price": None, "volume_total": vol,
                "delta_vol": 0, "flow_in": 0, "flow_out": 0, "net_flow": 0}

    if prev is None:
        prev_price = _f(now.get("o")) or _f(now.get("y")) or price
        prev_vol   = 0
    else:
        prev_price = _price(prev) or price
        prev_vol   = _f(prev.get("v")) or 0

    delta_vol = max(vol - prev_vol, 0)
    amount    = delta_vol * price * 1000
    delta_p   = price - prev_price

    if delta_p > 0:
        flow_in, flow_out = amount, 0
    elif delta_p < 0:
        flow_in, flow_out = 0, amount
    else:
        flow_in = flow_out = amount / 2

    period_chg = (delta_p / prev_price * 100) if prev_price else 0
    prev_close = _f(now.get("y")) or price
    open_price = _f(now.get("o"))
    high_price = _f(now.get("h"))
    low_price  = _f(now.get("l"))
    open_chg   = (open_price - prev_close) / prev_close * 100 if (open_price and prev_close) else 0
    day_chg    = (price      - prev_close) / prev_close * 100 if prev_close else 0
    high_chg   = (high_price - prev_close) / prev_close * 100 if (high_price and prev_close) else day_chg
    low_chg    = (low_price  - prev_close) / prev_close * 100 if (low_price  and prev_close) else day_chg

    return {
        "sid": sid, "name": name, "industry": ind,
        "price": price, "volume_total": vol,
        "delta_vol": delta_vol,
        "flow_in": flow_in, "flow_out": flow_out,
        "net_flow": flow_in - flow_out,
        "chg_pct":      period_chg,
        "open_chg_pct": open_chg,
        "day_chg_pct":  day_chg,
        "high_chg_pct": high_chg,
        "low_chg_pct":  low_chg,
    }


def aggregate_sectors(stock_flows: list[dict]) -> list[dict]:
    sectors = defaultdict(lambda: {
        "flow_in": 0, "flow_out": 0, "net_flow": 0,
        "delta_vol": 0, "count": 0,
        "_open_chg_sum": 0.0, "_day_chg_sum": 0.0,
        "_high_chg_sum": 0.0, "_low_chg_sum": 0.0, "_vol_sum": 0.0,
    })
    for s in stock_flows:
        ind  = s["industry"]
        tvol = s["volume_total"]
        sectors[ind]["flow_in"]       += s["flow_in"]
        sectors[ind]["flow_out"]      += s["flow_out"]
        sectors[ind]["net_flow"]      += s["net_flow"]
        sectors[ind]["delta_vol"]     += s["delta_vol"]
        sectors[ind]["count"]         += 1
        sectors[ind]["_open_chg_sum"] += s["open_chg_pct"] * tvol
        sectors[ind]["_day_chg_sum"]  += s["day_chg_pct"]  * tvol
        sectors[ind]["_high_chg_sum"] += s["high_chg_pct"] * tvol
        sectors[ind]["_low_chg_sum"]  += s["low_chg_pct"]  * tvol
        sectors[ind]["_vol_sum"]      += tvol

    rows = []
    for ind, v in sectors.items():
        vs = v["_vol_sum"]
        rows.append({
            "industry_code": ind,
            "industry":      INDUSTRY_MAP.get(ind, ind),
            "open_chg_pct":  (v["_open_chg_sum"] / vs) if vs > 0 else 0,
            "avg_chg_pct":   (v["_day_chg_sum"]  / vs) if vs > 0 else 0,
            "high_chg_pct":  (v["_high_chg_sum"] / vs) if vs > 0 else 0,
            "low_chg_pct":   (v["_low_chg_sum"]  / vs) if vs > 0 else 0,
            "volume_total":  vs,
            **{k: v[k] for k in ("flow_in","flow_out","net_flow","delta_vol","count")},
        })
    rows.sort(key=lambda x: x["avg_chg_pct"], reverse=True)
    return rows


def load_weights() -> dict[str, float]:
    """載入 data/weights.json，回傳 {sid → 市值佔大盤%}"""
    if not WEIGHTS_FILE.exists():
        return {}
    with WEIGHTS_FILE.open(encoding="utf-8") as f:
        raw = json.load(f)
    return {sid: v["weight"] for sid, v in raw.items()}


def load_themes(min_stocks: int = 5) -> dict[str, list[str]]:
    """載入 data/themes.json，回傳 {stock_id → [theme, ...]}，過濾掉檔數 < min_stocks 的題材"""
    if not THEMES_FILE.exists():
        return {}
    with THEMES_FILE.open(encoding="utf-8") as f:
        raw: dict[str, list[str]] = json.load(f)
    sid_map: dict[str, list[str]] = defaultdict(list)
    for theme, sids in raw.items():
        if len(sids) < min_stocks:
            continue
        for sid in sids:
            sid_map[sid].append(theme)
    return dict(sid_map)


def aggregate_themes(stock_flows: list[dict], theme_map: dict[str, list[str]]) -> list[dict]:
    themes: dict[str, dict] = defaultdict(lambda: {
        "flow_in": 0, "flow_out": 0, "net_flow": 0,
        "delta_vol": 0, "count": 0,
        "_open_chg_sum": 0.0, "_day_chg_sum": 0.0,
        "_high_chg_sum": 0.0, "_low_chg_sum": 0.0, "_vol_sum": 0.0,
    })
    for s in stock_flows:
        for theme in theme_map.get(s["sid"], []):
            tvol = s["volume_total"] * (s["price"] or 0) * 1000
            t = themes[theme]
            t["flow_in"]       += s["flow_in"]
            t["flow_out"]      += s["flow_out"]
            t["net_flow"]      += s["net_flow"]
            t["delta_vol"]     += s["delta_vol"]
            t["count"]         += 1
            t["_open_chg_sum"] += s.get("open_chg_pct", 0) * tvol
            t["_day_chg_sum"]  += s.get("day_chg_pct",  0) * tvol
            t["_high_chg_sum"] += s.get("high_chg_pct", 0) * tvol
            t["_low_chg_sum"]  += s.get("low_chg_pct",  0) * tvol
            t["_vol_sum"]      += tvol

    rows = []
    for name, v in themes.items():
        vs = v["_vol_sum"]
        rows.append({
            "theme":        name,
            "open_chg_pct": (v["_open_chg_sum"] / vs) if vs > 0 else 0,
            "avg_chg_pct":  (v["_day_chg_sum"]  / vs) if vs > 0 else 0,
            "high_chg_pct": (v["_high_chg_sum"] / vs) if vs > 0 else 0,
            "low_chg_pct":  (v["_low_chg_sum"]  / vs) if vs > 0 else 0,
            "volume_total": vs,
            **{k: v[k] for k in ("flow_in", "flow_out", "net_flow", "delta_vol", "count")},
        })
    rows.sort(key=lambda x: x["avg_chg_pct"], reverse=True)
    return rows


def is_market_hours() -> bool:
    now = datetime.datetime.now(TAIPEI_TZ).time()
    return MARKET_OPEN <= now <= MARKET_CLOSE


# ── CLI 入口 ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--interval", default=300, type=int)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--no-market-check", action="store_true",
                        help="盤後測試用，略過交易時段限制")
    args = parser.parse_args()

    log = _setup_logger()

    async def _main():
        stocks    = fetch_stock_list()
        ex_chs    = [s["ex_ch"] for s in stocks]
        prev_snap = {}
        redis_cli = _get_redis_client()
        influx_writer, influx_query, influx_org = _get_influx_client()
        if influx_writer:
            log.info("[InfluxDB] 連線成功")
        else:
            log.warning("[InfluxDB] 無法連線，歷史資料不會寫入")
        theme_map   = load_themes()
        weight_map  = load_weights()
        log.info(f"[權重] 載入 {len(weight_map)} 支股票市值權重")
        # 從 Redis 恢復當日累計淨流（有日期保護：跨日或新日自動重置）
        # 若 Redis 無今日資料，以 InfluxDB 為備援（每15分鐘一份快照）
        today_str   = datetime.date.today().isoformat()
        stored_date = ""   # 初始化，避免 redis_cli 失敗時 UnboundLocalError
        cum_flows: dict[str, float] = {}
        cum_theme_flows: dict[str, float] = {}
        redis_recovered = False
        if redis_cli:
            try:
                stored_ts   = redis_cli.get("tracker:timestamp") or ""
                stored_date = stored_ts[:10]   # "YYYY-MM-DDTHH:MM:SS" → "YYYY-MM-DD"
                if stored_date != today_str:
                    log.info(f"[累計] 新交易日（{today_str}），清除舊累計資料")
                else:
                    saved = redis_cli.get("tracker:stocks")
                    if saved:
                        for s in json.loads(saved):
                            sid = s.get("sid")
                            if not sid:
                                continue
                            if s.get("cum_net_flow") is not None:
                                cum_flows[sid] = s["cum_net_flow"]
                            # 重建 prev_snap：讓首掃 delta_vol 只算增量而非全天量
                            vol   = s.get("volume_total", 0)
                            price = s.get("price")
                            if price:
                                prev_snap[sid] = {"c": sid, "n": s.get("name", ""),
                                                  "v": str(int(max(vol, 0))), "z": str(price)}
                        if cum_flows:
                            log.info(f"[累計] 從 Redis 恢復 {len(cum_flows)} 支股票")
                            redis_recovered = True
                    saved_themes = redis_cli.get("tracker:themes")
                    if saved_themes:
                        for t in json.loads(saved_themes):
                            name = t.get("theme")
                            if name and t.get("cum_net_flow") is not None:
                                cum_theme_flows[name] = t["cum_net_flow"]
                        if cum_theme_flows:
                            log.info(f"[累計] 從 Redis 恢復 {len(cum_theme_flows)} 個題材")
            except Exception as e:
                log.warning(f"[累計] Redis 恢復失敗: {e}")
        # tracker:snap 補全 prev_snap（每掃描週期更新，格式精確，避免 tracker:stocks 遺漏個股）
        if redis_cli and stored_date == today_str:
            try:
                saved_snap = redis_cli.get("tracker:snap")
                if saved_snap:
                    for sid, d in json.loads(saved_snap).items():
                        if d.get("z") and sid not in prev_snap:
                            prev_snap[sid] = {"c": sid, "n": "", "v": d.get("v", "0"), "z": d["z"]}
                    if prev_snap:
                        log.info(f"[累計] tracker:snap 補全 prev_snap，共 {len(prev_snap)} 支")
            except Exception as e:
                log.warning(f"[累計] tracker:snap 讀取失敗: {e}")
        # 備援：Redis 無今日資料時從 InfluxDB 查詢
        # 同時回補 cum_flows、cum_theme_flows 和 prev_snap（避免重啟 spike）
        if not redis_recovered and influx_query:
            log.info("[累計] Redis 無今日資料，嘗試從 InfluxDB 恢復...")
            inf_flows, inf_themes, inf_snap = _recover_from_influx(influx_query, influx_org, today_str)
            if inf_flows:
                cum_flows.update(inf_flows)
                cum_theme_flows.update(inf_themes)
                prev_snap.update(inf_snap)
                log.info(f"[累計] 從 InfluxDB 恢復 {len(inf_flows)} 支股票、"
                         f"{len(inf_themes)} 個題材、{len(inf_snap)} 筆 prev_snap")
        log.info(f"[題材] 載入 {len(set(t for ts in theme_map.values() for t in ts))} 個題材，涵蓋 {len(theme_map)} 支股票")

        is_first_scan = True   # 標記第一掃（寫 is_restart=1 到 InfluxDB）
        while True:
            # 跨日偵測：新交易日自動重置累計
            current_date = datetime.date.today().isoformat()
            if current_date != today_str:
                today_str = current_date
                cum_flows.clear()
                cum_theme_flows.clear()
                prev_snap.clear()
                log.info(f"[累計] 跨日重置（{today_str}）")

            if not args.once and not args.no_market_check and not is_market_hours():
                log.info("非交易時段，等待...")
                await asyncio.sleep(60)
                continue
            t0  = time.time()
            try:
                raw = await asyncio.wait_for(poll_all(ex_chs), timeout=POLL_TIMEOUT)
            except asyncio.TimeoutError:
                log.warning(f"[逾時] poll_all 超過 {POLL_TIMEOUT}s 未完成，跳過本輪，保留上一輪資料")
                if args.once:
                    break
                await asyncio.sleep(args.interval)
                continue
            except Exception as e:
                # 任何未預期例外（如網路連線被重置、curl_cffi 底層錯誤）都不該讓
                # process 整個崩潰——2026-10-12/13 連續多次無聲消失，根因就是這類
                # 例外沒被捕捉、一路炸穿 asyncio.run()，且 stderr 當時未被導向任何
                # 地方，完全無法追查。跳過本輪、保留上一輪資料，下一輪重試即可。
                log.warning(f"[例外] poll_all 發生未預期錯誤，跳過本輪，保留上一輪資料: {e!r}")
                if args.once:
                    break
                await asyncio.sleep(args.interval)
                continue
            now_snap = {s["c"]: s for s in raw if s.get("c")}
            flows    = [calc_flow(s, prev_snap.get(s["c"])) for s in raw if s.get("c")]

            # 任何股票只要不在上輪 prev_snap 裡，calc_flow 的 prev=None
            # → prev_vol=0 → delta_vol=全天量 → spike
            # 涵蓋：全新啟動、部分恢復、網路 timeout 後重現、當日新上市
            no_prev = [f for f in flows if f["sid"] not in prev_snap]
            for f in no_prev:
                f["delta_vol"] = 0
                f["flow_in"]   = 0
                f["flow_out"]  = 0
                f["net_flow"]  = 0
            if no_prev:
                log.info(f"[累計] 首掃/重現股票設基準（{len(no_prev)} 支）：{[f['sid'] for f in no_prev[:5]]}")

            # 累計淨流 + 指數貢獻
            for f in flows:
                sid = f["sid"]
                cum_flows[sid] = cum_flows.get(sid, 0.0) + f["net_flow"]
                f["cum_net_flow"]    = cum_flows[sid]
                w = weight_map.get(sid, 0.0)
                f["index_contrib"] = f.get("day_chg_pct", 0) * w / 100  # 單位：指數%

            sectors  = aggregate_sectors(flows)
            themes   = aggregate_themes(flows, theme_map)
            elapsed  = time.time() - t0

            # 題材累計淨流
            for t in themes:
                name = t["theme"]
                cum_theme_flows[name] = cum_theme_flows.get(name, 0.0) + t["net_flow"]
                t["cum_net_flow"] = cum_theme_flows[name]

            # 寫入 Redis（用於 GUI 讀取）
            if redis_cli:
                try:
                    redis_cli.set("tracker:stocks", json.dumps([
                        {k: (round(v, 2) if k == "price" else v) for k, v in s.items()
                         if v is not None}
                        for s in flows
                    ], ensure_ascii=False))
                    redis_cli.set("tracker:sectors", json.dumps([
                        {k: (round(v, 4) if isinstance(v, float) else v)
                         for k, v in s.items()} for s in sectors
                    ], ensure_ascii=False))
                    redis_cli.set("tracker:themes", json.dumps([
                        {k: (round(v, 4) if isinstance(v, float) else v)
                         for k, v in t.items()} for t in themes
                    ], ensure_ascii=False))
                    redis_cli.set("tracker:timestamp", datetime.datetime.now(TAIPEI_TZ).isoformat())
                except Exception as e:
                    log.warning(f"Redis 寫入失敗: {e}")
            
            if influx_writer:
                try:
                    write_to_influx(influx_writer, influx_org, flows, themes,
                                    datetime.datetime.now(datetime.timezone.utc),
                                    is_restart=is_first_scan)
                    is_first_scan = False
                except Exception as e:
                    log.warning(f"InfluxDB 寫入失敗: {e}")

            log.info(f"{len(flows)} 支  耗時 {elapsed:.1f}s  interval={args.interval}s")
            for sec in sectors[:10]:
                log.info(f"  [產業] {sec['industry']:<10} 淨流 {sec['net_flow']/1e4:+,.0f} 萬")
            for thm in themes[:10]:
                log.info(f"  [題材] {thm['theme']:<10} 淨流 {thm['net_flow']/1e4:+,.0f} 萬  漲跌 {thm['avg_chg_pct']:+.2f}%")
            # update 而非直接替換：保留本輪未回應股票的舊基準（API 部分失敗時不清空）
            # 同時清理 v="-" 或無效量值，避免下輪 prev_vol=0 造成 spike
            for sid, s in now_snap.items():
                v = _f(s.get("v")) or 0
                prev_snap[sid] = {**s, "v": str(int(max(v, 0)))}
            # 把最新 volume/price 存到 Redis，供重啟時精確還原 prev_snap
            # 用獨立 key 避免依賴 tracker:stocks 的重建（後者可能遺漏某掃描週期的個股）
            if redis_cli:
                try:
                    snap_min = {
                        sid: {"v": str(int(_f(s.get("v")) or 0)), "z": s.get("z", "")}
                        for sid, s in now_snap.items() if s.get("z")
                    }
                    redis_cli.set("tracker:snap", json.dumps(snap_min, ensure_ascii=False))
                except Exception:
                    pass
            if args.once:
                break
            await asyncio.sleep(args.interval)

    asyncio.run(_main())
