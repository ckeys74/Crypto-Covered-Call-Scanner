import yfinance as yf
import pandas as pd
from datetime import datetime
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from functools import lru_cache

app = FastAPI(title="Crypto ETF Covered Call Scanner")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

crypto_etfs = {
    'BTC': ['IBIT', 'FBTC', 'GBTC', 'ARKB', 'BITB', 'HODL', 'EZBC', 'BTCW', 'YBTC', 'BTCI'],
    'ETH': ['ETHA', 'FETH', 'ETHV', 'ETHE', 'YETH', 'EHY'],
    'SOL': ['BSOL', 'GSOL', 'SOL', 'SOLM', 'SOLC'],
    'XRP': ['GXRP', 'XRPZ', 'TOXR', 'XRP', 'XRPM'],
    'ADA': [],
    'HBAR': ['HBR'],
    'LTC': ['LTCC'],
    'DOGE': [],
}

def get_covered_call_strategies(ticker: str):
    try:
        etf = yf.Ticker(ticker)
        
        hist_1d = etf.history(period='1d')
        if hist_1d.empty:
            return {"error": f"No recent price data for {ticker}."}
        current_price = hist_1d['Close'].iloc[-1]
        
        hist_1y = etf.history(period='1y')
        week52_high = hist_1y['High'].max() if not hist_1y.empty else None
        week52_low = hist_1y['Low'].min() if not hist_1y.empty else None
        
        expirations = etf.options
        if not expirations:
            return {"error": f"No options data for {ticker}."}
        
        today = datetime.now()
        target_exp = None
        for exp in expirations:
            exp_date = datetime.strptime(exp, '%Y-%m-%d')
            days_to_exp = (exp_date - today).days
            if 20 <= days_to_exp <= 40:
                target_exp = exp
                break
        
        if not target_exp:
            return {"error": f"No monthly expiration (20–40 days) for {ticker}."}
        
        opt_chain = etf.option_chain(target_exp)
        calls = opt_chain.calls
        
        calls['premium'] = calls['lastPrice'].fillna(calls['bid'])
        calls = calls[calls['premium'] > 0].dropna(subset=['premium'])
        
        if calls.empty:
            return {
                "ticker": ticker,
                "current_price": float(current_price),
                "week52_high": week52_high,
                "week52_low": week52_low,
                "expiration": target_exp,
                "strategies": [],
                "message": "No calls with positive bid/last price."
            }
        
        itm_calls = calls[calls['strike'] < current_price].sort_values('strike', ascending=False).head(2)
        otm_calls = calls[calls['strike'] > current_price].sort_values('strike', ascending=True).head(5)
        
        strategies = pd.concat([itm_calls, otm_calls])
        strategies['cap_gain'] = strategies['strike'] - current_price
        strategies['total_return_pct'] = ((strategies['premium'] + strategies['cap_gain']) / current_price) * 100
        
        # NEW: Premium yield % (if not assigned)
        strategies['premium_yield_pct'] = (strategies['premium'] / current_price) * 100
        
        # NEW: Downside break-even (current - premium)
        strategies['downside_breakeven'] = current_price - strategies['premium']
        
        strategies = strategies[['strike', 'premium', 'impliedVolatility', 'openInterest', 'total_return_pct', 'premium_yield_pct', 'downside_breakeven']]
        
        strategies_list = strategies.to_dict(orient='records')
        
        return {
            "ticker": ticker,
            "current_price": float(current_price),
            "week52_high": week52_high,
            "week52_low": week52_low,
            "expiration": target_exp,
            "strategies": strategies_list
        }
    
    except Exception as e:
        return {"error": str(e)}

@lru_cache(maxsize=16)
def cached_scan(asset: str):
    tickers = crypto_etfs.get(asset.upper(), [])
    if not tickers:
        return {"error": f"No ETFs for '{asset}'."}
    
    results = {}
    for tick in tickers:
        results[tick] = get_covered_call_strategies(tick)
    
    return results

@app.get("/scan/{asset}")
def scan_asset(asset: str):
    return cached_scan(asset.upper())

@app.get("/")
def home():
    return {
        "title": "Crypto ETF Covered Call Scanner API",
        "message": "Use /scan/BTC, /scan/XRP, etc. or /docs for interactive testing."
    }
    
