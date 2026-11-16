
import streamlit as st
import pandas as pd
import yfinance as yf
import requests
from io import StringIO
from datetime import datetime, time
from zoneinfo import ZoneInfo

st.set_page_config(
    page_title="Bullish Marubozu FVG Scanner",
    page_icon="📈",
    layout="wide"
)

IST = ZoneInfo("Asia/Kolkata")

st.title("📈 NSE Bullish Marubozu + FVG Scanner")
st.write("5 Minute | 3 Green Candles | Bullish FVG")

st.info(
    "1st Green + 2nd Bullish Marubozu + "
    "3rd Green + Bullish FVG"
)


@st.cache_data(ttl=3600)
def get_stocks():
    url = (
        "https://nsearchives.nseindia.com/"
        "content/equities/EQUITY_L.csv"
    )

    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "text/csv,*/*"
    }

    response = requests.get(
        url, headers=headers, timeout=30
    )
    response.raise_for_status()

    stocks = pd.read_csv(StringIO(response.text))
    stocks.columns = stocks.columns.str.strip()

    symbols = stocks["SYMBOL"].dropna().astype(str)

    return sorted(set(
        s.strip() for s in symbols if s.strip()
    ))


def get_stock_data(data, ticker):
    if data is None or data.empty:
        return None

    if isinstance(data.columns, pd.MultiIndex):
        for level in range(data.columns.nlevels):
            values = data.columns.get_level_values(level)

            if ticker in values:
                return data.xs(
                    ticker, axis=1, level=level
                )

        return None

    return data


def check_pattern(df, symbol, now):

    if df is None or df.empty:
        return []

    df = df.copy()

    if df.index.tz is None:
        df.index = df.index.tz_localize(IST)
    else:
        df.index = df.index.tz_convert(IST)

    # Ignore candles which have not closed
    df = df[
        df.index + pd.Timedelta(minutes=5) <= now
    ]

    df = df.dropna(
        subset=["Open", "High", "Low", "Close"]
    )

    results = []

    for i in range(2, len(df)):

        c1 = df.iloc[i - 2]
        c2 = df.iloc[i - 1]
        c3 = df.iloc[i]

        t1 = df.index[i - 2]
        t2 = df.index[i - 1]
        t3 = df.index[i]

        # Three consecutive 5-minute candles
        if t2 - t1 != pd.Timedelta(minutes=5):
            continue

        if t3 - t2 != pd.Timedelta(minutes=5):
            continue

        # Signal candle time
        if not (
            time(9, 30) <= t3.time() <= time(15, 25)
        ):
            continue

        # All three candles must be green
        first_green = c1["Close"] > c1["Open"]
        second_green = c2["Close"] > c2["Open"]
        third_green = c3["Close"] > c3["Open"]

        # Second candle Marubozu calculation
        candle_range = c2["High"] - c2["Low"]
        body = c2["Close"] - c2["Open"]

        if candle_range <= 0:
            continue

        upper_wick = c2["High"] - c2["Close"]
        lower_wick = c2["Open"] - c2["Low"]

        body_ratio = body / candle_range

        marubozu = (
            body_ratio >= 0.90
            and upper_wick / candle_range <= 0.05
            and lower_wick / candle_range <= 0.05
        )

        # Bullish FVG:
        # First candle HIGH < Third candle LOW
        fvg = c3["Low"] > c1["High"]

        if (
            first_green
            and second_green
            and third_green
            and marubozu
            and fvg
        ):

            results.append({
                "Stock": symbol,
                "Signal Time": (
                    t3 + pd.Timedelta(minutes=5)
                ).strftime("%I:%M %p"),
                "Price": round(float(c3["Close"]), 2),
                "FVG Lower": round(float(c1["High"]), 2),
                "FVG Upper": round(float(c3["Low"]), 2),
                "Marubozu Strength": round(
                    body_ratio * 100, 1
                )
            })

    return results


def scan_market():

    stocks = get_stocks()

    all_signals = []
    failed = 0

    progress = st.progress(0)
    status = st.empty()

    batch_size = 50
    total = len(stocks)

    for start in range(0, total, batch_size):

        batch = stocks[start:start + batch_size]

        tickers = [s + ".NS" for s in batch]

        try:
            data = yf.download(
                tickers=tickers,
                period="1d",
                interval="5m",
                group_by="ticker",
                auto_adjust=False,
                progress=False,
                threads=True,
                timeout=30
            )

            now = datetime.now(IST)

            for symbol in batch:

                ticker = symbol + ".NS"

                try:
                    candles = get_stock_data(
                        data, ticker
                    )

                    if candles is None:
                        failed += 1
                        continue

                    signals = check_pattern(
                        candles, symbol, now
                    )

                    all_signals.extend(signals)

                except Exception:
                    failed += 1

        except Exception:
            failed += len(batch)

        completed = min(
            start + batch_size, total
        )

        progress.progress(completed / total)

        status.write(
            f"Scanned: {completed}/{total} | "
            f"Signals: {len(all_signals)}"
        )

    progress.empty()
    status.empty()

    return all_signals, total, failed


@st.fragment(run_every="5m")
def live_scanner():

    now = datetime.now(IST)

    st.write(
        "Current Time:",
        now.strftime("%d-%m-%Y %I:%M:%S %p")
    )

    if now.weekday() >= 5:
        st.warning("Weekend - NSE Market Closed")
        return

    if not (
        time(9, 30) <= now.time() <= time(15, 30)
    ):
        st.info(
            "Scanner Active Time: "
            "Monday to Friday | 9:30 AM - 3:30 PM"
        )
        return

    st.caption(
        "Scanning NSE stocks automatically every 5 minutes "
        "while this website is open."
    )

    with st.spinner("Scanning All NSE Stocks..."):

        try:
            signals, total, failed = scan_market()

            st.session_state["signals"] = signals
            st.session_state["total"] = total
            st.session_state["failed"] = failed

            st.session_state["last_scan"] = (
                datetime.now(IST).strftime("%I:%M:%S %p")
            )

        except Exception as error:
            st.error(f"Scanner Error: {error}")
            return

    signals = st.session_state["signals"]

    col1, col2, col3 = st.columns(3)

    col1.metric("Total NSE Stocks", total)
    col2.metric("FVG Signals Found", len(signals))
    col3.metric("Failed / No Data", failed)

    st.caption(
        "Last Scan: " +
        st.session_state["last_scan"]
    )

    if signals:

        result = pd.DataFrame(signals)

        result = result.drop_duplicates(
            subset=["Stock", "Signal Time"]
        )

        st.success("Bullish Marubozu + FVG Found!")

        st.dataframe(
            result,
            use_container_width=True,
            hide_index=True
        )

    else:
        st.info(
            "No matching Bullish Marubozu + FVG found."
        )


live_scanner()
