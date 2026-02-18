import yfinance as yf
import pandas as pd
from datetime import datetime
from fastapi import FastAPI

app = FastAPI()

# ETF mapping by asset
crypto_etfs = {
    'BTC': ['IBIT', 'FBTC', 'GBTC', 'ARKB', 'BITB', 'HODL', 'EZBC', 'BTCW', 'YBTC', 'BTCI'],
    'ETH': ['ETHA', 'FETH', 'ETHV', 'ETHE', 'YETH', 'EHY'],
    'SOL': ['BSOL', 'GSOL', 'SOL', 'SOLM', 'SOLC'],
    'XRP': ['GXRP', 'XRPZ', 'TOXR', 'XRP', 'XRPM'],
    'ADA': [],  # Pending
    'HBAR': ['HBR'],
    'LTC': ['LTCC'],
    'DOGE': [],  # Pending
    # Add more as needed
}

def get_covered_call_strategies(ticker):
    try:
        etf = yf.Ticker(ticker)
        current_price = etf.history(period='1d')['Close'].iloc[-1]
        expirations = etf.options
        
        if not expirations:
            return {"error": f"No options available for {ticker}."}
        
        # Find nearest monthly expiration (20-40 days)
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
        otm_calls = calls[calls['strike'] > current_price]
        
        # Calculate return if executed
        otm_calls['premium'] = otm_calls['lastPrice'].fillna((otm_calls['bid'] + otm_calls['ask']) / 2)
        otm_calls['cap_gain'] = otm_calls['strike'] - current_price
        otm_calls['total_return_pct'] = ((otm_calls['premium'] + otm_calls['cap_gain']) / current_price) * 100
        otm_calls['monthly_annualized'] = otm_calls['total_return_pct'] * (30 / days_to_exp)
        
        # Top 5 by return (convert to dict for JSON)
        top_strategies = otm_calls.sort_values('total_return_pct', ascending=False).head(5)[['strike', 'premium', 'impliedVolatility', 'total_return_pct']].to_dict(orient='records')
        
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
        return {"error": f"No ETFs found for {asset}."}
    
    results = {}
    for tick in tickers:
        results[tick] = get_covered_call_strategies(tick)
    
    return results
if not top_strategies:
    return {"ticker": ticker, "current_price": float(current_price), "expiration": target_exp, "top_strategies": [], "message": "No suitable OTM calls found"}
