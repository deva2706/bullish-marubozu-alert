
import streamlit as st
import pandas as pd
import yfinance as yf
import requests
from io import StringIO
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

# ---------------- SETTINGS ----------------

st.set_page_config(
    page_title="NSE FVG + Marubozu Scanner",
    page_icon="📈",
    layout="wide"
)

IST = ZoneInfo("Asia/Kolkata")
NSE_URL = "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv"

# ---------------- NSE STOCK LIST ----------------

@st.cache_data(ttl=3600)
def get_nse_stocks():
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "text/csv,application/csv,*/*"
    }

    response = requests.get(
        NSE_URL,
        headers=headers,
        timeout=20
    )
    response.raise_for_status()

    stocks = pd.read_csv(StringIO(response.text))
    stocks.columns = stocks.columns.str.strip()

    symbols = (
        stocks["SYMBOL"]
        .dropna()
        .astype(str)
        .str.strip()
        .unique()
        .tolist()
    )

    return [symbol + ".NS" for symbol in symbols]


# ---------------- MARUBOZU CHECK ----------------

def is_bullish_marubozu(candle):

    open_price = float(candle["Open"])
    close_price = float(candle["Close"])
    high_price = float(candle["High"])
    low_price = float(candle["Low"])

    candle_range = high_price - low_price

    if candle_range <= 0:
        return False

    if close_price <= open_price:
        return False

    body = close_price - open_price
    upper_wick = high_price - close_price
    lower_wick = open_price - low_price

    body_ratio = body / candle_range
    upper_ratio = upper_wick / candle_range
    lower_ratio = lower_wick / candle_range

    return (
        body_ratio >= 0.90
        and upper_ratio <= 0.05
        and lower_ratio <= 0.05
    )


# ---------------- TIME CONVERSION ----------------

def prepare_data(df, now):

    if df is None or df.empty:
        return pd.DataFrame()

    df = df.copy()

    if isinstance(df.columns, pd.MultiIndex):
        return pd.DataFrame()

    required = ["Open", "High", "Low", "Close"]

    if not all(col in df.columns for col in required):
        return pd.DataFrame()

    if df.index.tz is None:
        df.index = df.index.tz_localize(IST)
    else:
        df.index = df.index.tz_convert(IST)

    df = df[required].dropna()
    df = df[~df.index.duplicated(keep="last")]
    df = df.sort_index()

    # Ignore the candle that has not fully closed
    closed_before = now - timedelta(minutes=5)
    df = df[df.index <= closed_before]

    # NSE regular session
    df = df[
        (df.index.time >= time(9, 15))
        & (df.index.time < time(15, 30))
    ]

    return df


# ---------------- PATTERN CHECK ----------------

def check_pattern(df, ticker, now):

    signals = []

    if df.empty or len(df) < 4:
        return signals

    for i in range(2, len(df) - 1):

        first_time = df.index[i]
        second_time = df.index[i + 1]

        first = df.iloc[i]
        second = df.iloc[i + 1]
        two_candles_before = df.iloc[i - 2]
        middle_candle = df.iloc[i - 1]

        # Both candles must be consecutive 5-minute candles
        if second_time - first_time != timedelta(minutes=5):
            continue

        # FVG candles must also be consecutive
        if (
            first_time - df.index[i - 1] != timedelta(minutes=5)
            or df.index[i - 1] - df.index[i - 2]
            != timedelta(minutes=5)
        ):
            continue

        # Only today's signals
        if second_time.date() != now.date():
            continue

        signal_time = second_time + timedelta(minutes=5)

        # User's requested scan window: 9:30 AM to 3:30 PM
        if signal_time.time() < time(9, 30):
            continue

        if signal_time.time() > time(15, 30):
            continue

        # CONDITION 1: Both candles must be GREEN
        first_green = first["Close"] > first["Open"]
        second_green = second["Close"] > second["Open"]

        if not first_green or not second_green:
            continue

        # CONDITION 2: Second Open within 0% to 0.30%
        # Difference is absolute, so wick/sweep is allowed
        open_difference = (
            abs(second["Open"] - first["Close"])
            / first["Close"]
        ) * 100

        if open_difference > 0.30:
            continue

        # CONDITION 3: Second Close must be higher
        if second["Close"] <= first["Close"]:
            continue

        # CONDITION 4: Bullish FVG on first candle
        # First candle Low > High of the candle two bars before it
        bullish_fvg = (
            first["Low"] > two_candles_before["High"]
        )

        # CONDITION 5: Marubozu checks
        first_marubozu = is_bullish_marubozu(first)
        second_marubozu = is_bullish_marubozu(second)

        both_marubozu = (
            first_marubozu and second_marubozu
        )

        # Any ONE of these three conditions is enough
        qualifies = (
            bullish_fvg
            or second_marubozu
            or both_marubozu
        )

        if not qualifies:
            continue

        reasons = []

        if bullish_fvg:
            reasons.append("Bullish FVG on 1st candle")

        if second_marubozu:
            reasons.append("2nd candle Bullish Marubozu")

        if both_marubozu:
            reasons.append("Both candles Bullish Marubozu")

        signals.append({
            "Stock": ticker.replace(".NS", ""),
            "Signal Time": signal_time.strftime("%H:%M"),
            "1st Candle Close": round(float(first["Close"]), 2),
            "2nd Candle Open": round(float(second["Open"]), 2),
            "2nd Candle Close": round(float(second["Close"]), 2),
            "Open Difference %": round(float(open_difference), 3),
            "FVG": "YES" if bullish_fvg else "NO",
            "1st Marubozu": "YES" if first_marubozu else "NO",
            "2nd Marubozu": "YES" if second_marubozu else "NO",
            "Signal Reason": " + ".join(reasons)
        })

    return signals


# ---------------- YAHOO DATA DOWNLOAD ----------------

def get_ticker_data(data, ticker):

    if not isinstance(data.columns, pd.MultiIndex):
        return pd.DataFrame()

    if ticker in data.columns.get_level_values(0):
        return data[ticker].copy()

    if ticker in data.columns.get_level_values(1):
        return data.xs(ticker, axis=1, level=1).copy()

    return pd.DataFrame()


# ---------------- MARKET SCANNER ----------------

def scan_market():

    now = datetime.now(IST)

    if now.weekday() >= 5:
        st.warning("இன்று NSE regular market விடுமுறை நாள்.")
        return

    if not (time(9, 30) <= now.time() <= time(15, 30)):
        st.info(
            "NSE scanner நேரம்: காலை 9:30 முதல் மாலை 3:30 வரை. "
            "Market நேரத்தில் மீண்டும் திறந்து பாருங்கள்."
        )
        return

    try:
        stocks = get_nse_stocks()
    except Exception as e:
        st.error(f"NSE stock list கிடைக்கவில்லை: {e}")
        return

    st.write(f"**Total NSE stocks:** {len(stocks)}")

    all_signals = []
    failed_batches = 0

    progress = st.progress(0)
    status = st.empty()

    batch_size = 50
    total_batches = (len(stocks) + batch_size - 1) // batch_size

    for batch_number, start in enumerate(
        range(0, len(stocks), batch_size), start=1
    ):

        batch = stocks[start:start + batch_size]

        try:
            data = yf.download(
                tickers=batch,
                period="5d",
                interval="5m",
                group_by="ticker",
                auto_adjust=False,
                threads=True,
                progress=False
            )

            for ticker in batch:

                try:
                    ticker_data = get_ticker_data(data, ticker)

                    if ticker_data.empty:
                        continue

                    ticker_data = prepare_data(ticker_data, now)

                    if ticker_data.empty:
                        continue

                    found = check_pattern(
                        ticker_data,
                        ticker,
                        now
                    )

                    all_signals.extend(found)

                except Exception:
                    continue

        except Exception:
            failed_batches += 1

        progress.progress(batch_number / total_batches)

        status.write(
            f"Scanning batch {batch_number}/{total_batches}..."
        )

    progress.empty()
    status.empty()

    st.subheader("📊 Scanner Results")

    if all_signals:

        results = pd.DataFrame(all_signals)

        results = results.drop_duplicates(
            subset=["Stock", "Signal Time"],
            keep="last"
        )

        results = results.sort_values(
            by="Signal Time",
            ascending=False
        )

        st.success(f"Total Signal Found: {len(results)}")

        st.dataframe(
            results,
            use_container_width=True,
            hide_index=True
        )

        csv = results.to_csv(index=False).encode("utf-8")

        st.download_button(
            "Download Signals CSV",
            data=csv,
            file_name="NSE_FVG_Marubozu_Signals.csv",
            mime="text/csv"
        )

    else:
        st.warning("இப்போது எந்த Signal-மும் கிடைக்கவில்லை.")

    if failed_batches:
        st.caption(
            f"{failed_batches} data batches download ஆகவில்லை. "
            "சில stocks-க்கு Yahoo Finance data கிடைக்காமல் இருக்கலாம்."
        )

    st.caption(
        "Last scan: " + datetime.now(IST).strftime("%d-%m-%Y %H:%M:%S")
    )


# ---------------- WEBSITE UI ----------------

st.title("📈 NSE FVG + Bullish Marubozu Scanner")

st.write(
    "NSE All Stocks | 5 Minute | "
    "2 Green Candles | FVG / Marubozu"
)

st.info(
    "ஒரு Signal கிடைக்க, இரண்டு Green Candles மற்றும் "
    "Open Difference / Close நிபந்தனைகள் கட்டாயம். "
    "FVG அல்லது Marubozu நிபந்தனைகளில் ஏதாவது ஒன்று போதும்."
)

st.caption(
    "Website திறந்திருக்கும் போது ஒவ்வொரு 5 நிமிடத்திற்கும் scan நடக்கும்."
)


@st.fragment(run_every="5m")
def live_scanner():

    scan_market()


live_scanner()
