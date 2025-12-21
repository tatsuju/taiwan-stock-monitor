# -*- coding: utf-8 -*-
import os, sys, time, random, logging, warnings, subprocess, json
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from tqdm import tqdm
import pandas as pd
import yfinance as yf

# ====== 自動安裝必要套件 ======
def ensure_pkg(pkg: str):
    try:
        __import__(pkg)
    except ImportError:
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", pkg])

ensure_pkg("pykrx")
from pykrx import stock as krx

# ====== 降噪與環境設定 ======
warnings.filterwarnings("ignore")
logging.getLogger("yfinance").setLevel(logging.CRITICAL)

MARKET_CODE = "kr-share"
DATA_SUBDIR = "dayK"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data", MARKET_CODE, DATA_SUBDIR)
LIST_DIR = os.path.join(BASE_DIR, "data", MARKET_CODE, "lists")

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(LIST_DIR, exist_ok=True)

# Checkpoint 檔案路徑
MANIFEST_CSV = Path(LIST_DIR) / "kr_manifest.csv"
START_DATE = "2000-01-01"
THREADS = 4

def log(msg: str):
    print(f"{pd.Timestamp.now():%H:%M:%S}: {msg}")

def map_symbol_kr(code: str, board: str) -> str:
    """轉換為 Yahoo Finance 格式"""
    suffix = ".KS" if board.upper() == "KS" else ".KQ"
    return f"{str(code).zfill(6)}{suffix}"

def standardize_df(df: pd.DataFrame) -> pd.DataFrame:
    """將 yfinance 原始資料標準化"""
    if df is None or df.empty: return pd.DataFrame()
    df = df.reset_index()
    df.columns = [c.lower() for c in df.columns]
    if 'date' not in df.columns: return pd.DataFrame()
    
    # 移除時區
    df['date'] = pd.to_datetime(df['date'], utc=True).dt.tz_localize(None)
    req = ['date','open','high','low','close','volume']
    return df[req] if all(c in df.columns for c in req) else pd.DataFrame()

def get_kr_list():
    """從 KRX 獲取最新 KOSPI/KOSDAQ 清單"""
    today = pd.Timestamp.today().strftime("%Y%m%d")
    lst = []
    try:
        for mk, bd in [("KOSPI","KS"), ("KOSDAQ","KQ")]:
            tickers = krx.get_market_ticker_list(today, market=mk)
            for t in tickers:
                name = krx.get_market_ticker_name(t)
                lst.append({"code": t, "name": name, "board": bd})
        return pd.DataFrame(lst)
    except Exception as e:
        log(f"⚠️ 獲取清單失敗: {e}")
        return pd.DataFrame([{"code":"005930","name":"三星電子","board":"KS"}])

def build_manifest(df_list):
    """建立續跑清單，偵測已下載的檔案"""
    if MANIFEST_CSV.exists():
        return pd.read_csv(MANIFEST_CSV)
    
    df_list["status"] = "pending"
    # 自動偵測現有檔案
    existing_files = [f for f in os.listdir(DATA_DIR) if f.endswith(".csv")]
    for f in existing_files:
        code_part = f.replace(".csv", "")
        if "." in code_part:
            c, b = code_part.split(".")
            df_list.loc[(df_list['code'] == c) & (df_list['board'] == b), "status"] = "done"
    
    df_list.to_csv(MANIFEST_CSV, index=False)
    return df_list

def download_one(row_tuple):
    idx, row = row_tuple
    code, board = row['code'], row['board']
    symbol = map_symbol_kr(code, board)
    out_path = os.path.join(DATA_DIR, f"{code}.{board}.csv")
    
    try:
        tk = yf.Ticker(symbol)
        # 使用 2y 期間進行分析所需數據
        df_raw = tk.history(period="2y", interval="1d", auto_adjust=False)
        df = standardize_df(df_raw)
        
        if not df.empty:
            df.to_csv(out_path, index=False)
            return idx, "done"
        return idx, "empty"
    except:
        return idx, "failed"

def main():
    log("🇰🇷 啟動韓股下載引擎 (KOSPI/KOSDAQ)")
    
    # 1. 獲取與建立清單
    df_list = get_kr_list()
    mf = build_manifest(df_list)
    
    todo = mf[mf["status"] != "done"]
    if todo.empty:
        log("✅ 所有韓股資料已是最新狀態，無需下載。")
        return

    log(f"📝 待處理標的：{len(todo)} 檔")
    
    # 2. 多執行緒下載
    with ThreadPoolExecutor(max_workers=THREADS) as executor:
        futures = {executor.submit(download_one, item): item for item in todo.iterrows()}
        pbar = tqdm(total=len(todo), desc="韓股進度")
        
        count = 0
        for f in as_completed(futures):
            idx, status = f.result()
            mf.at[idx, "status"] = status
            count += 1
            pbar.update(1)
            
            # 每 50 檔儲存一次進度，防止意外中斷
            if count % 50 == 0:
                mf.to_csv(MANIFEST_CSV, index=False)
        
        pbar.close()
    
    mf.to_csv(MANIFEST_CSV, index=False)
    success_count = len(mf[mf["status"] == "done"])
    log(f"🏁 韓股任務完成！成功率: {success_count}/{len(mf)}")

if __name__ == "__main__":
    main()