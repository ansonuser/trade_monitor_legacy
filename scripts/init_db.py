import sys
from loguru import logger
from tracker.core.database import engine, Base, ping_database
from tracker.core.db_models import DailyBar, OrderRecord, StrategyConfig

def main():
    logger.info("檢查資料庫連線中...")
    if not ping_database():
        logger.error("無法連線至 PostgreSQL！請確認 `docker-compose up -d` 是否已經啟動並成功映射 5432 Port。")
        sys.exit(1)
        
    logger.info("資料庫連線成功！準備建立資料表綱要...")
    
    try:
        # 強制重置資料表綱要 (因為我們要從 Integer 改成 BigInteger)
        logger.info("正在重置資料表綱要...")
        Base.metadata.drop_all(bind=engine)
        
        # 建立所有繼承 Base 的 ORM Tables
        Base.metadata.create_all(bind=engine)
        logger.info("所有的資料表 (Tables) 已成功建立！")
    except Exception as e:
        logger.error(f"建立資料表時發生錯誤: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
