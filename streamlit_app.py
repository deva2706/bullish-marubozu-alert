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

st.set_page_config(
    page_title="NSE Bullish Marubozu Scanner",
    page_icon="📈",
    layout="wide"
)

st.title("📈 NSE Bullish Marubozu Scanner")
st.caption("NSE EQ stocks | 5-minute candles | Automatic scan every 5 minutes")

st.info(
    "இந்த scanner Yahoo Finance data-ஐ பயன்படுத்துகிறது. "
    "Data தாமதமாகவோ அல்லது சில symbols-க்கு கிடைக்காமலோ இருக்கலாம். "
    "இது exchange-certified live feed அல்ல."
)

@st.cache_data(ttl=86400, show_spinner=False)
def get_nse_stocks():
    url = "https://archives.nseindia.com/content/equities/EQUITY_L.csv"
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "text/csv,*/*"
    }
    response = requests.get(url, headers=headers, timeout=30)
    response.raise_for_status()

    stocks = pd.read_csv(StringIO(response.text))
    stocks.columns = stocks.columns.str.strip()
    stocks["SERIES"] = stocks["SERIES"].astype(str).str.strip()
    stocks["SYMBOL"] = stocks["SYMBOL"].astype(str).str.strip()
    stocks = stocks[stocks["SERIES"] == "EQ"]

    symbols = stocks["SYMBOL"].dropna().unique().tolist()
    return [s + ".NS" for s in symbols if s and s.lower() != "nan"]


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


def check_candle(df, ticker):
    if df is None or df.empty:
        return None

    needed = ["Open", "High", "Low", "Close"]
    if not all(col in df.columns for col in needed):
        return None

    df = df.dropna(subset=needed)
    if len(df) < 2:
        return None

    now = pd.Timestamp.now(tz="Asia/Kolkata")
    candle_time = pd.Timestamp(df.index[-1])

    if candle_time.tzinfo is None:
        candle_time = candle_time.tz_localize("Asia/Kolkata")
    else:
        candle_time = candle_time.tz_convert("Asia/Kolkata")

    # Use only a completed 5-minute candle.
    if now < candle_time + pd.Timedelta(minutes=5):
        candle = df.iloc[-2]
        candle_time = pd.Timestamp(df.index[-2])
    else:
        candle = df.iloc[-1]

    if candle_time.tzinfo is None:
        candle_time = candle_time.tz_localize("Asia/Kolkata")
    else:
        candle_time = candle_time.tz_convert("Asia/Kolkata")

    o = float(candle["Open"])
    h = float(candle["High"])
    low = float(candle["Low"])
    close = float(candle["Close"])

    total_range = h - low
    if total_range <= 0 or close <= o:
        return None

    body = close - o
    upper_wick = h - close
    lower_wick = o - low

    body_ratio = body / total_range
    upper_ratio = upper_wick / total_range
    lower_ratio = lower_wick / total_range

    # Bullish Marubozu: body >= 90%, each wick <= 5%.
    if body_ratio >= 0.90 and upper_ratio <= 0.05 and lower_ratio <= 0.05:
        return {
            "Stock": ticker.replace(".NS", ""),
            "Price": round(close, 2),
            "Open": round(o, 2),
            "High": round(h, 2),
            "Low": round(low, 2),
            "Candle Time": candle_time.strftime("%d-%m-%Y %H:%M"),
            "Body %": round(body_ratio * 100, 1)
        }

    return None


def scan_stocks(symbols, progress, status):
    results = []
    batches = [
        symbols[i:i + BATCH_SIZE]
        for i in range(0, len(symbols), BATCH_SIZE)
    ]

    for batch_no, batch in enumerate(batches, start=1):
        status.write(f"Scanning batch {batch_no}/{len(batches)}")

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
                    signal = check_candle(frame, ticker)
                    if signal:
                        results.append(signal)
                except Exception:
                    continue

        except Exception:
            # Continue with the next batch if one batch fails.
            pass

        progress.progress(batch_no / max(len(batches), 1))
        time.sleep(0.3)

    return results


def is_market_open(now):
    # NSE regular equity session: Monday-Friday, 09:15-15:30 IST.
    if now.weekday() >= 5:
        return False
    current_time = now.time()
    return dt_time(9, 15) <= current_time <= dt_time(15, 30)


@st.fragment(run_every="5m")
def automatic_scanner():
    now = datetime.now(TZ)
    st.write("**இந்திய நேரம்:**", now.strftime("%d-%m-%Y %H:%M:%S"))

    if not is_market_open(now):
        st.warning(
            "இப்போது NSE regular market நேரம் இல்லை. "
            "திங்கள்–வெள்ளி காலை 9:15 முதல் மதியம் 3:30 வரை scan நடக்கும். "
            "விடுமுறை நாட்களையும் தனியாகச் சரிபார்க்கவும்."
        )
        if "scan_results" in st.session_state:
            st.subheader("கடைசியாக கிடைத்த Signals")
            st.dataframe(
                pd.DataFrame(st.session_state["scan_results"]),
                use_container_width=True,
                hide_index=True
            )
        return

    try:
        with st.spinner("NSE Equity stock list ஏற்றப்படுகிறது..."):
            symbols = get_nse_stocks()

        st.write(f"**மொத்த NSE EQ symbols:** {len(symbols)}")
        progress = st.progress(0)
        status = st.empty()

        results = scan_stocks(symbols, progress, status)

        st.session_state["scan_results"] = results
        st.session_state["scan_time"] = datetime.now(TZ).strftime(
            "%d-%m-%Y %H:%M:%S"
        )

        status.success("இந்த scan முடிந்தது.")
        st.metric("Total Bullish Marubozu Signals", len(results))

        if results:
            result_df = pd.DataFrame(results)
            st.dataframe(
                result_df,
                use_container_width=True,
                hide_index=True
            )
            csv = result_df.to_csv(index=False).encode("utf-8")
            st.download_button(
                "Download Signals CSV",
                data=csv,
                file_name="bullish_marubozu_signals.csv",
                mime="text/csv"
            )
        else:
            st.info("இந்த scan-ல் Bullish Marubozu signal கிடைக்கவில்லை.")

        st.caption("Last scan: " + st.session_state["scan_time"])

    except Exception as e:
        st.error(f"Scanner Error: {e}")
        st.caption(
            "NSE stock list அல்லது Yahoo Finance data கிடைக்கவில்லை என்றால் "
            "சிறிது நேரம் கழித்து மீண்டும் முயற்சிக்கவும்."
        )


automatic_scanner()

st.divider()
st.caption(
    "குறிப்பு: Streamlit app திறந்திருக்கும் போது மட்டுமே automatic refresh இயங்கும். "
    "Yahoo Finance-ல் rate limit, delayed data அல்லது missing symbols இருக்கலாம். "
    "இந்த scanner கல்வி நோக்கத்திற்கானது; buy/sell recommendation அல்ல."
)
