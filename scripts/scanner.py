import pandas as pd
import numpy as np
from sqlalchemy.orm import Session
from tracker.core.database import SessionLocal
from tracker.core.db_models import DailyBar
from loguru import logger
from datetime import datetime, timedelta

class VCPScanner:
    def __init__(self):
        self.db: Session = SessionLocal()

    def get_stock_data(self, symbol: str, limit: int = 300):
        """獲取最近 N 天的資料"""
        query = self.db.query(DailyBar).filter(DailyBar.symbol == symbol).order_by(DailyBar.timestamp.desc()).limit(limit)
        df = pd.read_sql(query.statement, self.db.get_bind())
        if df.empty:
            return None
        return df.iloc[::-1].reset_index(drop=True) # 轉回正向時間順序

    def check_minervini_trend_template(self, df):
        """
        Mark Minervini Trend Template (第二階段多頭趨勢篩選)
        """
        if len(df) < 200:
            return False, "資料不足 200 天"

        close = df['close'].iloc[-1]
        ma50 = df['close'].rolling(window=50).mean().iloc[-1]
        ma150 = df['close'].rolling(window=150).mean().iloc[-1]
        ma200 = df['close'].rolling(window=200).mean().iloc[-1]
        
        # 200 MA 的趨勢 (最近 20 天是否向上)
        ma200_past = df['close'].rolling(window=200).mean().iloc[-20]
        ma200_is_up = ma200 > ma200_past

        low_52w = df['close'].tail(252).min()
        high_52w = df['close'].tail(252).max()

        # 條件 1-5: 價格與均線關係
        c1 = close > ma150 and close > ma200
        c2 = ma150 > ma200
        c3 = ma200_is_up
        c4 = ma50 > ma150 and ma50 > ma200
        c5 = close > ma50
        
        # 條件 6-7: 52 週高低點關係
        c6 = close >= low_52w * 1.25
        c7 = close >= high_52w * 0.75

        conditions = [c1, c2, c3, c4, c5, c6, c7]
        if all(conditions):
            return True, "符合第二階段趨勢"
        
        # 如果不符合，回傳具體缺漏
        # failure = []
        # if not c1: failure.append("Price < MA150/200")
        # if not c3: failure.append("MA200 not up")
        return False, "不符合趨勢模板"

    def scan_vcp(self, symbol):
        """
        專業 VCP 篩選器：遵循半年大底、波動收縮 (T1>T2>T3)、底底高、量縮
        """
        df = self.get_stock_data(symbol, 400)
        if df is None or len(df) < 200:
            return None

        # 1. 趨勢模板過濾 (Minervini Stage 2)
        is_trending, msg = self.check_minervini_trend_template(df)
        if not is_trending:
            return None

        # 2. 準則 1 & 3：半年大底與深度控制
        base_6m = df.tail(130) # 約半年交易日
        high_6m = base_6m['high'].max()
        low_6m = base_6m['low'].min()
        max_depth = (high_6m - low_6m) / high_6m
        
        if max_depth > 0.5: # 規則 3: 回檔不可超過 50%
            return None

        # 3. 準則 2：多層次回檔收縮 (波段分析)
        # 我們將 60 天分為三段 (T1: 60-40, T2: 40-20, T3: 20-0)
        t1_win = base_6m.iloc[-60:-40]
        t2_win = base_6m.iloc[-40:-20]
        t3_win = base_6m.iloc[-20:]

        t1_d = (t1_win['high'].max() - t1_win['low'].min()) / t1_win['high'].max()
        t2_d = (t2_win['high'].max() - t2_win['low'].min()) / t2_win['high'].max()
        t3_d = (t3_win['high'].max() - t3_win['low'].min()) / t3_win['high'].max()

        # 核心判斷 A: 波動必須收縮 (允許 2 波或 3 波收縮)
        is_contracting = (t1_d > t2_d) and (t2_d >= t3_d)
        
        # 核心判斷 B: 底部越來越高 (Ascending Bottoms, 允許平底支撐)
        low1 = t1_win['low'].min()
        low2 = t2_win['low'].min()
        low3 = t3_win['low'].min()
        is_ascending = low1 <= low2 and low2 <= low3

        # 4. 量能準則：回檔量能萎縮
        avg_vol_6m = base_6m['volume'].mean()
        recent_vol_avg = t3_win['volume'].mean()
        volume_dry = recent_vol_avg < avg_vol_6m * 0.95

        # 5. 輸出數值
        pivot_price = t3_win['high'].max()
        stop_loss = low3 * 0.98
        risk_pct = (pivot_price - stop_loss) / pivot_price

        # 最終判定條件 (T3 必須夠緊)
        if is_contracting and is_ascending and volume_dry and t3_d < 0.12:
            return {
                "symbol": symbol,
                "msg": "符合專業 VCP (波動收縮+底部支撐)",
                "waves": f"{t1_d:.0%} > {t2_d:.0%} > {t3_d:.0%}",
                "pivot": round(pivot_price, 1),
                "stop_loss": round(stop_loss, 1),
                "risk": f"{risk_pct:.1%}",
                "depth": f"{max_depth:.1%}"
            }
        
        return None

    def run_all(self):
        try:
            symbols = [s[0] for s in self.db.query(DailyBar.symbol).distinct().all()]
            total = len(symbols)
            logger.info(f"開始掃描全台股 VCP 型態... 總計 {total} 檔")
            
            candidates = []
            for i, s in enumerate(symbols):
                try:
                    res = self.scan_vcp(s)
                    if res:
                        candidates.append(res)
                        logger.success(f"發現潛在標的: {res}")
                    
                    if (i + 1) % 100 == 0:
                        logger.info(f"進度: {i+1}/{total}...")
                except Exception as e:
                    logger.debug(f"跳過 {s}: {e}")
                    continue
                    
            logger.info(f"掃描完成！共發現 {len(candidates)} 個標的。")
            return candidates
        finally:
            self.db.close()

if __name__ == "__main__":
    scanner = VCPScanner()
    results = scanner.run_all()
    
    # 儲存結果到檔案
    import json
    with open("scanner_results.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=4)
        
    # 輸出簡易報告
    if results:
        print("\n=== VCP 篩選報告 ===")
        print(f"{'代號':<10} {'深度':<10} {'風險(停損)':<10} {'Pivot':<10}")
        for r in results:
            print(f"{r['symbol']:<10} {r['depth']:<10} {r['risk']:<10} {r['pivot']:<10}")
        print(f"\n完整結果已儲存至 scanner_results.json")
    else:
        print("目前沒有符合 VCP 型態的標的。")
