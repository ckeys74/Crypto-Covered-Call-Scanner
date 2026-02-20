import os
import time
from datetime import datetime, timedelta
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from functools import lru_cache
import requests
import yfinance as yf
import pandas as pd

app = FastAPI(title="Crypto ETF Covered Call Scanner")

# CORS for Kajabi
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Polygon API key from env
POLYGON_API_KEY = os.getenv("POLYGON_API_KEY")

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

def fetch_with_polygon(ticker: str):
    """Try Polygon first for current price and options chain."""
    try:
        # Current price snapshot
        snapshot_url = f"https://api.polygon.io/v3/snapshot/locale/us/markets/stocks/tickers/{ticker}?apiKey={POLYGON_API_KEY}"
        snapshot_resp = requests.get(snapshot_url, timeout=10)
        snapshot_resp.raise_for_status()
        snapshot = snapshot_resp.json()
        if 'ticker' not in snapshot or 'lastTrade' not in snapshot['ticker']:
            raise ValueError("No price in Polygon snapshot")
        current_price = snapshot['ticker']['lastTrade']['p']

        # 52-week range
        today = datetime.now().strftime('%Y-%m-%d')
        one_year_ago = (datetime.now() - timedelta(days=365)).strftime('%Y-%m-%d')
        agg_url = f"https://api.polygon.io/v2/aggs/ticker/{ticker}/range/1/day/{one_year_ago}/{today}?apiKey={POLYGON_API_KEY}"
        agg_resp = requests.get(agg_url, timeout=10)
        agg_resp.raise_for_status()
        aggs = agg_resp.json().get('results', [])
        week52_high = max(item['h'] for item in aggs) if aggs else None
        week52_low = min(item['l'] for item in aggs) if aggs else None

        # Options contracts list to find expirations
        contracts_url = f"https://api.polygon.io/v3/reference/options/contracts?underlying_ticker={ticker}&contract_type=call&limit=1000&apiKey={POLYGON_API_KEY}"
        contracts_resp = requests.get(contracts_url, timeout=15)
        contracts_resp.raise_for_status()
        contracts = contracts_resp.json().get('results', [])

        expirations = sorted(set(c['expiration_date'] for c in contracts if 'expiration_date' in c))

        if not expirations:
            raise ValueError("No options expirations found")

        # Find target expiration
        today_dt = datetime.now()
        target_exp = None
        for exp in expirations:
            exp_date = datetime.strptime(exp, '%Y-%m-%d')
            days = (exp_date - today_dt).days
            if 20 <= days <= 40:
                target_exp = exp
                break

        if not target_exp:
            raise ValueError("No suitable expiration")

        # Full options snapshot for expiration
        chain_url = f"https://api.polygon.io/v3/snapshot/options/{ticker}?expiration_date={target_exp}&limit=250&apiKey={POLYGON_API_KEY}"
        chain_resp = requests.get(chain_url, timeout=15)
        chain_resp.raise_for_status()
        chain_data = chain_resp.json().get('results', [])

        calls = [c for c in chain_data if c['details']['type'] == 'call']

        if not calls:
            raise ValueError("No call options")

        # Build DataFrame
        data = []
        for c in calls:
            d = c['details']
            greeks = c.get('greeks', {})
            last_quote = c.get('last_quote', {})
            data.append({
                'strike': d['strike_price'],
                'premium': last_quote.get('bid', 0) or last_quote.get('last_price', 0),
                'impliedVolatility': greeks.get('implied_volatility', 0),
                'openInterest': c.get('open_interest', 0),
            })

        calls_df = pd.DataFrame(data)
        calls_df = calls_df[calls_df['premium'] > 0]

        return calls_df, current_price, week52_high, week52_low, target_exp

    except Exception as e:
        # Raise to trigger yfinance fallback
        raise RuntimeError(f"Polygon failed: {str(e)}")

def get_covered_call_strategies(ticker: str):
    # Try Polygon first
    try:
        calls_df, current_price, week52_high, week52_low, target_exp = fetch_with_polygon(ticker)
    except RuntimeError as polygon_err:
        # Fallback to yfinance
        try:
            etf = yf.Ticker(ticker)
            hist_1d = etf.history(period='1d')
            if hist_1d.empty:
                return {"error": f"No price data for {ticker} (yfinance fallback)."}
            current_price = hist_1d['Close'].iloc[-1]

            hist_1y = etf.history(period='1y')
            week52_high = hist_1y['High'].max() if not hist_1y.empty else None
            week52_low = hist_1y['Low'].min() if not hist_1y.empty else None

            expirations = etf.options
            if not expirations:
                return {"error": f"No options for {ticker} (yfinance fallback)."}

            today = datetime.now()
            target_exp = None
            for exp in expirations:
                exp_date = datetime.strptime(exp, '%Y-%m-%d')
                days = (exp_date - today).days
                if 20 <= days <= 40:
                    target_exp = exp
                    break

            if not target_exp:
                return {"error": f"No monthly expiration for {ticker} (yfinance fallback)."}

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
                    "message": "No calls with positive bid/last price (yfinance fallback)."
                }

            calls_df = calls

        except Exception as yf_err:
            return {"error": f"Both Polygon and yfinance failed: {str(yf_err)}"}

    # Common processing for both sources
    itm_calls = calls_df[calls_df['strike'] < current_price].sort_values('strike', ascending=False).head(2)
    otm_calls = calls_df[calls_df['strike'] > current_price].sort_values('strike', ascending=True).head(5)

    strategies = pd.concat([itm_calls, otm_calls])
    strategies['cap_gain'] = strategies['strike'] - current_price
    strategies['total_return_pct'] = ((strategies['premium'] + strategies['cap_gain']) / current_price) * 100
    strategies['premium_yield_pct'] = (strategies['premium'] / current_price) * 100
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
