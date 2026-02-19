import yfinance as yf
import pandas as pd
from datetime import datetime
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="Crypto ETF Covered Call Scanner")

# Enable CORS for Kajabi frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ETF mapping
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
        history = etf.history(period='1d')
        if history.empty:
            return {"error": f"No price data for {ticker}."}
        
        current_price = history['Close'].iloc[-1]
        expirations = etf.options
        
        if not expirations:
            return {"error": f"No options for {ticker}."}
        
        today = datetime.now()
        target_exp = None
        for exp in expirations:
            exp_date = datetime.strptime(exp, '%Y-%m-%d')
            days_to_exp = (exp_date - today).days
            if 20 <= days_to_exp <= 40:
                target_exp = exp
                break
        
        if not target_exp:
            return {"error": f"No near-monthly expiration for {ticker}."}
        
        opt_chain = etf.option_chain(target_exp)
        calls = opt_chain.calls
        
        # Filter OTM calls
        otm_calls = calls[calls['strike'] > current_price].copy()
        
        if otm_calls.empty:
            return {
                "ticker": ticker,
                "current_price": float(current_price),
                "expiration": target_exp,
                "top_strategies": [],
                "message": "No OTM calls found."
            }
        
        # Use lastPrice if available, otherwise bid (never use ask or midpoint)
        otm_calls['premium'] = otm_calls['lastPrice']
        otm_calls['premium'] = otm_calls['premium'].fillna(otm_calls['bid'])
        
        # Drop rows where premium is zero or NaN (no real sell price)
        otm_calls = otm_calls[otm_calls['premium'] > 0].dropna(subset=['premium'])
        
        if otm_calls.empty:
            return {
                "ticker": ticker,
                "current_price": float(current_price),
                "expiration": target_exp,
                "top_strategies": [],
                "message": "No calls with positive bid/last price."
            }
        
        # Sort by strike (closest first) and take top 5
        otm_calls = otm_calls.sort_values('strike', ascending=True).head(5)
        
        # Calculate return %
        otm_calls['cap_gain'] = otm_calls['strike'] - current_price
        otm_calls['total_return_pct'] = ((otm_calls['premium'] + otm_calls['cap_gain']) / current_price) * 100
        
        top_strategies = otm_calls[['strike', 'premium', 'impliedVolatility', 'total_return_pct']].to_dict(orient='records')
        
        return {
            "ticker": ticker,
            "current_price": float(current_price),
            "expiration": target_exp,
            "top_strategies": top_strategies
        }
    
    except Exception as e:
        return {"error": str(e)}

@app.get("/scan/{asset}")
def scan_asset(asset: str):
    tickers = crypto_etfs.get(asset.upper(), [])
    if not tickers:
        return {"error": f"No ETFs for '{asset}'."}
    
    results = {}
    for tick in tickers:
        results[tick] = get_covered_call_strategies(tick)
    
    return results

@app.get("/")
def home():
    return {
        "title": "Crypto ETF Covered Call Scanner API",
        "message": "Welcome! Use /scan/BTC, /scan/XRP, etc.",
        "docs": "/docs"
    }
    
