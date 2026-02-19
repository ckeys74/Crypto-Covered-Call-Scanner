import os
from datetime import datetime, timedelta
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from functools import lru_cache
import requests  # For Polygon API calls

app = FastAPI(title="Crypto ETF Covered Call Scanner")

# CORS for Kajabi
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Polygon API key (add as env var in Render later)
POLYGON_API_KEY = os.getenv("POLYGON_API_KEY")  # Set in Render dashboard

# ETF tickers
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
    if not POLYGON_API_KEY:
        return {"error": "Polygon API key not set. Contact admin."}

    try:
        # Get current price (use snapshot or aggregate)
        snapshot_url = f"https://api.polygon.io/v3/snapshot/locale/us/markets/stocks/tickers/{ticker}?apiKey={POLYGON_API_KEY}"
        snapshot_resp = requests.get(snapshot_url)
        snapshot_resp.raise_for_status()
        snapshot = snapshot_resp.json()
        current_price = snapshot['ticker']['lastTrade']['p'] if 'ticker' in snapshot else None
        if not current_price:
            return {"error": f"No current price for {ticker}."}

        # 52-week high/low (use aggregates)
        today = datetime.now().strftime('%Y-%m-%d')
        one_year_ago = (datetime.now() - timedelta(days=365)).strftime('%Y-%m-%d')
        agg_url = f"https://api.polygon.io/v2/aggs/ticker/{ticker}/range/1/day/{one_year_ago}/{today}?apiKey={POLYGON_API_KEY}"
        agg_resp = requests.get(agg_url)
        agg_resp.raise_for_status()
        aggs = agg_resp.json().get('results', [])
        if aggs:
            week52_high = max(item['h'] for item in aggs)
            week52_low = min(item['l'] for item in aggs)
        else:
            week52_high = None
            week52_low = None

        # Get options expirations (list contracts and find expirations)
        contracts_url = f"https://api.polygon.io/v3/reference/options/contracts?underlying_ticker={ticker}&contract_type=call&limit=1000&apiKey={POLYGON_API_KEY}"
        contracts_resp = requests.get(contracts_url)
        contracts_resp.raise_for_status()
        contracts = contracts_resp.json().get('results', [])

        expirations = set()
        for c in contracts:
            if 'expiration_date' in c:
                expirations.add(c['expiration_date'])

        expirations = sorted(list(expirations))
        if not expirations:
            return {"error": f"No options for {ticker}."}

        # Find nearest monthly (20–40 days)
        today_dt = datetime.now()
        target_exp = None
        for exp in expirations:
            exp_date = datetime.strptime(exp, '%Y-%m-%d')
            days = (exp_date - today_dt).days
            if 20 <= days <= 40:
                target_exp = exp
                break

        if not target_exp:
            return {"error": f"No monthly expiration (20–40 days) for {ticker}."}

        # Get full options chain for target expiration
        chain_url = f"https://api.polygon.io/v3/snapshot/options/{ticker}?expiration_date={target_exp}&limit=250&apiKey={POLYGON_API_KEY}"
        chain_resp = requests.get(chain_url)
        chain_resp.raise_for_status()
        chain_data = chain_resp.json().get('results', [])

        # Filter calls (only calls)
        calls = [c for c in chain_data if c['details']['type'] == 'call']

        if not calls:
            return {"error": f"No call options for {ticker} on {target_exp}."}

        # Convert to DataFrame for easier processing
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

        if calls_df.empty:
            return {
                "ticker": ticker,
                "current_price": current_price,
                "week52_high": week52_high,
                "week52_low": week52_low,
                "expiration": target_exp,
                "strategies": [],
                "message": "No calls with positive bid/last price."
            }

        # ITM (2 closest below)
        itm = calls_df[calls_df['strike'] < current_price].sort_values('strike', ascending=False).head(2)

        # OTM (5 closest above)
        otm = calls_df[calls_df['strike'] > current_price].sort_values('strike', ascending=True).head(5)

        strategies_df = pd.concat([itm, otm])
        strategies_df['cap_gain'] = strategies_df['strike'] - current_price
        strategies_df['total_return_pct'] = ((strategies_df['premium'] + strategies_df['cap_gain']) / current_price) * 100
        strategies_df['premium_yield_pct'] = (strategies_df['premium'] / current_price) * 100
        strategies_df['downside_breakeven'] = current_price - strategies_df['premium']

        strategies_list = strategies_df[['strike', 'premium', 'impliedVolatility', 'openInterest', 'total_return_pct', 'premium_yield_pct', 'downside_breakeven']].to_dict(orient='records')

        return {
            "ticker": ticker,
            "current_price": float(current_price),
            "week52_high": week52_high,
            "week52_low": week52_low,
            "expiration": target_exp,
            "strategies": strategies_list
        }

    except Exception as e:
        return {"error": f"Error fetching data: {str(e)}"}

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
    
