# -*- coding: utf-8 -*-
"""
早盤訊號模組 — 每掃描週期更新，寫入 Redis tracker:morning

訊號列表：
  breadth        多點開花率（正流向股比例）+ 集中度（拉積盤警示）
  vol_ratio      量比 Top N（今日量 / 20日同時段均量）
  rs_laggards    RS 強勢股但開盤未跟上（潛在補漲）
  sector_laggards 熱門族群內落後股

獨立執行（測試用）：
    .venv/Scripts/python.exe monitor/morning_signal.py
"""

import configparser, datetime, json, logging, os
from pathlib import Path

log = logging.getLogger("morning_signal")

# ── 設定路徑 ──────────────────────────────────────────────────────────────────
# cfg.ini 在 crawler 專案下，支援環境變數覆蓋
_CRAWLER_ROOT = Path(os.getenv(
    "CRAWLER_ROOT",
    r"D:\Side_projects\stocks\stock_crawler\codes\tw_stock\tw_crawl"
))
_CFG_PATH = _CRAWLER_ROOT / "configs" / "cfg.ini"
_RS_DIR   = _CRAWLER_ROOT / "results" / "rs"

MARKET_OPEN   = datetime.time(9, 0)
MARKET_CLOSE  = datetime.time(13, 30)
_DAY_SECS     = (13 * 3600 + 30 * 60) - (9 * 3600)   # 4.5h = 16200s

FUTURES_OPEN  = datetime.time(8, 45)
FUTURES_CLOSE = datetime.time(13, 45)
_FUT_DAY_SECS = (13 * 3600 + 45 * 60) - (8 * 3600 + 45 * 60)  # 5h = 18000s


# ── 資料庫連線 ────────────────────────────────────────────────────────────────

def _pg_connect():
    host = os.getenv("PG_HOST")
    user = os.getenv("PG_USER")
    pwd  = os.getenv("PG_PASS")

    if not (host and user and pwd) and _CFG_PATH.exists():
        cfg = configparser.ConfigParser()
        cfg.read(_CFG_PATH)
        host = host or cfg.get("Account",          "host",     fallback="localhost")
        user = user or cfg.get("Postgres_Account", "user",     fallback="postgres")
        pwd  = pwd  or cfg.get("Postgres_Account", "password", fallback="")

    import psycopg2
    conn = psycopg2.connect(
        dbname="postgres", user=user, password=pwd,
        host=host, port=5432, connect_timeout=10
    )
    with conn.cursor() as _c:
        _c.execute("SET statement_timeout = '30s'")
    conn.commit()
    return conn


def _pg_connect_no_timeout():
    """PostgreSQL 連線，不設 statement_timeout（用於背景重量級查詢）"""
    host = os.getenv("PG_HOST")
    user = os.getenv("PG_USER")
    pwd  = os.getenv("PG_PASS")
    if not (host and user and pwd) and _CFG_PATH.exists():
        cfg = configparser.ConfigParser()
        cfg.read(_CFG_PATH)
        host = host or cfg.get("Account",          "host",     fallback="localhost")
        user = user or cfg.get("Postgres_Account", "user",     fallback="postgres")
        pwd  = pwd  or cfg.get("Postgres_Account", "password", fallback="")
    import psycopg2
    return psycopg2.connect(
        dbname="postgres", user=user, password=pwd,
        host=host, port=5432, connect_timeout=10
    )


def _mysql_connect():
    """MySQL stock_db（institutional_stock_table 等在此）"""
    host = os.getenv("MYSQL_HOST", "127.0.0.1")
    user = os.getenv("MYSQL_USER", "root")
    pwd  = os.getenv("MYSQL_PASS", "root")
    port = int(os.getenv("MYSQL_PORT", "3306"))

    if _CFG_PATH.exists():
        cfg = configparser.ConfigParser()
        cfg.read(_CFG_PATH)
        host = os.getenv("MYSQL_HOST") or cfg.get("MySQL_Account", "host",     fallback=host)
        user = os.getenv("MYSQL_USER") or cfg.get("MySQL_Account", "user",     fallback=user)
        pwd  = os.getenv("MYSQL_PASS") or cfg.get("MySQL_Account", "password", fallback=pwd)

    import pymysql
    return pymysql.connect(
        host=host, port=port, user=user, password=pwd,
        database="stock_db", charset="utf8mb4",
        connect_timeout=10, cursorclass=pymysql.cursors.Cursor,
    )


# ── RS 載入 ───────────────────────────────────────────────────────────────────

def _load_rs(date_str: str | None = None) -> dict[str, dict]:
    """載入最新 RS CSV，回傳 {stock_id: {rank, rs_vs_tsea}}"""
    if date_str:
        path = _RS_DIR / f"rs_{date_str.replace('-','')}.csv"
    else:
        paths = sorted(_RS_DIR.glob("rs_*.csv"))
        path  = paths[-1] if paths else None

    if not path or not path.exists():
        log.warning(f"[RS] 找不到 RS 檔案: {path}")
        return {}

    import csv
    result = {}
    with open(path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            sid = row.get("stock_id", "").strip()
            if sid:
                result[sid] = {
                    "rank":       int(row.get("rank", 9999)),
                    "rs_vs_tsea": float(row.get("rs_vs_tsea") or 0),
                }
    log.info(f"[RS] 載入 {len(result)} 支，來源 {path.name}")
    return result


# ── PostgreSQL 查詢 ────────────────────────────────────────────────────────────

def _query_vol_avg(cur, lookback_days: int = 20) -> dict[str, float]:
    """
    20 日平均全日成交量（日K）
    回傳 {stock_id: avg_daily_vol}
    """
    sql = """
        SELECT stock_id, AVG(vol)::float
        FROM   daily_kbar_table
        WHERE  trade_date >= CURRENT_DATE - %s
          AND  trade_date <  CURRENT_DATE
          AND  vol > 0
        GROUP  BY stock_id
        HAVING COUNT(*) >= 10
    """
    cur.execute(sql, (lookback_days,))
    return {row[0]: row[1] for row in cur.fetchall()}



_FUT_VOL_CACHE_KEY  = "cache:fut_vol_avg"
_FUT_VOL_LOOKBACK   = 20


def _query_fut_vol_avg(cur, spot_ids: list[str],
                       lookback_days: int = _FUT_VOL_LOOKBACK) -> dict[str, float]:
    """
    指定代碼的股期 N 日平均日成交量（含日夜盤）。
    fut_1min_table.stock_id 即現貨代碼（spot_id）。
    使用 stock_id = ANY(list) 讓 PG 對每支做索引 range scan，速度快。
    """
    if not spot_ids:
        return {}
    cur.execute("""
        SELECT stock_id, AVG(daily_vol)::float
        FROM (
            SELECT stock_id,
                   DATE(tick_time) AS d,
                   SUM(vol) AS daily_vol
            FROM   fut_1min_table
            WHERE  stock_id = ANY(%s)
              AND  tick_time >= CURRENT_DATE - %s
              AND  tick_time <  CURRENT_DATE
            GROUP  BY stock_id, d
            HAVING SUM(vol) > 0
        ) sub
        GROUP  BY stock_id
        HAVING COUNT(*) >= 3
    """, (spot_ids, lookback_days + 7))
    return {row[0]: row[1] for row in cur.fetchall()}


def _query_fut_daily_vol(cur, spot_ids: list[str],
                         since: datetime.date) -> dict[str, list[float]]:
    """
    查詢 since 之後各股期每日成交量，用於滾動更新。
    回傳 {spot_id: [vol_day1, vol_day2, ...]}（按日期排序）
    """
    if not spot_ids:
        return {}
    cur.execute("""
        SELECT stock_id, DATE(tick_time) AS d, SUM(vol)
        FROM   fut_1min_table
        WHERE  stock_id = ANY(%s)
          AND  tick_time >= %s
          AND  tick_time <  CURRENT_DATE
        GROUP  BY stock_id, d
        ORDER  BY d
    """, (spot_ids, since))
    result: dict[str, list[float]] = {}
    for sid, _, vol in cur.fetchall():
        result.setdefault(sid, []).append(float(vol))
    return result


def _query_fut_vol_avg_full(cur,
                            lookback_days: int = _FUT_VOL_LOOKBACK) -> dict[str, float]:
    """
    全量重建：先取近3日所有股期代碼，再批次查詢 N 日均量。
    fut_1min_table.stock_id 直接是現貨代碼（如 '2344'、'TX'），與 taifex_fetcher 的 spot_id 一致。
    耗時數分鐘，應從背景執行緒呼叫（使用 _pg_connect_no_timeout 連線）。
    """
    cur.execute("""
        SELECT DISTINCT stock_id FROM fut_1min_table
        WHERE  tick_time >= CURRENT_DATE - 3
    """)
    codes = [r[0] for r in cur.fetchall()]
    return _query_fut_vol_avg(cur, codes, lookback_days)


def _query_inst_yesterday(cur) -> dict[str, dict]:
    """
    昨日個股三大法人（取最新一個交易日）
    回傳 {stock_id: {foreign_net, trust_net, total_net}}
    """
    sql = """
        SELECT stock_id, foreign_net, trust_net, total_net
        FROM   institutional_stock_table
        WHERE  date_of_data = (
                   SELECT MAX(date_of_data) FROM institutional_stock_table
               )
    """
    cur.execute(sql)
    return {
        row[0]: {"foreign_net": row[1], "trust_net": row[2], "total_net": row[3]}
        for row in cur.fetchall()
    }


# ── 即時訊號計算 ──────────────────────────────────────────────────────────────

_BREADTH_MIN_FLOW = 10_000_000   # 200萬：過濾無活動股

def _calc_breadth(stocks: list[dict]) -> dict:
    # 只計算有累計流量且絕對值超過門檻的股票，排除無活動小型股
    valid = [s for s in stocks
             if s.get("price") and abs(s.get("cum_net_flow", 0)) >= _BREADTH_MIN_FLOW]
    if not valid:
        return {}

    pos_flow = sum(s["cum_net_flow"] for s in valid if s["cum_net_flow"] > 0)
    neg_flow = sum(s["cum_net_flow"] for s in valid if s["cum_net_flow"] < 0)
    total_f  = pos_flow + abs(neg_flow)

    # flow-weighted 廣度：正流量佔比（非等權股數比）
    positive_pct = round(pos_flow / total_f, 3) if total_f > 0 else 0
    negative_pct = round(abs(neg_flow) / total_f, 3) if total_f > 0 else 0

    flows  = [abs(s["cum_net_flow"]) for s in valid]
    top3_f = sum(sorted(flows, reverse=True)[:3])
    conc   = round(top3_f / total_f, 3) if total_f > 0 else 0

    alert = None
    if conc > 0.6 and total_f > 0:
        top3  = sorted(valid, key=lambda s: abs(s["cum_net_flow"]), reverse=True)[:3]
        alert = "拉積盤 " + " ".join(s["name"] for s in top3)

    return {
        "positive_pct":  positive_pct,
        "negative_pct":  negative_pct,
        "concentration": conc,
        "alert":         alert,
        "active_count":  len(valid),
    }


def _calc_vol_ratio(stocks: list[dict], vol_avg: dict[str, float],
                    futures_map: dict[str, dict] | None = None,
                    top_n: int = 20) -> list[dict]:
    """
    量比 = 今日累計量 / (均量 × 今日時間佔比)，取偏離最大的 top_n
    futures_map: {spot_id: taifex_fetcher 的 dict}，有資料時附加股期欄位
    """
    now = datetime.datetime.now().time()
    elapsed = max(
        (datetime.datetime.combine(datetime.date.today(), now) -
         datetime.datetime.combine(datetime.date.today(), MARKET_OPEN)).total_seconds(),
        60
    )
    time_ratio = min(elapsed / _DAY_SECS, 1.0)

    rows = []
    for s in stocks:
        sid     = s.get("sid", "")
        avg     = vol_avg.get(sid, 0)
        vol_now = s.get("volume_total", 0) or 0
        if avg < 1000 or vol_now <= 0:
            continue
        expected = avg * time_ratio
        ratio    = round(vol_now / expected, 2)
        row = {
            "sid":       sid,
            "name":      s.get("name", ""),
            "vol_ratio": ratio,
            "vol_today": int(vol_now),
            "vol_avg":   int(avg),
        }
        # 附加股期資料（若有）
        if futures_map:
            fut = futures_map.get(sid)
            if fut:
                row["fut_vol"]      = int(fut.get("volume") or 0)
                row["fut_diff_pct"] = fut.get("diff_pct")
                row["fut_price"]    = fut.get("price")
        rows.append(row)

    rows.sort(key=lambda r: abs(r["vol_ratio"] - 1), reverse=True)
    return rows[:top_n]


def _calc_rs_laggards(stocks: list[dict], rs_data: dict[str, dict],
                      rs_threshold: int = 50,
                      open_chg_threshold: float = 1.0) -> list[dict]:
    """
    RS 強勢股（rank <= rs_threshold）但今日開盤漲幅 < open_chg_threshold %
    → 尚未跟上，潛在補漲候選
    """
    rows = []
    for s in stocks:
        sid  = s.get("sid", "")
        rs   = rs_data.get(sid)
        if not rs or rs["rank"] > rs_threshold:
            continue
        open_chg = s.get("open_chg_pct", 0) or 0
        day_chg  = s.get("day_chg_pct",  0) or 0
        rows.append({
            "sid":          sid,
            "name":         s.get("name", ""),
            "rs_rank":      rs["rank"],
            "rs_vs_tsea":   round(rs["rs_vs_tsea"], 4),
            "open_chg_pct": round(open_chg, 2),
            "day_chg_pct":  round(day_chg,  2),
        })

    # 開盤漲幅最小的排前面（落後最多的優先）
    rows.sort(key=lambda r: r["open_chg_pct"])
    return [r for r in rows if r["open_chg_pct"] < open_chg_threshold]


def _calc_sector_laggards(stocks: list[dict], themes: list[dict],
                          sector_chg_threshold: float = 1.5,
                          lag_ratio: float = 0.35) -> list[dict]:
    """
    熱門族群（avg_chg_pct > threshold）內，漲幅 < 族群均值 × lag_ratio 的個股
    → 族群牆落後補漲機會
    """
    sid_to_stock = {s["sid"]: s for s in stocks if s.get("sid")}
    rows = []
    for theme in themes:
        avg_chg = theme.get("avg_chg_pct", 0) or 0
        if avg_chg < sector_chg_threshold:
            continue
        threshold = avg_chg * lag_ratio
        # 族群成員需從 themes_file 反查；這裡用 scanner 的 theme 統計近似：
        # 取 day_chg_pct 遠低於族群均值的個股（需要 GUI 端有 theme→sids 對應）
        # 先記錄族群本身，GUI 端可用 _theme_stocks 反查
        rows.append({
            "theme":        theme["theme"],
            "theme_avg_chg": round(avg_chg, 2),
            "lag_threshold": round(threshold, 2),
        })
    return rows


def _calc_inst_leaders(stocks: list[dict], inst_data: dict[str, dict],
                       top_n: int = 10) -> list[dict]:
    """昨日外資 + 投信同向買超，今日量比又高的強勢股"""
    rows = []
    for s in stocks:
        sid  = s.get("sid", "")
        if sid.startswith("0"):   # 過濾 ETF（0050、00878 等）
            continue
        inst = inst_data.get(sid)
        if not inst:
            continue
        foreign = inst.get("foreign_net", 0) or 0
        trust   = inst.get("trust_net",   0) or 0
        if foreign > 0 and trust > 0:
            rows.append({
                "sid":         sid,
                "name":        s.get("name", ""),
                "foreign_net": foreign,
                "trust_net":   trust,
                "day_chg_pct": round(s.get("day_chg_pct", 0), 2),
                "cum_net_flow": s.get("cum_net_flow", 0),
            })
    rows.sort(key=lambda r: r["foreign_net"] + r["trust_net"] * 2, reverse=True)
    return rows[:top_n]


def _calc_fut_vol_ratio(futures_map: dict[str, dict], fut_vol_avg: dict[str, float],
                        top_n: int = 10) -> list[dict]:
    """
    股期量比 = 期貨今日累計量 / (fut_1min_table 20日均量 × 今日日盤時間佔比)
    fut_1min_table.stock_id 即現貨代碼（spot_id），兩邊格式一致，直接查表即可。
    """
    now = datetime.datetime.now().time()
    elapsed = max(
        (datetime.datetime.combine(datetime.date.today(), now) -
         datetime.datetime.combine(datetime.date.today(), FUTURES_OPEN)).total_seconds(),
        60
    )
    time_ratio = min(elapsed / _FUT_DAY_SECS, 1.0)

    rows = []
    for spot_id, f in futures_map.items():
        avg       = fut_vol_avg.get(spot_id, 0)
        vol_today = f.get("volume") or 0
        if avg <= 0 or vol_today <= 0:
            continue
        ratio = round(vol_today / (avg * time_ratio), 2)
        rows.append({
            "sid":       spot_id,
            "name":      f.get("name", ""),
            "fut_ratio": ratio,
            "fut_vol":   int(vol_today),
            "fut_avg":   int(avg),
            "diff_pct":  f.get("diff_pct"),
            "price":     f.get("price"),
        })

    rows.sort(key=lambda r: r["fut_ratio"], reverse=True)
    return rows[:top_n]


# ── 主類別 ────────────────────────────────────────────────────────────────────

class MorningSignal:
    """
    用法（在 scanner 主迴圈末尾呼叫）：
        ms = MorningSignal(redis_cli)
        ms.update(flows, themes)
    """

    def __init__(self, redis_cli):
        self.redis_cli  = redis_cli
        self._vol_avg:     dict[str, float] = {}
        self._fut_vol_avg: dict[str, float] = {}
        self._rs_data:     dict[str, dict]  = {}
        self._inst_data:   dict[str, dict]  = {}
        self._init_date:   str | None       = None
        self._influx_write_api = None
        self._influx_org       = None

    def _init_influx(self):
        if self._influx_write_api is not None:
            return
        try:
            from influxdb_client import InfluxDBClient
            from influxdb_client.client.write_api import SYNCHRONOUS
            url   = os.getenv("INFLUX_URL",   "http://localhost:8086")
            token = os.getenv("INFLUX_TOKEN", "mytoken123")
            org   = os.getenv("INFLUX_ORG",   "stocks")
            client = InfluxDBClient(url=url, token=token, org=org)
            self._influx_write_api = client.write_api(write_options=SYNCHRONOUS)
            self._influx_org       = org
            log.info("[早盤] InfluxDB 連線成功")
        except Exception as e:
            log.warning(f"[早盤] InfluxDB 連線失敗，廣度指標不會寫入: {e}")
            self._influx_write_api = False  # 標記已嘗試，不重試

    def _write_breadth_to_influx(self, breadth: dict, ts):
        if not breadth:
            return
        self._init_influx()
        if not self._influx_write_api:
            return
        try:
            from influxdb_client import Point
            bucket = os.getenv("INFLUX_BUCKET", "intraday")
            p = (
                Point("morning_breadth")
                .field("positive_pct",  breadth.get("positive_pct",  0.0))
                .field("negative_pct",  breadth.get("negative_pct",  0.0))
                .field("concentration", breadth.get("concentration", 0.0))
                .field("active_count",  breadth.get("active_count",  0))
                .time(ts)
            )
            self._influx_write_api.write(bucket=bucket, org=self._influx_org, record=p)
        except Exception as e:
            log.warning(f"[早盤] InfluxDB 廣度寫入失敗: {e}")

    # ── 股期均量快取 ──────────────────────────────────────────────────────────

    def _load_fut_vol_avg(self):
        """
        從 Redis 快取讀取股期均量；若快取過期則滾動更新；若無快取則背景重建。
        morning_runner 不阻塞：背景重建完成前 _fut_vol_avg 保持 {}。

        cache["date"] = 快取內資料的最後日期（= 建立當日的前一日，因 tick_time < CURRENT_DATE）
        """
        import threading
        yesterday = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
        cache: dict = {}
        if self.redis_cli:
            try:
                cj = self.redis_cli.get(_FUT_VOL_CACHE_KEY)
                if cj:
                    cache = json.loads(cj)
            except Exception:
                pass

        if cache.get("avg"):
            self._fut_vol_avg = cache["avg"]
            cache_date = cache.get("date", "")
            if cache_date < yesterday:
                # 快取資料落後超過1天 → 滾動補齊
                self._roll_fut_vol_avg(cache)
            log.info(f"[早盤] 股期均量快取載入: {len(self._fut_vol_avg)} 支")
        else:
            log.info("[早盤] 股期均量快取不存在，背景重建中（首次需 1-3 分鐘）...")
            threading.Thread(target=self._rebuild_fut_vol_cache, daemon=True).start()

    def _roll_fut_vol_avg(self, cache: dict):
        """
        補齊快取缺少的交易日：查詢 cache_date+1 到昨日的成交量，滾動更新均量。
        只涉及已快取的股期代碼 × 少數天數，速度快。
        cache["date"] 語義：資料最後涵蓋日（inclusive）。
        """
        try:
            cache_date = cache.get("date", "")
            old_avg    = cache.get("avg", {})
            codes = list(old_avg.keys())
            if not codes or not cache_date:
                return
            since     = datetime.date.fromisoformat(cache_date) + datetime.timedelta(days=1)
            yesterday = datetime.date.today() - datetime.timedelta(days=1)
            if since > yesterday:
                return  # 沒有更新日期的必要

            conn = _pg_connect()
            cur  = conn.cursor()
            new_days = _query_fut_daily_vol(cur, codes, since)
            cur.close(); conn.close()

            n = _FUT_VOL_LOOKBACK
            updated = dict(old_avg)
            for sid, vols in new_days.items():
                if sid in updated:
                    for vol in vols:
                        updated[sid] = (updated[sid] * (n - 1) + vol) / n

            self._fut_vol_avg = updated
            new_cache_date = yesterday.isoformat()
            if self.redis_cli:
                self.redis_cli.set(_FUT_VOL_CACHE_KEY,
                                   json.dumps({"date": new_cache_date, "avg": updated}))
            log.info(f"[早盤] 股期均量滾動更新完成: {len(updated)} 支  至 {new_cache_date}")
        except Exception as e:
            log.warning(f"[早盤] 股期均量滾動更新失敗: {e}")

    def _rebuild_fut_vol_cache(self):
        """
        背景執行緒：全量重建所有 %F00 股期均量並寫入 Redis 快取。
        使用無 statement_timeout 的連線；完成後即時更新 _fut_vol_avg。
        cache["date"] = 昨日（資料最後涵蓋日）。
        """
        try:
            conn = _pg_connect_no_timeout()
            cur  = conn.cursor()
            result = _query_fut_vol_avg_full(cur)
            cur.close(); conn.close()
            yesterday = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
            if result and self.redis_cli:
                self.redis_cli.set(_FUT_VOL_CACHE_KEY,
                                   json.dumps({"date": yesterday, "avg": result}))
            self._fut_vol_avg = result
            log.info(f"[早盤] 股期均量背景重建完成: {len(result)} 支  至 {yesterday}")
        except Exception as e:
            log.warning(f"[早盤] 股期均量背景重建失敗: {e}")

    # ── 每日初始化（首掃或跨日自動觸發）──────────────────────────────────────

    def _load_context(self):
        today = datetime.date.today().isoformat()
        log.info("[早盤] 載入每日背景資料...")

        # RS
        self._rs_data = _load_rs()

        # PostgreSQL 現股均量（快速查詢）
        try:
            conn = _pg_connect()
            cur  = conn.cursor()
            self._vol_avg = _query_vol_avg(cur)
            cur.close(); conn.close()
            log.info(f"[早盤] 現股均量 {len(self._vol_avg)} 支")
        except Exception as e:
            log.warning(f"[早盤] PostgreSQL 現股均量載入失敗: {e}")

        # 股期均量：從 Redis 快取讀取，必要時滾動更新或背景重建
        self._load_fut_vol_avg()

        # MySQL（institutional_stock_table）
        try:
            mconn = _mysql_connect()
            mcur  = mconn.cursor()
            self._inst_data = _query_inst_yesterday(mcur)
            mcur.close(); mconn.close()
            log.info(f"[早盤] 法人昨日 {len(self._inst_data)} 支")
        except Exception as e:
            log.warning(f"[早盤] MySQL 法人資料載入失敗: {e}")

        # 寫入 Redis，讓 GUI 可以獨立讀取法人資訊
        if self.redis_cli:
            try:
                self.redis_cli.set("morning:context", json.dumps({
                    "date":       today,
                    "rs_count":   len(self._rs_data),
                    "vol_avg_count": len(self._vol_avg),
                    "inst_top_foreign": sorted(
                        [{"sid": k, "foreign_net": v["foreign_net"], "trust_net": v["trust_net"]}
                         for k, v in self._inst_data.items() if v["foreign_net"] > 0],
                        key=lambda x: x["foreign_net"], reverse=True
                    )[:20],
                    "inst_top_trust": sorted(
                        [{"sid": k, "trust_net": v["trust_net"]}
                         for k, v in self._inst_data.items() if v["trust_net"] > 0],
                        key=lambda x: x["trust_net"], reverse=True
                    )[:20],
                }, ensure_ascii=False))
            except Exception as e:
                log.warning(f"[早盤] Redis morning:context 寫入失敗: {e}")

        self._init_date = today

    def _ensure_init(self):
        today = datetime.date.today().isoformat()
        if self._init_date != today:
            self._load_context()

    # ── 每掃描週期更新 ─────────────────────────────────────────────────────────

    def update(self, stocks: list[dict], themes: list[dict]):
        self._ensure_init()

        # 從 Redis 讀股期資料（taifex_fetcher 寫入）
        futures_map: dict[str, dict] = {}
        if self.redis_cli:
            try:
                fj = self.redis_cli.get("taifex:stock_futures")
                if fj:
                    for f in json.loads(fj):
                        sid = f.get("spot_id")
                        if sid:
                            futures_map[sid] = f
            except Exception as e:
                log.warning(f"[早盤] 讀取 taifex:stock_futures 失敗: {e}")

        now = datetime.datetime.now(datetime.timezone.utc)
        breadth = _calc_breadth(stocks)
        result = {
            "ts":               now.astimezone().isoformat(),
            "breadth":          breadth,
            "vol_ratio":        _calc_vol_ratio(stocks, self._vol_avg, futures_map or None),
            "fut_vol_ratio":    _calc_fut_vol_ratio(futures_map, self._fut_vol_avg),
            "rs_laggards":      _calc_rs_laggards(stocks, self._rs_data),
            "sector_laggards":  _calc_sector_laggards(stocks, themes),
            "inst_leaders":     _calc_inst_leaders(stocks, self._inst_data),
        }
        self._write_breadth_to_influx(breadth, now)

        if self.redis_cli:
            try:
                self.redis_cli.set("tracker:morning", json.dumps(result, ensure_ascii=False))
            except Exception as e:
                log.warning(f"[早盤] Redis 寫入失敗: {e}")

        return result


# ── 獨立測試 ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys, redis as redis_lib
    sys.stdout.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s",
                        datefmt="%H:%M:%S")

    r = redis_lib.Redis(host="127.0.0.1", port=6379, db=0, decode_responses=True)

    ms = MorningSignal(r)
    ms._load_context()

    print(f"\nRS 載入: {len(ms._rs_data)} 支")
    print(f"量比基準: {len(ms._vol_avg)} 支")
    print(f"法人昨日: {len(ms._inst_data)} 支")

    # 從 Redis 讀現有 tracker:stocks 測試即時訊號
    stocks_json = r.get("tracker:stocks")
    themes_json = r.get("tracker:themes")
    if stocks_json and themes_json:
        stocks = json.loads(stocks_json)
        themes = json.loads(themes_json)
        result = ms.update(stocks, themes)

        b = result["breadth"]
        print(f"\n多點開花率: {b.get('positive_pct', 0)*100:.1f}%  "
              f"集中度: {b.get('concentration', 0)*100:.1f}%")
        if b.get("alert"):
            print(f"  ⚠ {b['alert']}")

        print(f"\n量比 Top 5:")
        for r2 in result["vol_ratio"][:5]:
            print(f"  {r2['sid']} {r2['name']:<8} {r2['vol_ratio']:.1f}x "
                  f"(今{r2['vol_today']}張 / 均{r2['vol_avg']}張)")

        print(f"\nRS 強勢開盤落後 ({len(result['rs_laggards'])} 支):")
        for r2 in result["rs_laggards"][:5]:
            print(f"  #{r2['rs_rank']:3d} {r2['sid']} {r2['name']:<8} "
                  f"開{r2['open_chg_pct']:+.2f}% 現{r2['day_chg_pct']:+.2f}%")

        print(f"\n法人昨買今表現 Top 5:")
        for r2 in result["inst_leaders"][:5]:
            print(f"  {r2['sid']} {r2['name']:<8} "
                  f"外資{r2['foreign_net']:+,} 投信{r2['trust_net']:+,} "
                  f"今{r2['day_chg_pct']:+.2f}%")
    else:
        print("\n[Redis 無資料] 請先啟動 scanner 或 mock_scanner")
