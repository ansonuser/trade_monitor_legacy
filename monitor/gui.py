# -*- coding: utf-8 -*-
"""
盤中監控 GUI（混合方案 - 從 Redis 讀取數據）
使用方式:
    venv/Scripts/python.exe monitor/gui.py
    venv/Scripts/python.exe monitor/gui.py --redis-host 127.0.0.1 --redis-port 6379
    venv/Scripts/python.exe monitor/gui.py --redis-host 172.17.0.1    # Docker 從 Windows 訪問
"""
import tkinter as tk
from tkinter import ttk
import threading, time, datetime, argparse, sys, json, os, queue, gc
from pathlib import Path
import matplotlib
matplotlib.use("TkAgg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import matplotlib.dates as mdates
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from monitor.scanner import INDUSTRY_MAP, is_market_hours, load_weights, THEMES_FILE

parser = argparse.ArgumentParser()
parser.add_argument("--interval", default=60, type=int, help="GUI 更新間隔（秒）")
parser.add_argument("--redis-host", default="127.0.0.1", help="Redis 主機（預設本地）")
parser.add_argument("--redis-port", default=6379, type=int, help="Redis 埠")
parser.add_argument("--redis-db", default=0, type=int, help="Redis 資料庫")
parser.add_argument("--test", action="store_true",
                    help="測試模式：跳過交易時段限制，使用 redis-db=1（除非明確指定）")
args = parser.parse_args()
if args.test and args.redis_db == 0:
    args.redis_db = 1   # 測試模式預設用 DB=1，不污染正式資料

BG      = "#1e1e2e"
PANEL   = "#2a2a3e"
HEADER  = "#313150"
FG      = "#cdd6f4"
MUTED   = "#6c7086"
GREEN   = "#a6e3a1"
RED     = "#f38ba8"
ACCENT  = "#89b4fa"
ROW_ODD = "#252535"
ROW_EVN = "#2a2a3e"
POS_BG  = "#2e1a1a"
NEG_BG  = "#1a2e1a"

TOPN_OPTIONS = [10, 15, 20, 30]
TOPN = 20

plt.rcParams.update({
    "figure.facecolor": BG,  "axes.facecolor":  PANEL,
    "axes.edgecolor":   MUTED, "axes.labelcolor": FG,
    "xtick.color": FG, "ytick.color": FG, "text.color": FG,
    "font.family": ["Microsoft JhengHei", "Noto Sans TC", "sans-serif"],
    "font.size": 9,
})

# ── Redis 連線 ────────────────────────────────────────────────────────────

def _get_redis_client():
    """取得 Redis 連線"""
    try:
        import redis
        r = redis.Redis(
            host=args.redis_host,
            port=args.redis_port,
            db=args.redis_db,
            decode_responses=True,
            socket_connect_timeout=2
        )
        r.ping()
        return r
    except Exception as e:
        print(f"[警告] Redis 連線失敗: {e}")
        return None

def _fetch_from_redis(redis_cli):
    """從 Redis 讀取掃描結果 + 早盤訊號"""
    if not redis_cli:
        return None, None, None
    try:
        stocks_json  = redis_cli.get("tracker:stocks")
        themes_json  = redis_cli.get("tracker:themes")
        morning_json = redis_cli.get("tracker:morning")

        if not stocks_json or not themes_json:
            return None, None, None

        stocks  = json.loads(stocks_json)
        themes  = json.loads(themes_json)
        morning = json.loads(morning_json) if morning_json else None
        return stocks, themes, morning
    except Exception as e:
        print(f"[警告] Redis 讀取失敗: {e}")
        return None, None, None


class IntradayGUI:
    def __init__(self, root: tk.Tk):
        self.root      = root
        self.running        = True
        self.countdown      = args.interval
        self._countdown_job = None
        self.topn        = tk.IntVar(value=TOPN)
        self.weight_map  = load_weights()   # sid → 市值佔大盤%
        self._last_flows:   list[dict] = []   # 最新一輪個股資料快取
        self._last_themes:  list[dict] = []   # 最新一輪題材資料快取
        self._last_morning: dict | None = None  # tracker:morning 快取
        self._influx_query = self._init_influx_query()
        # {theme_name: [sid, ...]} 反查表
        self._theme_stocks: dict[str, list[str]] = (
            json.loads(THEMES_FILE.read_text(encoding="utf-8"))
            if THEMES_FILE.exists() else {}
        )
        self.redis_cli = _get_redis_client()
        
        if not self.redis_cli:
            print("[錯誤] 無法連接 Redis！請確保：")
            print(f"      - Redis 運行在 {args.redis_host}:{args.redis_port}")
            print(f"      - 混合方案：`docker-compose up -d redis`")
            sys.exit(1)

        root.title("盤中監控（Docker 混合方案）" + (" [TEST DB=1]" if args.test else ""))
        root.configure(bg=BG)
        root.geometry("1200x740")
        root.minsize(900, 520)

        self._q: queue.Queue = queue.Queue()
        self._build_ui()
        self._start_countdown()
        self._poll_queue()
        self._scan_thread = threading.Thread(target=self._scan_loop, daemon=True)
        self._scan_thread.start()

    # ── InfluxDB ──────────────────────────────────────────────────────────────

    def _init_influx_query(self):
        try:
            from influxdb_client import InfluxDBClient
            url   = os.getenv("INFLUX_URL",   "http://localhost:8086")
            token = os.getenv("INFLUX_TOKEN", "mytoken123")
            org   = os.getenv("INFLUX_ORG",   "stocks")
            client = InfluxDBClient(url=url, token=token, org=org)
            return client.query_api(), org
        except Exception:
            return None, None

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        bar = tk.Frame(self.root, bg=BG)
        bar.pack(fill="x", padx=12, pady=(8, 2))
        self.lbl_status = tk.Label(bar, text="初始化...", bg=BG,
                                   fg=ACCENT, font=("Consolas", 11))
        self.lbl_status.pack(side="left")

        tk.Label(bar, text="前N:", bg=BG, fg=MUTED,
                 font=("Consolas", 10)).pack(side="left", padx=(20, 2))
        for n in TOPN_OPTIONS:
            tk.Radiobutton(bar, text=str(n), variable=self.topn, value=n,
                           bg=BG, fg=FG, selectcolor=HEADER,
                           activebackground=BG, font=("Consolas", 10)
                           ).pack(side="left", padx=2)
        self.lbl_clock = tk.Label(bar, text="", bg=BG,
                                  fg=MUTED, font=("Consolas", 11))
        self.lbl_clock.pack(side="right")

        btn_cfg = dict(bg=HEADER, fg=FG, font=("Microsoft JhengHei", 10),
                       relief="flat", padx=8, pady=2, cursor="hand2",
                       activebackground=ACCENT, activeforeground=BG)
        tk.Button(bar, text="早盤訊號", **btn_cfg,
                  command=self._open_morning_signal).pack(side="right", padx=(0, 6))
        tk.Button(bar, text="查詢個股", **btn_cfg,
                  command=self._open_stock_query).pack(side="right", padx=(0, 4))
        tk.Button(bar, text="查詢題材", **btn_cfg,
                  command=self._open_theme_query).pack(side="right", padx=(0, 4))
        tk.Button(bar, text="負流前10", **btn_cfg,
                  command=lambda: self._open_flow_rank("neg")).pack(side="right", padx=(0, 4))
        tk.Button(bar, text="正流前10", **btn_cfg,
                  command=lambda: self._open_flow_rank("pos")).pack(side="right", padx=(0, 4))

        body = tk.Frame(self.root, bg=BG)
        body.pack(fill="both", expand=True, padx=12, pady=(4, 10))
        body.columnconfigure(0, weight=5)
        body.columnconfigure(1, weight=5)
        body.rowconfigure(0, weight=3)
        body.rowconfigure(1, weight=2)

        # 左：題材日K
        lf = tk.LabelFrame(body, text=" 題材日K（開→現，下方:成交量）",
                           bg=PANEL, fg=ACCENT,
                           font=("Microsoft JhengHei", 10, "bold"),
                           bd=1, relief="groove")
        lf.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        self.fig, (self.ax, self.ax_vol) = plt.subplots(
            2, 1, figsize=(6, 7),
            gridspec_kw={"height_ratios": [4, 1], "hspace": 0.08},
        )
        self.fig.subplots_adjust(left=0.06, right=0.87, top=0.97, bottom=0.18)
        self.canvas = FigureCanvasTkAgg(self.fig, master=lf)
        self.canvas.get_tk_widget().pack(fill="both", expand=True, padx=4, pady=4)

        # 左下：前N個股K棒（依市值權重排序）
        sf = tk.LabelFrame(body, text=" 權值股 K棒（依當日指數貢獻排序）",
                           bg=PANEL, fg=ACCENT,
                           font=("Microsoft JhengHei", 12, "bold"),
                           bd=1, relief="groove")
        sf.grid(row=1, column=0, sticky="nsew", padx=(0, 6), pady=(6, 0))
        self.fig2, self.ax2 = plt.subplots(figsize=(6, 3))
        self.fig2.patch.set_facecolor(BG)
        self.fig2.subplots_adjust(left=0.10, right=0.97, top=0.97, bottom=0.22)
        self.canvas2 = FigureCanvasTkAgg(self.fig2, master=sf)
        self.canvas2.get_tk_widget().pack(fill="both", expand=True, padx=4, pady=4)

        # 右上：個股資金流
        self._rf_stocks = tk.LabelFrame(body, text=" 個股資金流 Top 50（累計）",
                           bg=PANEL, fg=ACCENT,
                           font=("Microsoft JhengHei", 10, "bold"),
                           bd=1, relief="groove")
        rf = self._rf_stocks
        rf.grid(row=0, column=1, sticky="nsew")
        self.stock_tree = self._tree(rf, [
            ("代號",    55, "w"),
            ("名稱",    90, "w"),
            ("族群",    65, "w"),
            ("現價",    62, "e"),
            ("漲跌%",   58, "e"),
            ("新增(張)", 72, "e"),
            ("累計(萬)", 80, "e"),
        ])

        # 右下：市場寬度
        lf_bw = tk.LabelFrame(body, text=" 市場寬度 ",
                              bg=PANEL, fg=ACCENT,
                              font=("Microsoft JhengHei", 10, "bold"),
                              bd=1, relief="groove")
        lf_bw.grid(row=1, column=1, sticky="nsew", pady=(6, 0))

        bw_inner = tk.Frame(lf_bw, bg=PANEL)
        bw_inner.pack(fill="both", expand=True, padx=10, pady=6)

        _lbl_cfg  = dict(bg=PANEL, font=("Microsoft JhengHei", 10))
        _val_cfg  = dict(bg=PANEL, font=("Consolas", 18, "bold"))

        for col, title in enumerate(["多點開花", "集中度", "活躍股"]):
            bw_inner.columnconfigure(col, weight=1)
            tk.Label(bw_inner, text=title, fg=MUTED, **_lbl_cfg).grid(
                row=0, column=col, pady=(2, 0))

        self._lbl_bw_pos   = tk.Label(bw_inner, text="--",   fg=FG,   **_val_cfg)
        self._lbl_bw_conc  = tk.Label(bw_inner, text="--",   fg=FG,   **_val_cfg)
        self._lbl_bw_act   = tk.Label(bw_inner, text="--",   fg=MUTED, **_val_cfg)
        self._lbl_bw_alert = tk.Label(bw_inner, text="",     fg=RED,
                                      bg=PANEL, font=("Microsoft JhengHei", 9),
                                      wraplength=320, justify="left")

        self._lbl_bw_pos .grid(row=1, column=0, pady=(0, 2))
        self._lbl_bw_conc.grid(row=1, column=1, pady=(0, 2))
        self._lbl_bw_act .grid(row=1, column=2, pady=(0, 2))
        self._lbl_bw_alert.grid(row=2, column=0, columnspan=3, sticky="w")

    def _tree(self, parent, cols) -> ttk.Treeview:
        s = ttk.Style()
        s.theme_use("clam")
        sid = f"T{id(parent)}.Treeview"
        s.configure(sid, background=PANEL, foreground=FG, fieldbackground=PANEL,
                    rowheight=25, font=("Consolas", 10))
        s.configure(f"{sid}.Heading", background=HEADER, foreground=ACCENT,
                    font=("Consolas", 10, "bold"), relief="flat")
        s.map(sid, background=[("selected", HEADER)])

        frame = tk.Frame(parent, bg=PANEL)
        frame.pack(fill="both", expand=True, padx=4, pady=4)

        tree = ttk.Treeview(frame, columns=[c[0] for c in cols],
                            show="headings", style=sid)
        for col, w, anc in cols:
            tree.heading(col, text=col)
            tree.column(col, width=w, anchor=anc, minwidth=30)

        tree.tag_configure("pos",  background=POS_BG, foreground=RED)
        tree.tag_configure("neg",  background=NEG_BG, foreground=GREEN)
        tree.tag_configure("odd",  background=ROW_ODD, foreground=FG)
        tree.tag_configure("even", background=ROW_EVN, foreground=FG)

        sb = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=sb.set)
        tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="left", fill="y")
        return tree

    # ── 倒數計時 ──────────────────────────────────────────────────────────────

    def _start_countdown(self):
        if self._countdown_job is not None:
            self.root.after_cancel(self._countdown_job)
        self.countdown = args.interval
        self._tick_countdown()

    def _tick_countdown(self):
        if not self.running:
            return
        try:
            now = datetime.datetime.now().strftime("%H:%M:%S")
            mkt = "● 交易中" if is_market_hours() else "○ 非交易"
            self.lbl_clock.config(text=f"{mkt}  {now}  (下次刷新 {self.countdown}s)")
            self.countdown = max(0, self.countdown - 1)
            self._countdown_job = self.root.after(1000, self._tick_countdown)
        except Exception:
            pass

    def _poll_queue(self):
        """主執行緒輪詢 queue，處理背景執行緒傳來的資料"""
        try:
            while True:
                msg = self._q.get_nowait()
                if msg[0] == "data":
                    _, themes, stocks, elapsed, *rest = msg
                    morning = rest[0] if rest else None
                    self._refresh(themes, stocks, elapsed, morning)
                    self._start_countdown()
                elif msg[0] == "status":
                    self.lbl_status.config(text=msg[1])
        except queue.Empty:
            pass
        if self.running:
            self.root.after(200, self._poll_queue)

    # ── 主掃描循環（從 Redis 讀取）──────────────────────────────────────────

    def _scan_loop(self):
        """背景執行緒：只做 I/O，結果放進 queue，不碰任何 tkinter 物件"""
        while self.running:
            if not args.test and not is_market_hours():
                self._q.put(("status", "非交易時段，等待開盤..."))
                for _ in range(600):
                    if not self.running:
                        return
                    time.sleep(0.1)
                continue

            t0 = time.time()
            stocks, themes, morning = _fetch_from_redis(self.redis_cli)
            elapsed = time.time() - t0

            if stocks and themes:
                self._q.put(("data", themes, stocks, elapsed, morning))
            else:
                self._q.put(("status", "⏳ 等待掃描器數據..."))

            wait = max(0, args.interval - elapsed)
            for _ in range(int(wait * 10)):
                if not self.running:
                    return
                time.sleep(0.1)

    def _refresh(self, sectors, flows, elapsed, morning=None):
        self._last_flows   = flows
        self._last_themes  = sectors
        self._last_morning = morning
        self.lbl_status.config(text=f"耗時 {elapsed:.1f}s  ·  {len(flows)} 支")
        self._draw_sector_bar(sectors)
        self._draw_stock_bar(flows)
        self._fill_stocks(flows)
        self._update_breadth_panel(morning)
        gc.collect()

    def _update_breadth_panel(self, morning: dict | None):
        if not morning:
            return
        b    = morning.get("breadth", {})
        pos  = b.get("positive_pct",  0) * 100
        conc = b.get("concentration", 0) * 100
        act  = b.get("active_count",  0)

        pos_color  = RED   if pos  >= 60 else (GREEN if pos  <= 40 else FG)
        conc_color = GREEN if conc >= 50 else FG

        self._lbl_bw_pos .config(text=f"{pos:.1f}%",  fg=pos_color)
        self._lbl_bw_conc.config(text=f"{conc:.1f}%", fg=conc_color)
        self._lbl_bw_act .config(text=f"{act}支")
        self._lbl_bw_alert.config(text=b.get("alert") or "")

    # ── 族群日K chart ─────────────────────────────────────────────────────────

    def _draw_sector_bar(self, sectors):
        active = [s for s in sectors if s["volume_total"] > 0]
        if not active:
            return

        # 取累計資金流絕對值前15，並依此排序（左強右弱）
        active = sorted(active, key=lambda s: abs(s.get("cum_net_flow", 0)), reverse=True)[:15]

        labels = [s["theme"] for s in active]
        opens  = [s["open_chg_pct"]  for s in active]  # 個股開盤價加權
        closes = [s["avg_chg_pct"]   for s in active]
        vols   = [s["volume_total"]  for s in active]

        highs = [s.get("high_chg_pct", cl) for s, cl in zip(active, closes)]
        lows  = [s.get("low_chg_pct",  cl) for s, cl in zip(active, closes)]

        x = list(range(len(labels)))

        # ── Y 軸範圍：依 closes 的 5/95 百分位 + 邊距，並確保 0 兩側都有空間 ──
        sorted_c = sorted(closes)
        n = len(sorted_c)
        p05 = sorted_c[max(0, n // TOPN)]
        p95 = sorted_c[min(n - 1, int(TOPN*0.95) * n // TOPN)]
        margin = max(abs(p95 - p05) * 0.2, 0.3)
        y_lo = min(p05, min(lows))  - margin
        y_hi = max(p95, max(highs)) + margin
        y_lo = min(y_lo, -0.5)   # 0 以下一定有負值空間
        y_hi = max(y_hi,  0.5)   # 0 以上一定有正值空間

        min_body = (y_hi - y_lo) * 0.04

        # ── K-bar bodies + wicks ──
        self.ax.clear()
        for xi, (o, c, h, l) in enumerate(zip(opens, closes, highs, lows)):
            color    = RED if c >= o else GREEN
            body_bot = min(o, c)
            body_ht  = max(abs(c - o), min_body)
            self.ax.bar(xi, body_ht, bottom=body_bot,
                        color=color, width=0.55, zorder=3)
            wick_hi = min(h, y_hi)          # clip wicks to axis range
            wick_lo = max(l, y_lo)
            if wick_hi > body_bot + body_ht:
                self.ax.vlines(xi, body_bot + body_ht, wick_hi,
                               color=color, linewidth=1, zorder=2)
            if wick_lo < body_bot:
                self.ax.vlines(xi, wick_lo, body_bot,
                               color=color, linewidth=1, zorder=2)

        self.ax.axhline(0, color=FG, linewidth=2.0, zorder=1, alpha=0.6)
        self.ax.set_xlim(-0.5, len(x) - 0.5)
        self.ax.set_ylim(y_lo, y_hi)
        self.ax.set_xticks(x)
        self.ax.tick_params(axis="x", bottom=False, labelbottom=False)  # labels on vol chart
        self.ax.yaxis.set_major_formatter(mticker.FormatStrFormatter("%+.1f%%"))
        self.ax.tick_params(axis="y", labelsize=7.5)
        self.ax.grid(axis="y", color=MUTED, linewidth=0.3, alpha=0.4, zorder=0)
        self.ax.set_ylabel("漲跌幅 %", fontsize=10)

        # ── Volume sub-chart ──
        self.ax_vol.clear()
        vol_colors = [RED if c >= o else GREEN for o, c in zip(opens, closes)]
        vols_wan   = [v / 1e8 for v in vols]
        self.ax_vol.bar(x, vols_wan, color=vol_colors, alpha=0.75, width=0.55)
        self.ax_vol.set_xlim(-0.5, len(x) - 0.5)
        self.ax_vol.set_xticks(x)
        self.ax_vol.set_xticklabels(labels, rotation=45, ha="right", fontsize=7.5)
        self.ax_vol.tick_params(axis="x", bottom=False, labelsize=7.5)
        self.ax_vol.yaxis.set_label_position("right")
        self.ax_vol.yaxis.tick_right()
        self.ax_vol.set_ylabel("億元", fontsize=10, color=MUTED)
        self.ax_vol.tick_params(axis="y", labelsize=10, labelcolor=MUTED)
        self.ax_vol.yaxis.set_major_formatter(mticker.FormatStrFormatter("%.1f"))
        self.ax_vol.set_facecolor(PANEL)

        self.canvas.draw()

    # ── 前15個股K棒 ───────────────────────────────────────────────────────────

    def _draw_stock_bar(self, flows):
        n = self.topn.get()
        valid = [f for f in flows if f.get("price") and f.get("index_contrib") is not None]
        topn = sorted(valid, key=lambda f: abs(f.get("index_contrib", 0)),
                      reverse=True)[:n]
        if not topn:
            return

        labels = [f["name"][:5] for f in topn]
        opens  = [f.get("open_chg_pct", 0) for f in topn]
        closes = [f.get("day_chg_pct",  0) for f in topn]
        highs  = [f.get("high_chg_pct", c) for f, c in zip(topn, closes)]
        lows   = [f.get("low_chg_pct",  c) for f, c in zip(topn, closes)]

        all_vals = closes + highs + lows
        margin = max(abs(max(all_vals) - min(all_vals)) * 0.2, 0.3)
        y_lo = min(min(lows)  - margin, -0.5)
        y_hi = max(max(highs) + margin,  0.5)
        min_body = (y_hi - y_lo) * 0.04   # 至少佔 Y 軸範圍 4%，漲停時仍清楚可見

        x = list(range(len(labels)))
        self.ax2.clear()
        for xi, (o, c, h, l) in enumerate(zip(opens, closes, highs, lows)):
            color    = RED if c >= o else GREEN
            body_bot = min(o, c)
            body_ht  = max(abs(c - o), min_body)
            self.ax2.bar(xi, body_ht, bottom=body_bot, color=color, width=0.55, zorder=3)
            if h > body_bot + body_ht:
                self.ax2.vlines(xi, body_bot + body_ht, min(h, y_hi), color=color, linewidth=1)
            if l < body_bot:
                self.ax2.vlines(xi, max(l, y_lo), body_bot, color=color, linewidth=1)

        self.ax2.axhline(0, color=FG, linewidth=2.0, zorder=1, alpha=0.6)
        self.ax2.set_xlim(-0.5, len(x) - 0.5)
        self.ax2.set_ylim(y_lo, y_hi)
        self.ax2.set_xticks(x)
        self.ax2.set_xticklabels(labels, rotation=45, ha="right", fontsize=9)
        self.ax2.yaxis.set_major_formatter(mticker.FormatStrFormatter("%+.1f%%"))
        self.ax2.tick_params(axis="y", labelsize=9)
        self.ax2.grid(axis="y", color=MUTED, linewidth=0.3, alpha=0.4, zorder=0)
        self.ax2.set_facecolor(PANEL)
        self.canvas2.draw()

    # ── 個股表格 ──────────────────────────────────────────────────────────────

    def _fill_stocks(self, flows):
        self.stock_tree.delete(*self.stock_tree.get_children())
        topn = sorted(flows, key=lambda x: abs(x.get("cum_net_flow", 0)),
                      reverse=True)[:50]
        for i, s in enumerate(topn):
            net    = s.get("cum_net_flow", 0) / 1e4
            chg    = s.get("day_chg_pct", s.get("chg_pct", 0))
            tag = "pos" if net > 0 else ("neg" if net < 0 else
                  ("odd" if i % 2 == 0 else "even"))
            self.stock_tree.insert("", "end", values=(
                s["sid"],
                s["name"][:7],
                INDUSTRY_MAP.get(s["industry"], s["industry"])[:5],
                f"{s['price']:.2f}" if s["price"] else "-",
                f"{chg:+.2f}%",
                f"{s['delta_vol']:,}",
                f"{net:+,.0f}",
            ), tags=(tag,))

    # ── 查詢子視窗 ───────────────────────────────────────────────────────────────

    def _popup_tree(self, title: str, cols: list, rows: list, sign_col: int = -1):
        """通用：開一個 Toplevel Treeview 視窗，sign_col 指定用哪欄判斷正負色"""
        win = tk.Toplevel(self.root)
        win.title(title)
        win.configure(bg=PANEL)
        win.geometry("560x380")
        win.resizable(True, True)
        tree = self._tree(win, cols)
        for i, vals in enumerate(rows):
            v = str(vals[sign_col])
            tag = "pos" if v.startswith("+") else ("neg" if v.startswith("-") else
                  ("odd" if i % 2 == 0 else "even"))
            tree.insert("", "end", values=vals, tags=(tag,))

    def _open_flow_rank(self, direction: str):
        """題材正/負累計資金流前10子視窗（雙擊題材開K棒）"""
        themes = self._last_themes
        if not themes:
            return
        if direction == "pos":
            top10 = sorted(themes, key=lambda t: t.get("cum_net_flow", 0), reverse=True)[:10]
            title = "題材 正資金流前10（累計）— 雙擊看K棒"
        else:
            top10 = sorted(themes, key=lambda t: t.get("cum_net_flow", 0))[:10]
            title = "題材 負資金流前10（累計）— 雙擊看K棒"

        win = tk.Toplevel(self.root)
        win.title(title)
        win.configure(bg=PANEL)
        win.geometry("480x320")
        win.resizable(True, True)

        cols = [("題材", 150, "w"), ("漲跌%", 65, "e"), ("累計(萬)", 100, "e"), ("支數", 50, "e")]
        tree = self._tree(win, cols)
        for i, t in enumerate(top10):
            net = t.get("cum_net_flow", 0) / 1e4
            v = f"{net:+,.0f}"
            tag = "pos" if net > 0 else ("neg" if net < 0 else ("odd" if i%2==0 else "even"))
            tree.insert("", "end", iid=t["theme"], values=(
                t["theme"][:18],
                f"{t.get('avg_chg_pct', 0):+.2f}%",
                v,
                str(t.get("count", 0)),
            ), tags=(tag,))

        tree.bind("<Double-1>", lambda _: self._open_theme_kbar(
            tree.item(tree.focus(), "values")[0] if tree.focus() else None
        ))
        tk.Label(win, text="雙擊題材列查看成分股K棒", bg=PANEL, fg=MUTED,
                 font=("Microsoft JhengHei", 9)).pack(side="bottom", pady=4)

    def _open_theme_kbar(self, theme_name: str | None):
        """開子視窗，畫該題材成分股的當日K棒"""
        if not theme_name:
            return
        # 找到該題材的成分股 sid 清單
        sids = set(self._theme_stocks.get(theme_name, []))
        if not sids:
            return
        flows = [f for f in self._last_flows if f["sid"] in sids and f.get("price")]
        if not flows:
            return

        flows = sorted(flows, key=lambda f: abs(f.get("cum_net_flow", 0)), reverse=True)[:10]
        flows = sorted(flows, key=lambda f: f.get("day_chg_pct", 0), reverse=True)
        labels = [f["name"][:5] for f in flows]
        opens  = [f.get("open_chg_pct", 0) for f in flows]
        closes = [f.get("day_chg_pct",  0) for f in flows]
        highs  = [f.get("high_chg_pct", c) for f, c in zip(flows, closes)]
        lows   = [f.get("low_chg_pct",  c) for f, c in zip(flows, closes)]

        win = tk.Toplevel(self.root)
        win.title(f"{theme_name} — 成分股日K")
        win.configure(bg=BG)
        win.geometry("700x420")
        win.resizable(True, True)

        cum_flows_wan = [f.get("cum_net_flow", 0) / 1e4 for f in flows]

        fig, (ax, ax_flow) = plt.subplots(
            2, 1, figsize=(7, 4),
            gridspec_kw={"height_ratios": [4, 1], "hspace": 0.08},
        )
        fig.patch.set_facecolor(BG)
        fig.subplots_adjust(left=0.09, right=0.88, top=0.93, bottom=0.22)

        all_vals = closes + highs + lows
        margin = max(abs(max(all_vals) - min(all_vals)) * 0.2, 0.3)
        y_lo = min(min(lows)  - margin, -0.5)
        y_hi = max(max(highs) + margin,  0.5)
        min_body = (y_hi - y_lo) * 0.04

        x = list(range(len(labels)))
        for xi, (o, c, h, l) in enumerate(zip(opens, closes, highs, lows)):
            color    = RED if c >= o else GREEN
            body_bot = min(o, c)
            body_ht  = max(abs(c - o), min_body)
            ax.bar(xi, body_ht, bottom=body_bot, color=color, width=0.55, zorder=3)
            if h > body_bot + body_ht:
                ax.vlines(xi, body_bot + body_ht, min(h, y_hi), color=color, linewidth=1)
            if l < body_bot:
                ax.vlines(xi, max(l, y_lo), body_bot, color=color, linewidth=1)

        ax.axhline(0, color=FG, linewidth=2.0, zorder=1, alpha=0.6)
        ax.set_xlim(-0.5, len(x) - 0.5)
        ax.set_ylim(y_lo, y_hi)
        ax.set_xticks(x)
        ax.tick_params(axis="x", bottom=False, labelbottom=False)
        ax.yaxis.set_major_formatter(mticker.FormatStrFormatter("%+.1f%%"))
        ax.tick_params(axis="y", labelsize=8)
        ax.grid(axis="y", color=MUTED, linewidth=0.3, alpha=0.4, zorder=0)
        ax.set_facecolor(PANEL)
        ax.set_title(f"{theme_name}（{len(flows)} 支）", fontsize=10, color=FG, pad=4)

        # 資金流子圖
        flow_colors = [RED if v >= 0 else GREEN for v in cum_flows_wan]
        ax_flow.bar(x, cum_flows_wan, color=flow_colors, alpha=0.8, width=0.55)
        ax_flow.axhline(0, color=FG, linewidth=1.2, alpha=0.5)
        ax_flow.set_xlim(-0.5, len(x) - 0.5)
        ax_flow.set_xticks(x)
        ax_flow.set_xticklabels(labels, rotation=45, ha="right", fontsize=8)
        ax_flow.tick_params(axis="x", bottom=False, labelsize=8)
        ax_flow.yaxis.set_label_position("right")
        ax_flow.yaxis.tick_right()
        ax_flow.set_ylabel("萬元", fontsize=9, color=MUTED)
        ax_flow.tick_params(axis="y", labelsize=8, labelcolor=MUTED)
        ax_flow.yaxis.set_major_formatter(mticker.FormatStrFormatter("%.0f"))
        ax_flow.set_facecolor(PANEL)

        canvas = FigureCanvasTkAgg(fig, master=win)
        canvas.get_tk_widget().pack(fill="both", expand=True)
        canvas.draw()
        win.protocol("WM_DELETE_WINDOW", lambda: (plt.close(fig), win.destroy()))

    def _open_theme_query(self):
        """細產業資金流查詢視窗（雙擊看成分股K棒）"""
        win = tk.Toplevel(self.root)
        win.title("細產業資金流查詢")
        win.configure(bg=PANEL)
        win.geometry("520x440")
        win.resizable(True, True)

        bar = tk.Frame(win, bg=PANEL)
        bar.pack(fill="x", padx=8, pady=6)
        tk.Label(bar, text="題材名稱：", bg=PANEL, fg=FG,
                 font=("Microsoft JhengHei", 10)).pack(side="left")
        entry = tk.Entry(bar, bg=HEADER, fg=FG, insertbackground=FG,
                         font=("Consolas", 11), width=16)
        entry.pack(side="left", padx=(4, 8))

        tree = self._tree(win, [
            ("題材",    160, "w"),
            ("漲跌%",   65, "e"),
            ("本輪(萬)", 90, "e"),
            ("累計(萬)", 90, "e"),
            ("支數",     50, "e"),
        ])

        def do_search(*_):
            q = entry.get().strip()
            themes = self._last_themes
            hits = [t for t in themes if q in t["theme"]] if q else \
                   sorted(themes, key=lambda t: abs(t.get("cum_net_flow", 0)), reverse=True)
            tree.delete(*tree.get_children())
            for i, t in enumerate(hits):
                cur  = t.get("net_flow",     0) / 1e4
                cum  = t.get("cum_net_flow",  0) / 1e4
                tag  = "pos" if cum > 0 else ("neg" if cum < 0 else
                       ("odd" if i % 2 == 0 else "even"))
                tree.insert("", "end", iid=t["theme"], values=(
                    t["theme"][:20],
                    f"{t.get('avg_chg_pct', 0):+.2f}%",
                    f"{cur:+,.0f}",
                    f"{cum:+,.0f}",
                    str(t.get("count", 0)),
                ), tags=(tag,))

        tree.bind("<Double-1>", lambda _: self._open_theme_kbar(
            tree.item(tree.focus(), "values")[0] if tree.focus() else None
        ))

        tk.Button(bar, text="搜尋", bg=ACCENT, fg=BG,
                  font=("Microsoft JhengHei", 10), relief="flat",
                  command=do_search).pack(side="left")
        tk.Label(bar, text="雙擊看成分股K棒", bg=PANEL, fg=MUTED,
                 font=("Microsoft JhengHei", 9)).pack(side="left", padx=8)
        entry.bind("<Return>", do_search)
        do_search()

    def _open_stock_query(self):
        """個股資金流查詢子視窗"""
        win = tk.Toplevel(self.root)
        win.title("個股資金流查詢")
        win.configure(bg=PANEL)
        win.geometry("560x420")
        win.resizable(True, True)

        # 搜尋列
        bar = tk.Frame(win, bg=PANEL)
        bar.pack(fill="x", padx=8, pady=6)
        tk.Label(bar, text="代號/名稱：", bg=PANEL, fg=FG,
                 font=("Microsoft JhengHei", 10)).pack(side="left")
        entry = tk.Entry(bar, bg=HEADER, fg=FG, insertbackground=FG,
                         font=("Consolas", 11), width=14)
        entry.pack(side="left", padx=(4, 8))

        tree = self._tree(win, [
            ("代號", 55, "w"), ("名稱", 90, "w"), ("族群", 70, "w"),
            ("現價", 65, "e"), ("漲跌%", 62, "e"), ("新增(張)", 75, "e"), ("累計(萬)", 90, "e"),
        ])

        def do_search(*_):
            q = entry.get().strip().lower()
            flows = self._last_flows
            if q:
                hits = [f for f in flows
                        if q in f["sid"] or q in f["name"].lower()]
            else:
                hits = sorted(flows, key=lambda f: abs(f.get("cum_net_flow", 0)), reverse=True)[:50]
            tree.delete(*tree.get_children())
            for i, f in enumerate(hits):
                chg = f.get("day_chg_pct", 0)
                net = f.get("cum_net_flow", 0) / 1e4
                tag = "pos" if net > 0 else ("neg" if net < 0 else ("odd" if i%2==0 else "even"))
                tree.insert("", "end", iid=f["sid"], values=(
                    f["sid"], f["name"][:7],
                    INDUSTRY_MAP.get(f["industry"], f["industry"])[:6],
                    f"{f['price']:.2f}" if f["price"] else "-",
                    f"{chg:+.2f}%",
                    f"{f['delta_vol']:,}",
                    f"{net:+,.0f}",
                ), tags=(tag,))

        def on_dbl(_, t=tree):
            iid = t.focus()
            if not iid:
                return
            vals = t.item(iid, "values")
            self._open_stock_timeseries(vals[0], vals[1])

        tree.bind("<Double-1>", on_dbl)

        tk.Button(bar, text="搜尋", bg=ACCENT, fg=BG,
                  font=("Microsoft JhengHei", 10), relief="flat",
                  command=do_search).pack(side="left")
        tk.Label(bar, text="雙擊列查看資金流走勢", bg=PANEL, fg=MUTED,
                 font=("Microsoft JhengHei", 9)).pack(side="left", padx=8)
        entry.bind("<Return>", do_search)
        do_search()  # 預設顯示 abs 前50

    def _open_stock_timeseries(self, sid: str, name: str):
        """個股累計資金流時序圖（從 InfluxDB 讀取當日資料）"""
        import datetime as dt
        query_api, org = self._influx_query
        bucket = os.getenv("INFLUX_BUCKET", "intraday")

        win = tk.Toplevel(self.root)
        win.title(f"{sid} {name} — 累計資金流走勢")
        win.configure(bg=BG)
        win.geometry("640x340")
        win.resizable(True, True)

        fig, ax = plt.subplots(figsize=(6.5, 3))
        fig.patch.set_facecolor(BG)
        fig.subplots_adjust(left=0.10, right=0.97, top=0.90, bottom=0.15)
        ax.set_facecolor(PANEL)
        ax.set_title(f"{sid} {name}  累計淨資金流（萬元）", fontsize=10, color=FG, pad=4)
        ax.tick_params(axis="both", labelsize=8, colors=FG)
        ax.grid(axis="y", color=MUTED, linewidth=0.3, alpha=0.4)

        canvas = FigureCanvasTkAgg(fig, master=win)
        canvas.get_tk_widget().pack(fill="both", expand=True)

        lbl = tk.Label(win, text="載入中...", bg=BG, fg=MUTED, font=("Consolas", 9))
        lbl.pack(side="bottom", pady=2)
        win.update()  # show loading state before blocking on query

        # Query runs in the main thread — InfluxDB queries are typically < 1s.
        # Background threads trigger Python GC which calls tk __del__ from wrong thread.
        times, values, err_msg = [], [], None
        if query_api is None:
            err_msg = "InfluxDB 未連線"
        else:
            today = dt.date.today().isoformat()
            flux = f'''
from(bucket: "{bucket}")
  |> range(start: {today}T00:00:00+08:00)
  |> filter(fn: (r) => r._measurement == "stock"
         and r._field == "cum_net_flow"
         and r.sid == "{sid}")
  |> sort(columns: ["_time"])
'''
            try:
                tz_tw = dt.timezone(dt.timedelta(hours=8))
                for table in query_api.query(flux, org=org):
                    for rec in table.records:
                        times.append(rec.get_time().astimezone(tz_tw).replace(tzinfo=None))
                        values.append(rec.get_value() / 1e4)
            except Exception as e:
                err_msg = str(e)

        if err_msg:
            ax.text(0.5, 0.5, f"查詢失敗\n{err_msg}", transform=ax.transAxes,
                    ha="center", color=RED, fontsize=9)
            lbl.config(text=f"錯誤: {err_msg}")
        elif not times:
            ax.text(0.5, 0.5, "今日尚無 InfluxDB 資料", transform=ax.transAxes,
                    ha="center", color=MUTED, fontsize=11)
            lbl.config(text="無資料")
        else:
            ax.plot(times, values, color=ACCENT, linewidth=1.8,
                    marker="o", markersize=4, zorder=3)
            ax.fill_between(times, values, 0,
                            where=[v >= 0 for v in values], color=RED,  alpha=0.15)
            ax.fill_between(times, values, 0,
                            where=[v <  0 for v in values], color=GREEN, alpha=0.15)
            ax.axhline(0, color=FG, linewidth=1.5, alpha=0.5)
            ax.yaxis.set_major_formatter(mticker.FormatStrFormatter("%.0f"))
            ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
            lbl.config(text=f"{len(times)} 個資料點  最新: {values[-1]:+,.0f} 萬元")
        canvas.draw()
        win.protocol("WM_DELETE_WINDOW", lambda: (plt.close(fig), win.destroy()))

    def _open_morning_signal(self):
        """早盤訊號彈出視窗：廣度 + 量比 + RS 落後 + 法人昨買（30 秒自動刷新）"""
        # 單例：已開啟就置前
        if hasattr(self, "_morning_win") and self._morning_win and self._morning_win.winfo_exists():
            self._morning_win.lift()
            return

        win = tk.Toplevel(self.root)
        self._morning_win = win
        win.title("早盤訊號")
        win.configure(bg=BG)
        win.geometry("860x540")
        win.resizable(True, True)

        # ── 廣度列 ────────────────────────────────────────────────────────────
        lf_b = tk.LabelFrame(win, text=" 市場廣度 ", bg=PANEL, fg=ACCENT,
                             font=("Microsoft JhengHei", 10, "bold"), bd=1, relief="groove")
        lf_b.pack(fill="x", padx=8, pady=(8, 4))
        breadth_lbl = tk.Label(lf_b, text="載入中...", bg=PANEL, fg=FG,
                               font=("Microsoft JhengHei", 11))
        breadth_lbl.pack(pady=5)

        # ── 主體框架 ──────────────────────────────────────────────────────────
        body = tk.Frame(win, bg=BG)
        body.pack(fill="both", expand=True, padx=8, pady=4)
        body.columnconfigure(0, weight=1)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=3)
        body.rowconfigure(1, weight=2)
        body.rowconfigure(2, weight=2)

        lf_vr = tk.LabelFrame(body, text=" 現股量比異常 Top 10 ", bg=PANEL, fg=ACCENT,
                              font=("Microsoft JhengHei", 10, "bold"), bd=1, relief="groove")
        lf_vr.grid(row=0, column=0, sticky="nsew", padx=(0, 4))
        vr_tree = self._tree(lf_vr, [("代號", 58, "w"), ("名稱", 78, "w"), ("量比", 62, "e"),
                                      ("今量(張)", 78, "e"), ("均量(張)", 78, "e")])

        lf_fvr = tk.LabelFrame(body, text=" 股期量比異常 Top 10 ", bg=PANEL, fg=ACCENT,
                               font=("Microsoft JhengHei", 10, "bold"), bd=1, relief="groove")
        lf_fvr.grid(row=1, column=0, sticky="nsew", padx=(0, 4), pady=(4, 0))
        fvr_tree = self._tree(lf_fvr, [("代號", 58, "w"), ("名稱", 78, "w"), ("期量比", 65, "e"),
                                        ("今期量", 68, "e"), ("漲跌%", 62, "e")])

        lf_rs = tk.LabelFrame(body, text=" RS強勢開盤落後（補漲候選）", bg=PANEL, fg=ACCENT,
                              font=("Microsoft JhengHei", 10, "bold"), bd=1, relief="groove")
        lf_rs.grid(row=0, column=1, rowspan=3, sticky="nsew", padx=(4, 0))
        rs_tree = self._tree(lf_rs, [("代號", 58, "w"), ("名稱", 78, "w"), ("RS排", 55, "e"),
                                     ("開盤%", 62, "e"), ("現價%", 62, "e")])

        lf_inst = tk.LabelFrame(body, text=" 法人昨買今強 Top 10 ", bg=PANEL, fg=ACCENT,
                               font=("Microsoft JhengHei", 10, "bold"), bd=1, relief="groove")
        lf_inst.grid(row=2, column=0, sticky="nsew", padx=(0, 4), pady=(4, 0))
        inst_tree = self._tree(lf_inst, [("代號", 58, "w"), ("名稱", 80, "w"),
                                          ("外資(張)", 90, "e"), ("投信(張)", 90, "e"), ("今日%", 65, "e")])

        # ── 底部時間戳 ────────────────────────────────────────────────────────
        ts_lbl = tk.Label(win, text="", bg=BG, fg=MUTED, font=("Consolas", 9))
        ts_lbl.pack(side="bottom", pady=(0, 4))

        def _fill():
            if not win.winfo_exists():
                return
            m = self._last_morning
            if not m:
                breadth_lbl.config(text="尚無資料，等待 morning_runner...")
                ts_lbl.config(text="")
                win.after(5000, _fill)
                return

            # 資料日期檢查
            data_ts  = m.get("ts", "")
            data_date = data_ts[:10]
            today_str = datetime.date.today().isoformat()
            is_stale  = bool(data_date) and data_date != today_str

            # 廣度
            b    = m.get("breadth", {})
            pos  = b.get("positive_pct",  0) * 100
            conc = b.get("concentration", 0) * 100
            act  = b.get("active_count",  0)
            alert = b.get("alert") or ""
            txt = f"多點開花 {pos:.1f}%   集中度 {conc:.1f}%   活躍股 {act} 支"
            if alert:
                txt += f"   ⚠ {alert}"
            breadth_lbl.config(text=txt)

            # 清空所有 tree
            for tree in (vr_tree, fvr_tree, rs_tree, inst_tree):
                tree.delete(*tree.get_children())

            for i, r in enumerate(m.get("vol_ratio", [])[:10]):
                fut = f"  ★{r['fut_vol']}" if r.get("fut_vol") else ""
                vr_tree.insert("", "end", values=(
                    r["sid"], (r.get("name", "") + fut)[:8],
                    f"{r['vol_ratio']:.1f}x",
                    f"{r.get('vol_today', 0):,}", f"{r.get('vol_avg', 0):,}",
                ), tags=("odd" if i % 2 == 0 else "even",))

            for i, r in enumerate(m.get("fut_vol_ratio", [])[:10]):
                diff = r.get("diff_pct") or 0
                fvr_tree.insert("", "end", values=(
                    r["sid"], r.get("name", "")[:7],
                    f"{r.get('fut_ratio', 0):.1f}x",
                    f"{r.get('fut_vol', 0):,}", f"{diff:+.2f}%",
                ), tags=("pos" if diff >= 0 else "neg",))

            for i, r in enumerate(m.get("rs_laggards", [])[:10]):
                rs_tree.insert("", "end", values=(
                    r["sid"], r.get("name", "")[:7],
                    f"#{r.get('rs_rank', 0)}",
                    f"{r.get('open_chg_pct', 0):+.2f}%",
                    f"{r.get('day_chg_pct',  0):+.2f}%",
                ), tags=("neg",))

            for i, r in enumerate(m.get("inst_leaders", [])[:10]):
                inst_tree.insert("", "end", values=(
                    r["sid"], r.get("name", "")[:7],
                    f"{r.get('foreign_net', 0):+,}",
                    f"{r.get('trust_net',   0):+,}",
                    f"{r.get('day_chg_pct', 0):+.2f}%",
                ), tags=("pos",))

            if is_stale:
                ts_lbl.config(text=f"⚠ 昨日資料（{data_ts[:19]}），等待今日更新...", fg=GREEN)
            else:
                ts_lbl.config(text=f"資料時間: {data_ts[:19]}  （每 30 秒自動刷新）", fg=MUTED)
            win.after(30000, _fill)

        _fill()

    def on_close(self):
        self.running = False
        if self._scan_thread and self._scan_thread.is_alive():
            self._scan_thread.join(timeout=2.0)
        plt.close("all")
        # Force GC while Tk is still alive so matplotlib cycles (FigureCanvasTkAgg,
        # PhotoImage, Variable) are collected here rather than after root.destroy().
        self.topn = None
        gc.collect()
        self.root.destroy()


def main():
    root = tk.Tk()
    app  = IntradayGUI(root)
    root.protocol("WM_DELETE_WINDOW", app.on_close)
    root.mainloop()


if __name__ == "__main__":
    main()
