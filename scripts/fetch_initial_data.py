from tracker.services.data_service import DataService
from tracker.providers.history_provider import HistoryProvider
from tracker.core.database import SessionLocal
from tracker.core.db_models import DailyBar
from loguru import logger
import time

def main():
    # 改為抓取全台股清單
    target_stocks = HistoryProvider.get_all_taiwan_tickers()
    
    if not target_stocks:
        logger.error("無法取得股票清單，請檢查網路連線")
        return

    logger.info(f"開始執行全台股歷史抓取任务，目標數量: {len(target_stocks)}")
    
    service = DataService()
    db = SessionLocal()
    try:
        total = len(target_stocks)
        logger.info(f"啟動全量歷史回補 (Period: max)，預計處理 {total} 檔標的...")
        
        for i, stock in enumerate(target_stocks):
            full_symbol = f"{stock['code']}{stock['suffix']}"
            
            # 不再跳過已有資料的標的，而是交由 DataService 檢查並補足缺失的歷史日期
            logger.info(f"[{i+1}/{total}] 正在同步 {stock['market']}: {full_symbol} ...")
            
            # 使用 period='max' 獲取所有可用歷史資料
            service.sync_historical_bars(full_symbol, period="max")
            
            # 全量回補資料量大，稍微增加休息時間以維護爬蟲壽命
            time.sleep(1.0)
            
            if (i + 1) % 20 == 0:
                logger.info(f"已完成 {i+1} 檔，目前進度 {((i+1)/total):.1%} ...")
                time.sleep(2)
    finally:
        db.close()

    logger.info("所有股價抓取任務已完成！")

if __name__ == "__main__":
    main()
