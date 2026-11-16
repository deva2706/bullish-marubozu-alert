
import streamlit as st
import pandas as pd
import yfinance as yf
import requests
from io import StringIO
from datetime import datetime, time as dt_time
from zoneinfo import ZoneInfo
import time

TZ = ZoneInfo("Asia/Kolkata")
BATCH_SIZE = 20

st.set_page_config(page_title="NSE 2-Candle Bullish Marubozu Scanner", page_icon="📈", layout="wide")
st.title("📈 NSE 2-Candle Bullish Marubozu Scanner")
st.caption("NSE EQ stocks | 5-minute candles | Automatic scan every 5 minutes")
st.info("முதல் candle எந்த அளவிலான Green candle-ஆகவும் இருக்கலாம். அதற்குப் பின்வரும் இரண்டாவது candle Bullish Marubozu ஆக இருக்க வேண்டும். Scan நேரம்: 9:30 AM–3:30 PM.")

@st.cache_data(ttl=86400, show_spinner=False)
def get_nse_stocks():
    url = "https://archives.nseindia.com/content/equities/EQUITY_L.csv"
    headers = {"User-Agent": "Mozilla/5.0", "Accept": "text/csv,*/*"}
    response = requests.get(url, headers=headers, timeout=30)
    response.raise_for_status()
    stocks = pd.read_csv(StringIO(response.text))
    stocks.columns = stocks.columns.str.strip()
    stocks["SERIES"] = stocks["SERIES"].astype(str).str.strip()
    stocks["SYMBOL"] = stocks["SYMBOL"].astype(str).str.strip()
    stocks = stocks[stocks["SERIES"] == "EQ"]
    return [s + ".NS" for s in stocks["SYMBOL"].dropna().unique().tolist() if s and s.lower() != "nan"]

def get_stock_frame(data, ticker):
    if data is None or data.empty:
        return None
    if isinstance(data.columns, pd.MultiIndex):
        if ticker in data.columns.get_level_values(0):
            return data[ticker]
        if ticker in data.columns.get_level_values(1):
            return data.xs(ticker, axis=1, level=1)
        return None
    return data

def local_time(value):
    stamp = pd.Timestamp(value)
    return stamp.tz_localize("Asia/Kolkata") if stamp.tzinfo is None else stamp.tz_convert("Asia/Kolkata")

def check_pattern(df, ticker, now):
    if df is None or df.empty:
        return None
    cols = ["Open", "High", "Low", "Close"]
    if not all(c in df.columns for c in cols):
        return None
    df = df.dropna(subset=cols)
    completed = [(local_time(idx), row) for idx, row in df.iterrows()
                 if now >= local_time(idx) + pd.Timedelta(minutes=5)]
    if len(completed) < 2:
        return None
    t1, first = completed[-2]
    t2, second = completed[-1]
    if t2.time() < dt_time(9, 25):
        return None
    # Candle 1: any-size green candle
    if float(first["Close"]) <= float(first["Open"]):
        return None
    # Candle 2: bullish Marubozu
    o, h, low, close = (float(second[x]) for x in cols)
    rng = h - low
    if rng <= 0 or close <= o:
        return None
    body_pct = (close - o) / rng
    upper_pct = (h - close) / rng
    lower_pct = (o - low) / rng
    if body_pct >= 0.90 and upper_pct <= 0.05 and lower_pct <= 0.05:
        return {"Stock": ticker.replace(".NS", ""),
                "First Candle Time": t1.strftime("%H:%M"),
                "Second Candle Time": t2.strftime("%H:%M"),
                "First Candle": "Green",
                "Marubozu Close": round(close, 2),
                "Body %": round(body_pct * 100, 1)}
    return None

def scan_stocks(symbols, progress, status, now):
    found = []
    batches = [symbols[i:i+BATCH_SIZE] for i in range(0, len(symbols), BATCH_SIZE)]
    for n, batch in enumerate(batches, 1):
        status.write(f"Scanning batch {n}/{len(batches)}")
        try:
            data = yf.download(tickers=batch, period="1d", interval="5m",
                               group_by="ticker", auto_adjust=False,
                               threads=True, progress=False, timeout=30)
            for ticker in batch:
                try:
                    result = check_pattern(get_stock_frame(data, ticker), ticker, now)
                    if result:
                        found.append(result)
                except Exception:
                    continue
        except Exception:
            pass
        progress.progress(n / max(len(batches), 1))
        time.sleep(0.3)
    return found

def scan_time(now):
    return now.weekday() < 5 and dt_time(9, 30) <= now.time() <= dt_time(15, 30)

@st.fragment(run_every="5m")
def automatic_scanner():
    now = datetime.now(TZ)
    st.write("**இந்திய நேரம்:**", now.strftime("%d-%m-%Y %H:%M:%S"))
    if not scan_time(now):
        st.warning("Scan நேரம்: திங்கள்–வெள்ளி காலை 9:30 முதல் மதியம் 3:30 வரை. NSE விடுமுறை நாட்களில் scan இயங்காது.")
        if "scan_results" in st.session_state:
            st.dataframe(pd.DataFrame(st.session_state["scan_results"]), use_container_width=True, hide_index=True)
        return
    try:
        with st.spinner("NSE EQ stock list ஏற்றப்படுகிறது..."):
            symbols = get_nse_stocks()
        st.write(f"**மொத்த NSE EQ symbols:** {len(symbols)}")
        progress = st.progress(0)
        status = st.empty()
        results = scan_stocks(symbols, progress, status, now)
        st.session_state["scan_results"] = results
        st.session_state["scan_time"] = datetime.now(TZ).strftime("%d-%m-%Y %H:%M:%S")
        status.success("இந்த scan முடிந்தது.")
        st.metric("Total 2-Candle Signals", len(results))
        if results:
            result_df = pd.DataFrame(results)
            st.dataframe(result_df, use_container_width=True, hide_index=True)
            st.download_button("Download Signals CSV", result_df.to_csv(index=False).encode("utf-8"),
                               "nse_two_candle_bullish_marubozu.csv", "text/csv")
        else:
            st.info("இந்த scan-ல் இந்த 2-candle pattern கிடைக்கவில்லை.")
        st.caption("Last scan: " + st.session_state["scan_time"])
    except Exception as e:
        st.error(f"Scanner Error: {e}")
        st.caption("NSE stock list அல்லது Yahoo Finance data கிடைக்கவில்லை என்றால் சிறிது நேரம் கழித்து மீண்டும் முயற்சிக்கவும்.")

automatic_scanner()
st.divider()
st.caption("முதல் candle: எந்த அளவிலான Green candle-ம் OK. இரண்டாவது candle: Bullish Marubozu கட்டாயம். Streamlit app திறந்திருக்கும் போது மட்டுமே automatic refresh இயங்கும். Yahoo Finance data delayed/missing ஆகலாம்; இது live exchange feed அல்ல. இது buy/sell recommendation அல்ல.")
