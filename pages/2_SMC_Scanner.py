

import streamlit as st
import pandas as pd
import yfinance as yf
import requests
import plotly.graph_objects as go

from io import StringIO
from datetime import datetime, timedelta, time
from zoneinfo import ZoneInfo

# ================= SETTINGS =================

st.set_page_config(
    page_title="NSE SMC Scanner",
    page_icon="📊",
    layout="wide"
)

IST = ZoneInfo("Asia/Kolkata")
NSE_URL = "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv"

# ================= NSE STOCK LIST =================

@st.cache_data(ttl=3600)
def get_nse_stocks():

    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "text/csv,*/*"
    }

    response = requests.get(
        NSE_URL,
        headers=headers,
        timeout=30
    )
    response.raise_for_status()

    stocks = pd.read_csv(StringIO(response.text))
    stocks.columns = stocks.columns.str.strip()

    return [
        s + ".NS"
        for s in stocks["SYMBOL"].dropna()
        .astype(str).str.strip().unique()
    ]


# ================= YAHOO DATA =================

def get_ticker_data(data, ticker):

    if data is None or data.empty:
        return pd.DataFrame()

    if not isinstance(data.columns, pd.MultiIndex):
        return pd.DataFrame()

    if ticker in data.columns.get_level_values(0):
        return data[ticker].copy()

    if ticker in data.columns.get_level_values(1):
        return data.xs(ticker, axis=1, level=1).copy()

    return pd.DataFrame()


def prepare_data(df, now):

    if df is None or df.empty:
        return pd.DataFrame()

    df = df.copy()

    if isinstance(df.columns, pd.MultiIndex):
        return pd.DataFrame()

    required = ["Open", "High", "Low", "Close", "Volume"]

    if not all(c in df.columns for c in required):
        return pd.DataFrame()

    if df.index.tz is None:
        df.index = df.index.tz_localize(IST)
    else:
        df.index = df.index.tz_convert(IST)

    df = df[required].dropna()
    df = df.sort_index()
    df = df[~df.index.duplicated(keep="last")]

    # Completed 5-minute candles only
    df = df[
        df.index + timedelta(minutes=5) <= now
    ]

    df = df[
        (df.index.time >= time(9, 15))
        & (df.index.time < time(15, 30))
    ]

    # Today's candles only
    df = df[df.index.date == now.date()]

    return df


# ================= SMC DETECTION =================

def analyze_smc(df, ticker):

    signals = []
    events = []

    if df.empty or len(df) < 15:
        return signals, events

    df = df.copy().reset_index()
    time_col = df.columns[0]

    df = df.rename(columns={time_col: "CandleTime"})

    # Detect all liquidity sweeps
    bull_sweeps = []
    bear_sweeps = []

    for i in range(10, len(df)):

        candle = df.iloc[i]
        previous = df.iloc[i-10:i]

        old_low = previous["Low"].min()
        old_high = previous["High"].max()

        # Sell-side liquidity sweep
        if (
            candle["Low"] < old_low
            and candle["Close"] > old_low
        ):
            bull_sweeps.append(i)

            events.append({
                "Index": i,
                "Time": candle["CandleTime"],
                "Type": "Bullish Liquidity Sweep",
                "Price": candle["Low"]
            })

        # Buy-side liquidity sweep
        if (
            candle["High"] > old_high
            and candle["Close"] < old_high
        ):
            bear_sweeps.append(i)

            events.append({
                "Index": i,
                "Time": candle["CandleTime"],
                "Type": "Bearish Liquidity Sweep",
                "Price": candle["High"]
            })

    # Find latest bullish and bearish sweep
    bull_sweep = (
        bull_sweeps[-1] if bull_sweeps else None
    )

    bear_sweep = (
        bear_sweeps[-1] if bear_sweeps else None
    )

    # Structure break after sweep
    bull_break = None
    bear_break = None

    if bull_sweep is not None:

        for k in range(bull_sweep + 1, len(df)):

            previous = df.iloc[max(bull_sweep + 1, k-5):k]

            if len(previous) < 3:
                continue

            if df.iloc[k]["Close"] > previous["High"].max():

                bull_break = k

                events.append({
                    "Index": k,
                    "Time": df.iloc[k]["CandleTime"],
                    "Type": "Bullish CHOCH/BOS",
                    "Price": df.iloc[k]["Close"]
                })

    if bear_sweep is not None:

        for k in range(bear_sweep + 1, len(df)):

            previous = df.iloc[max(bear_sweep + 1, k-5):k]

            if len(previous) < 3:
                continue

            if df.iloc[k]["Close"] < previous["Low"].min():

                bear_break = k

                events.append({
                    "Index": k,
                    "Time": df.iloc[k]["CandleTime"],
                    "Type": "Bearish CHOCH/BOS",
                    "Price": df.iloc[k]["Close"]
                })

    # Three-candle FVG
    fvg_type = None
    fvg_low = None
    fvg_high = None

    if len(df) >= 3:

        first = df.iloc[-3]
        middle = df.iloc[-2]
        last = df.iloc[-1]

        if last["Low"] > first["High"]:

            fvg_type = "Bullish"
            fvg_low = first["High"]
            fvg_high = last["Low"]

            events.append({
                "Index": len(df)-1,
                "Time": last["CandleTime"],
                "Type": "Bullish FVG",
                "Price": fvg_high
            })

        elif last["High"] < first["Low"]:

            fvg_type = "Bearish"
            fvg_low = last["High"]
            fvg_high = first["Low"]

            events.append({
                "Index": len(df)-1,
                "Time": last["CandleTime"],
                "Type": "Bearish FVG",
                "Price": fvg_low
            })

    # Volume confirmation
    avg_volume = df["Volume"].iloc[-21:-1].mean()
    current = df.iloc[-1]

    volume_ok = (
        pd.notna(avg_volume)
        and avg_volume > 0
        and current["Volume"] > avg_volume
    )

    last_index = len(df)-1

    # Only signal when sweep, structure and FVG
    # are aligned with the latest completed candle.

    if (
        bull_sweep is not None
        and bull_break is not None
        and bull_break <= last_index
        and last_index - bull_sweep <= 12
        and fvg_type == "Bullish"
        and volume_ok
        and current["Close"] > current["Open"]
    ):

        signals.append({
            "Stock": ticker.replace(".NS", ""),
            "Signal": "BULLISH",
            "Signal Time": current["CandleTime"].strftime("%H:%M"),
            "Price": round(float(current["Close"]), 2),
            "FVG Low": round(float(fvg_low), 2),
            "FVG High": round(float(fvg_high), 2),
            "Liquidity": "Sell-side Sweep",
            "Structure": "Bullish CHOCH/BOS",
            "Volume": "High"
        })

    if (
        bear_sweep is not None
        and bear_break is not None
        and last_index - bear_sweep <= 12
        and fvg_type == "Bearish"
        and volume_ok
        and current["Close"] < current["Open"]
    ):

        signals.append({
            "Stock": ticker.replace(".NS", ""),
            "Signal": "BEARISH",
            "Signal Time": current["CandleTime"].strftime("%H:%M"),
            "Price": round(float(current["Close"]), 2),
            "FVG Low": round(float(fvg_low), 2),
            "FVG High": round(float(fvg_high), 2),
            "Liquidity": "Buy-side Sweep",
            "Structure": "Bearish CHOCH/BOS",
            "Volume": "High"
        })

    return signals, events


# ================= CHART =================

def show_smc_chart(df, ticker, signal):

    if df.empty:
        st.warning("Chart data கிடைக்கவில்லை.")
        return

    chart_df = df.tail(100).copy()

    signals, events = analyze_smc(df, ticker)

    fig = go.Figure()

    # Candlestick chart
    fig.add_trace(go.Candlestick(
        x=chart_df.index,
        open=chart_df["Open"],
        high=chart_df["High"],
        low=chart_df["Low"],
        close=chart_df["Close"],
        name="Price"
    ))

    # Volume bars
    fig.add_trace(go.Bar(
        x=chart_df.index,
        y=chart_df["Volume"],
        name="Volume",
        marker_color="rgba(120,120,120,0.30)",
        yaxis="y2"
    ))

    chart_start = chart_df.index[0]
    chart_end = chart_df.index[-1] + timedelta(minutes=5)

    # Mark SMC events
    for event in events:

        event_time = event["Time"]

        if event_time not in chart_df.index:
            continue

        event_type = event["Type"]
        price = event["Price"]

        if "Bullish Liquidity" in event_type:

            fig.add_annotation(
                x=event_time,
                y=price,
                text="SSL SWEEP",
                showarrow=True,
                arrowhead=2,
                arrowcolor="green",
                font=dict(color="green")
            )

        elif "Bearish Liquidity" in event_type:

            fig.add_annotation(
                x=event_time,
                y=price,
                text="BSL SWEEP",
                showarrow=True,
                arrowhead=2,
                arrowcolor="red",
                font=dict(color="red")
            )

        elif "Bullish CHOCH" in event_type:

            fig.add_annotation(
                x=event_time,
                y=price,
                text="BULLISH CHOCH/BOS",
                showarrow=True,
                arrowhead=2,
                arrowcolor="green",
                font=dict(color="green")
            )

        elif "Bearish CHOCH" in event_type:

            fig.add_annotation(
                x=event_time,
                y=price,
                text="BEARISH CHOCH/BOS",
                showarrow=True,
                arrowhead=2,
                arrowcolor="red",
                font=dict(color="red")
            )

    # Draw current FVG zone
    if signals:

        latest = signals[-1]

        low = latest["FVG Low"]
        high = latest["FVG High"]

        fig.add_shape(
            type="rect",
            x0=chart_df.index[-3],
            x1=chart_end,
            y0=low,
            y1=high,
            fillcolor=(
                "rgba(0,180,0,0.18)"
                if signal == "BULLISH"
                else "rgba(255,0,0,0.18)"
            ),
            line=dict(
                color="green" if signal == "BULLISH" else "red",
                width=1
            )
        )

        fig.add_annotation(
            x=chart_df.index[-1],
            y=high,
            text=f"{signal} FVG",
            showarrow=False
        )

    # Price and entry reference
    last_price = float(chart_df["Close"].iloc[-1])

    fig.add_hline(
        y=last_price,
        line_dash="dot",
        line_color="blue",
        annotation_text=f"Price: {last_price:.2f}"
    )

    fig.update_layout(
        title=f"{ticker.replace('.NS','')} - SMC Analysis",
        xaxis_title="Time",
        yaxis_title="Price",
        xaxis_rangeslider_visible=False,
        height=650,
        template="plotly_dark",
        hovermode="x unified",
        yaxis=dict(domain=[0.25, 1]),
        yaxis2=dict(
            domain=[0, 0.18],
            title="Volume"
        )
    )

    st.plotly_chart(
        fig,
        use_container_width=True
    )

    st.caption(
        "Chart labels are rule-based estimates, not verified institutional orders."
    )


# ================= SCAN MARKET =================

def scan_market():

    now = datetime.now(IST)

    if now.weekday() >= 5:
        st.warning("Weekend - NSE market closed.")
        return

    if not (time(9, 30) <= now.time() <= time(15, 30)):
        st.info(
            "NSE regular session-ல் 9:30 AM முதல் 3:30 PM வரை scan செய்யவும்."
        )
        return

    try:
        stocks = get_nse_stocks()
    except Exception as e:
        st.error(f"NSE stock list error: {e}")
        return

    st.write(f"Total NSE stocks: {len(stocks)}")

    all_signals = []
    failed_batches = 0

    batch_size = 50
    total_batches = (
        len(stocks) + batch_size - 1
    ) // batch_size

    progress = st.progress(0)
    status = st.empty()

    for batch_no, start in enumerate(
        range(0, len(stocks), batch_size), 1
    ):

        batch = stocks[start:start+batch_size]

        try:
            data = yf.download(
                tickers=batch,
                period="5d",
                interval="5m",
                group_by="ticker",
                auto_adjust=False,
                threads=True,
                progress=False,
                timeout=30
            )

            for ticker in batch:

                try:
                    ticker_df = get_ticker_data(data, ticker)
                    ticker_df = prepare_data(ticker_df, now)

                    found, _ = analyze_smc(
                        ticker_df,
                        ticker
                    )

                    all_signals.extend(found)

                except Exception:
                    continue

        except Exception:
            failed_batches += 1

        progress.progress(batch_no / total_batches)

        status.write(
            f"Scanning {batch_no}/{total_batches} batches..."
        )

    progress.empty()
    status.empty()

    st.session_state["smc_results"] = all_signals
    st.session_state["smc_scan_time"] = now.strftime(
        "%d-%m-%Y %H:%M:%S"
    )

    if failed_batches:
        st.warning(
            f"{failed_batches} batches failed to download."
        )


# ================= WEBSITE UI =================

st.title("📊 NSE SMART MONEY CONCEPTS SCANNER")

st.write(
    "NSE All Stocks | 5 Minutes | "
    "Bullish + Bearish | Liquidity + CHOCH/BOS + FVG"
)

st.info(
    "Signal conditions: Liquidity Sweep + Structure Break "
    "+ Latest FVG + Above-average Volume."
)

st.warning(
    "Yahoo Finance data may be delayed, incomplete or unavailable. "
    "This is an educational scanner, not a trading recommendation."
)

if "smc_results" not in st.session_state:
    st.session_state["smc_results"] = []

if st.button("🔄 SCAN ALL NSE STOCKS", type="primary"):
    scan_market()

results = st.session_state["smc_results"]

if results:

    result_df = pd.DataFrame(results)

    result_df = result_df.drop_duplicates(
        subset=["Stock", "Signal"],
        keep="last"
    )

    col1, col2, col3 = st.columns(3)

    col1.metric("Total Signals", len(result_df))
    col2.metric(
        "Bullish",
        len(result_df[result_df["Signal"] == "BULLISH"])
    )
    col3.metric(
        "Bearish",
        len(result_df[result_df["Signal"] == "BEARISH"])
    )

    st.subheader("📋 SMC Signal Results")

    st.dataframe(
        result_df,
        use_container_width=True,
        hide_index=True
    )

    st.download_button(
        "Download CSV",
        result_df.to_csv(index=False).encode("utf-8"),
        "NSE_SMC_Signals.csv",
        "text/csv"
    )

    st.divider()
    st.subheader("📈 Individual SMC Chart")

    selected_stock = st.selectbox(
        "Select Stock to View Chart",
        sorted(result_df["Stock"].unique())
    )

    selected_signal = result_df[
        result_df["Stock"] == selected_stock
    ].iloc[-1]

    st.write(
        f"**Signal:** {selected_signal['Signal']} | "
        f"**Price:** ₹{selected_signal['Price']}"
    )

    if st.button("SHOW SMC CHART"):

        ticker = selected_stock + ".NS"
        now = datetime.now(IST)

        try:
            raw = yf.download(
                ticker,
                period="5d",
                interval="5m",
                auto_adjust=False,
                progress=False
            )

            if isinstance(raw.columns, pd.MultiIndex):
                raw.columns = raw.columns.get_level_values(0)

            chart_data = prepare_data(raw, now)

            if not chart_data.empty:
                show_smc_chart(
                    chart_data,
                    ticker,
                    selected_signal["Signal"]
                )
            else:
                st.warning("Chart data கிடைக்கவில்லை.")

        except Exception as e:
            st.error(f"Chart error: {e}")

    st.caption(
        "Last scan: " +
        st.session_state.get("smc_scan_time", "Not available")
    )

else:
    st.info(
        "முதலில் SCAN ALL NSE STOCKS அழுத்துங்கள். "
        "Signal கிடைத்தால் Chart பார்க்கலாம்."
    )

st.caption(
    "Only completed 5-minute candles are considered. "
    "Manual scan button-ஐ அழுத்தி மீண்டும் scan செய்ய வேண்டும்."
)
