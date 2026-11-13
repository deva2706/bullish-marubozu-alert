import streamlit as st
import pandas as pd
import yfinance as yf
import requests
from io import StringIO
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from zoneinfo import ZoneInfo
import time

st.set_page_config(
    page_title="Bullish Marubozu Scanner",
    page_icon="📈",
    layout="wide"
)

st.title("📈 NSE Bullish Marubozu Scanner")
st.caption("5-Minute Timeframe | Bullish Marubozu Only")

st.info(
    "இந்த Scanner NSE Equity stocks-ஐ Yahoo Finance data மூலம் scan செய்கிறது. "
    "Data கிடைப்பது மற்றும் புதுப்பிக்கும் வேகம் source-ஐப் பொறுத்தது."
)

@st.cache_data(ttl=3600)
def get_nse_stocks():
    url = "https://archives.nseindia.com/content/equities/EQUITY_L.csv"

    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "text/csv,*/*"
    }

    response = requests.get(
        url,
        headers=headers,
        timeout=30
    )
    response.raise_for_status()

    stocks = pd.read_csv(StringIO(response.text))
    stocks.columns = stocks.columns.str.strip()

    stocks["SERIES"] = stocks["SERIES"].astype(str).str.strip()
    stocks["SYMBOL"] = stocks["SYMBOL"].astype(str).str.strip()

    stocks = stocks[stocks["SERIES"] == "EQ"]
    symbols = stocks["SYMBOL"].dropna().unique().tolist()

    return [s + ".NS" for s in symbols if s and s != "nan"]


def check_candle(df, symbol):
    if df is None or df.empty:
        return None

    df = df.dropna(subset=["Open", "High", "Low", "Close"])

    if len(df) < 2:
        return None

    # Latest completed 5-minute candle
    now = pd.Timestamp.now(tz="Asia/Kolkata")
    candle_time = df.index[-1]

    if candle_time.tzinfo is None:
        candle_time = candle_time.tz_localize("Asia/Kolkata")
    else:
        candle_time = candle_time.tz_convert("Asia/Kolkata")

    if now < candle_time + pd.Timedelta(minutes=5):
        candle = df.iloc[-2]
        candle_time = df.index[-2]
    else:
        candle = df.iloc[-1]

    o = float(candle["Open"])
    h = float(candle["High"])
    l = float(candle["Low"])
    c = float(candle["Close"])

    total_range = h - l

    if total_range <= 0 or c <= o:
        return None

    body = c - o
    upper_wick = h - c
    lower_wick = o - l

    body_ratio = body / total_range
    upper_ratio = upper_wick / total_range
    lower_ratio = lower_wick / total_range

    # Bullish Marubozu rules
    if (
        body_ratio >= 0.90
        and upper_ratio <= 0.05
        and lower_ratio <= 0.05
    ):
        candle_time = pd.Timestamp(candle_time)

        if candle_time.tzinfo is None:
            candle_time = candle_time.tz_localize("Asia/Kolkata")
        else:
            candle_time = candle_time.tz_convert("Asia/Kolkata")

        return {
            "Stock": symbol.replace(".NS", ""),
            "Price": round(c, 2),
            "Open": round(o, 2),
            "High": round(h, 2),
            "Low": round(l, 2),
            "Candle Time": candle_time.strftime("%d-%m-%Y %H:%M"),
            "Body %": round(body_ratio * 100, 1)
        }

    return None


def get_stock_frame(data, ticker):
    if isinstance(data.columns, pd.MultiIndex):
        if ticker in data.columns.get_level_values(0):
            return data[ticker]

        if ticker in data.columns.get_level_values(1):
            return data.xs(ticker, axis=1, level=1)

        return None

    return data


def scan_stocks(symbols, progress_bar, status):
    results = []
    batch_size = 40

    batches = [
        symbols[i:i + batch_size]
        for i in range(0, len(symbols), batch_size)
    ]

    for batch_no, batch in enumerate(batches, start=1):
        status.write(
            f"Scanning batch {batch_no}/{len(batches)}..."
        )

        try:
            data = yf.download(
                tickers=batch,
                period="1d",
                interval="5m",
                group_by="ticker",
                auto_adjust=False,
                threads=True,
                progress=False,
                timeout=30
            )

            for ticker in batch:
                try:
                    frame = get_stock_frame(data, ticker)

                    if frame is None:
                        continue

                    signal = check_candle(frame, ticker)

                    if signal:
                        results.append(signal)

                except Exception:
                    continue

        except Exception:
            pass

        progress_bar.progress(
            batch_no / len(batches)
        )

        time.sleep(0.5)

    return results


if st.button("🔍 Scan All NSE Stocks", type="primary"):
    try:
        with st.spinner("Loading NSE stock list..."):
            symbols = get_nse_stocks()

        st.success(f"Total NSE Equity Stocks: {len(symbols)}")

        progress = st.progress(0)
        status = st.empty()

        results = scan_stocks(
            symbols,
            progress,
            status
        )

        st.session_state["scan_results"] = results
        st.session_state["scan_time"] = datetime.now(
            ZoneInfo("Asia/Kolkata")
        ).strftime("%d-%m-%Y %H:%M:%S")

    except Exception as e:
        st.error(f"Scanner Error: {e}")


if "scan_results" in st.session_state:
    results = st.session_state["scan_results"]

    st.divider()
    st.subheader("🟢 Bullish Marubozu Results")

    st.metric("Total Signals Found", len(results))

    if results:
        result_df = pd.DataFrame(results)

        st.dataframe(
            result_df,
            use_container_width=True,
            hide_index=True
        )

        csv = result_df.to_csv(index=False).encode("utf-8")

        st.download_button(
            "Download Results CSV",
            data=csv,
            file_name="bullish_marubozu_signals.csv",
            mime="text/csv"
        )

    else:
        st.warning("No Bullish Marubozu found.")

    st.caption(
        "Last Scan: " + st.session_state["scan_time"]
    )

st.divider()
st.caption(
    "Educational scanner only. Trading decisions and risk management "
    "are the user's responsibility."
)
