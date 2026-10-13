import requests
import pandas as pd
from loguru import logger

def test_fetch():
    tickers = []
    try:
        # 1. 上市股票 (Mode 2)
        url_twse = "https://isin.twse.com.tw/isin/C_public.jsp?strMode=2"
        logger.info(f"Testing TWSE fetch: {url_twse}")
        res = requests.get(url_twse, timeout=10)
        res.encoding = 'big5' # 證交所通常用 big5
        
        dfs = pd.read_html(res.text)
        if not dfs:
            print("No tables found!")
            return []
        
        df_twse = dfs[0]
        print(f"TWSE Table shape: {df_twse.shape}")
        
        # 整理資料：第 0 欄包含 "2330 台積電" 這種格式
        for item in df_twse[0]:
            if isinstance(item, str):
                parts = item.split("\u3000") # 全形空白
                if len(parts) > 1 and parts[0].isdigit() and len(parts[0]) == 4:
                    tickers.append(parts[0])
        
        print(f"First 5 TWSE tickers: {tickers[:5]}")
        
        # 2. 上櫃股票 (Mode 4)
        url_otc = "https://isin.twse.com.tw/isin/C_public.jsp?strMode=4"
        print(f"Testing OTC fetch: {url_otc}")
        res = requests.get(url_otc, timeout=15)
        res.encoding = 'big5'
        
        dfs_otc = pd.read_html(res.text)
        if not dfs_otc:
            print("No OTC tables found!")
        else:
            df_otc = dfs_otc[0]
            print(f"OTC Table shape: {df_otc.shape}")
            
            for item in df_otc[0]:
                if isinstance(item, str):
                    parts = item.split("\u3000")
                    if len(parts) > 1 and parts[0].isdigit() and len(parts[0]) == 4:
                        tickers.append(parts[0])
        
        print(f"Total tickers found: {len(tickers)}")
        return tickers
    except Exception as e:
        print(f"Fetch failed: {e}")
        import traceback
        traceback.print_exc()
        return []

if __name__ == "__main__":
    test_fetch()
