"""
A-stock specific factors — PB classification & main force flow.
Called once per stock analysis, feeds into macro_data for scoring.
Data sources: akshare (stock_zh_valuation_baidu, stock_individual_fund_flow)
"""
import numpy as np, pandas as pd
from pathlib import Path

def get_pb_classification(symbol):
    """Get PB ratio and classify stock type. symbol = '600519'"""
    try:
        import akshare as ak
        df = ak.stock_zh_valuation_baidu(symbol=symbol, indicator="市净率")
        pb = float(df.iloc[-1]["value"])
        if pb > 4: return "growth", pb
        elif pb > 2: return "value", pb
        else: return "deep_value", pb
    except:
        return "growth", 5  # fallback

def get_mff_data(symbol, market="sh"):
    """Get main force flow data for last 120 days. Returns dict: date -> {super_ratio, mf_ratio}"""
    try:
        import akshare as ak
        df = ak.stock_individual_fund_flow(stock=symbol, market=market)
        df = df.rename(columns={"日期": "date"})
        df["date"] = pd.to_datetime(df["date"])
        
        mff_dict = {}
        for _, row in df.iterrows():
            d = row["date"]
            if not pd.isna(d):
                mff_dict[d] = {
                    "super_ratio": float(row.get("超大单净流入-净占比", 0)),
                    "mf_ratio": float(row.get("主力净流入-净占比", 0))
                }
        return mff_dict
    except:
        return {}
