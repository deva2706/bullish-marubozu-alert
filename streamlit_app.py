
import streamlit as st
import pandas as pd
import numpy as np
import yfinance as yf
import requests
import plotly.graph_objects as go

from io import StringIO
from datetime import datetime, timedelta, time
from zoneinfo import ZoneInfo

# ==================================================
# SETTINGS
# ==================================================

st.set_page_config(
    page_title="Advanced NSE SMC Scanner",
    page_icon="📊",
    layout="wide"
)

IST = ZoneInfo("Asia/Kolkata")
NSE_URL = "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv"

HISTORY_DAYS = 30
YAHOO_PERIOD = "60d"
INTERVAL = "5m"


# ==================================================
# NSE STOCK LIST
# ==================================================

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

    symbols = (
        stocks["SYMBOL"]
        .dropna()
        .astype(str)
        .str.strip()
        .unique()
    )

    return [s + ".NS" for s in symbols if s]


# ==================================================
# YAHOO DOWNLOAD
# ==================================================

@st.cache_data(ttl=60, show_spinner=False)
def download_batch(batch):

    return yf.download(
        tickers=list(batch),
        period=YAHOO_PERIOD,
        interval=INTERVAL,
        group_by="ticker",
        auto_adjust=False,
        threads=True,
        progress=False,
        timeout=30
    )


def get_ticker_data(data, ticker):

    if data is None or data.empty:
        return pd.DataFrame()

    if not isinstance(data.columns, pd.MultiIndex):
        if len(data.columns) > 0:
            return data.copy()
        return pd.DataFrame()

    for level in range(data.columns.nlevels):

        if ticker in data.columns.get_level_values(level):
            try:
                result = data.xs(
                    ticker,
                    axis=1,
                    level=level
                )

                if isinstance(result, pd.DataFrame):
                    return result.copy()

            except Exception:
                pass

    return pd.DataFrame()


# ==================================================
# PREPARE 5 MIN DATA
# ==================================================

def prepare_data(df, now):

    if df is None or df.empty:
        return pd.DataFrame()

    df = df.copy()

    if isinstance(df.columns, pd.MultiIndex):
        return pd.DataFrame()

    required = [
        "Open", "High", "Low", "Close", "Volume"
    ]

    if not all(c in df.columns for c in required):
        return pd.DataFrame()

    if df.index.tz is None:
        df.index = df.index.tz_localize(IST)
    else:
        df.index = df.index.tz_convert(IST)

    df = df[required].dropna()
    df = df.sort_index()

    df = df[~df.index.duplicated(keep="last")]

    # Completed candles only
    df = df[
        df.index + timedelta(minutes=5) <= now
    ]

    # NSE regular session
    df = df[
        (df.index.time >= time(9, 15))
        & (df.index.time < time(15, 30))
    ]

    # Last 30 calendar days
    start_date = now - timedelta(days=HISTORY_DAYS)

    df = df[
        df.index >= start_date
    ]

    return df


# ==================================================
# MULTI TIMEFRAME
# ==================================================

def resample_data(df, rule):

    if df.empty:
        return pd.DataFrame()

    result = df.resample(rule).agg({
        "Open": "first",
        "High": "max",
        "Low": "min",
        "Close": "last",
        "Volume": "sum"
    }).dropna()

    return result


def get_trend(df):

    if len(df) < 20:
        return "INSUFFICIENT DATA"

    ema20 = df["Close"].ewm(
        span=20, adjust=False
    ).mean()

    ema50 = df["Close"].ewm(
        span=50, adjust=False
    ).mean()

    price = df["Close"].iloc[-1]

    if price > ema20.iloc[-1] > ema50.iloc[-1]:
        return "BULLISH"

    if price < ema20.iloc[-1] < ema50.iloc[-1]:
        return "BEARISH"

    return "SIDEWAYS"


def get_mtf_analysis(df):

    frames = {
        "5 Minute": df,
        "15 Minute": resample_data(df, "15min"),
        "1 Hour": resample_data(df, "1h"),
        "4 Hour": resample_data(df, "4h"),
        "Daily": resample_data(df, "1D")
    }

    return {
        name: get_trend(frame)
        for name, frame in frames.items()
    }


# ==================================================
# PREVIOUS DAY / WEEK / MONTH LEVELS
# ==================================================

def get_previous_levels(df):

    levels = {}

    if df.empty:
        return levels

    daily = df.groupby(df.index.date).agg({
        "High": "max",
        "Low": "min"
    })

    if len(daily) >= 2:

        levels["PDH"] = float(daily["High"].iloc[-2])
        levels["PDL"] = float(daily["Low"].iloc[-2])

    weekly = df.groupby(
        df.index.to_period("W-FRI")
    ).agg({
        "High": "max",
        "Low": "min"
    })

    if len(weekly) >= 2:

        levels["PWH"] = float(weekly["High"].iloc[-2])
        levels["PWL"] = float(weekly["Low"].iloc[-2])

    monthly = df.groupby(
        df.index.to_period("M")
    ).agg({
        "High": "max",
        "Low": "min"
    })

    if len(monthly) >= 2:

        levels["PMH"] = float(monthly["High"].iloc[-2])
        levels["PML"] = float(monthly["Low"].iloc[-2])

    return levels


# ==================================================
# ADVANCED SMC ANALYSIS
# ==================================================

def analyze_smc(df, ticker):

    signals = []
    events = []

    if df.empty or len(df) < 20:
        return signals, events, {}

    df = df.copy()

    # Swing points
    for i in range(2, len(df) - 2):

        candle = df.iloc[i]

        if candle["High"] >= df["High"].iloc[i-2:i+3].max():

            events.append({
                "Time": df.index[i],
                "Type": "Swing High",
                "Price": float(candle["High"])
            })

        if candle["Low"] <= df["Low"].iloc[i-2:i+3].min():

            events.append({
                "Time": df.index[i],
                "Type": "Swing Low",
                "Price": float(candle["Low"])
            })

    # Liquidity and structure
    bull_sweeps = []
    bear_sweeps = []

    last_structure = None

    for i in range(10, len(df)):

        candle = df.iloc[i]
        previous = df.iloc[i-10:i]

        old_high = previous["High"].max()
        old_low = previous["Low"].min()

        # SSL Sweep
        if (
            candle["Low"] < old_low
            and candle["Close"] > old_low
        ):

            bull_sweeps.append(i)

            events.append({
                "Time": df.index[i],
                "Type": "SSL Sweep",
                "Price": float(candle["Low"])
            })

        # BSL Sweep
        if (
            candle["High"] > old_high
            and candle["Close"] < old_high
        ):

            bear_sweeps.append(i)

            events.append({
                "Time": df.index[i],
                "Type": "BSL Sweep",
                "Price": float(candle["High"])
            })

        # BOS / CHOCH / MSS
        previous_structure = df.iloc[max(0, i-5):i]

        if len(previous_structure) >= 3:

            if candle["Close"] > previous_structure["High"].max():

                event_type = (
                    "Bullish CHOCH"
                    if last_structure == "BEARISH"
                    else "Bullish BOS"
                )

                if last_structure is None:
                    event_type = "Bullish BOS"

                if (
                    bull_sweeps
                    and i > bull_sweeps[-1]
                    and last_structure == "BEARISH"
                ):
                    event_type = "Bullish MSS"

                last_structure = "BULLISH"

                events.append({
                    "Time": df.index[i],
                    "Type": event_type,
                    "Price": float(candle["Close"])
                })

            elif candle["Close"] < previous_structure["Low"].min():

                event_type = (
                    "Bearish CHOCH"
                    if last_structure == "BULLISH"
                    else "Bearish BOS"
                )

                if last_structure is None:
                    event_type = "Bearish BOS"

                if (
                    bear_sweeps
                    and i > bear_sweeps[-1]
                    and last_structure == "BULLISH"
                ):
                    event_type = "Bearish MSS"

                last_structure = "BEARISH"

                events.append({
                    "Time": df.index[i],
                    "Type": event_type,
                    "Price": float(candle["Close"])
                })

    # ==================================================
    # FVG / IFVG
    # ==================================================

    fvg_zones = []

    for i in range(2, len(df)):

        first = df.iloc[i-2]
        middle = df.iloc[i-1]
        last = df.iloc[i]

        # Bullish FVG
        if last["Low"] > first["High"]:

            low = float(first["High"])
            high = float(last["Low"])

            zone = {
                "Type": "Bullish FVG",
                "Time": df.index[i],
                "Low": low,
                "High": high
            }

            fvg_zones.append(zone)

            events.append({
                "Time": df.index[i],
                "Type": "Bullish FVG",
                "Price": high,
                "Zone Low": low,
                "Zone High": high
            })

        # Bearish FVG
        elif last["High"] < first["Low"]:

            low = float(last["High"])
            high = float(first["Low"])

            zone = {
                "Type": "Bearish FVG",
                "Time": df.index[i],
                "Low": low,
                "High": high
            }

            fvg_zones.append(zone)

            events.append({
                "Time": df.index[i],
                "Type": "Bearish FVG",
                "Price": low,
                "Zone Low": low,
                "Zone High": high
            })

    # IFVG detection
    for zone in fvg_zones:

        later = df[df.index > zone["Time"]]

        if later.empty:
            continue

        if zone["Type"] == "Bullish FVG":

            if (later["Close"] < zone["Low"]).any():

                events.append({
                    "Time": later.index[
                        later["Close"].values < zone["Low"]
                    ][0],
                    "Type": "Bearish IFVG",
                    "Price": zone["Low"],
                    "Zone Low": zone["Low"],
                    "Zone High": zone["High"]
                })

        else:

            if (later["Close"] > zone["High"]).any():

                events.append({
                    "Time": later.index[
                        later["Close"].values > zone["High"]
                    ][0],
                    "Type": "Bullish IFVG",
                    "Price": zone["High"],
                    "Zone Low": zone["Low"],
                    "Zone High": zone["High"]
                })

    # ==================================================
    # ORDER BLOCK / BREAKER / MITIGATION
    # ==================================================

    ob_zones = []

    for event in events:

        if event["Type"] not in [
            "Bullish BOS", "Bullish CHOCH",
            "Bullish MSS", "Bearish BOS",
            "Bearish CHOCH", "Bearish MSS"
        ]:
            continue

        event_time = event["Time"]

        pos = df.index.get_loc(event_time)

        if isinstance(pos, slice):
            continue

        direction = (
            "BULLISH"
            if event["Type"].startswith("Bullish")
            else "BEARISH"
        )

        for j in range(pos-1, max(pos-6, -1), -1):

            candle = df.iloc[j]

            opposite = (
                candle["Close"] < candle["Open"]
                if direction == "BULLISH"
                else candle["Close"] > candle["Open"]
            )

            if opposite:

                zone = {
                    "Type": direction + " Order Block",
                    "Time": df.index[j],
                    "Low": float(candle["Low"]),
                    "High": float(candle["High"])
                }

                ob_zones.append(zone)

                events.append({
                    "Time": df.index[j],
                    "Type": zone["Type"],
                    "Price": float(candle["Close"]),
                    "Zone Low": zone["Low"],
                    "Zone High": zone["High"]
                })

                break

    # Mitigation / Breaker estimates
    for zone in ob_zones:

        later = df[df.index > zone["Time"]]

        if later.empty:
            continue

        if zone["Type"] == "BULLISH Order Block":

            if (
                (later["Low"] <= zone["High"])
                & (later["High"] >= zone["Low"])
            ).any():

                events.append({
                    "Time": later.index[
                        (
                            (later["Low"] <= zone["High"])
                            & (later["High"] >= zone["Low"])
                        ).values
                    ][0],
                    "Type": "Bullish Mitigation",
                    "Price": zone["Low"],
                    "Zone Low": zone["Low"],
                    "Zone High": zone["High"]
                })

            if (later["Close"] < zone["Low"]).any():

                events.append({
                    "Time": later.index[
                        (later["Close"] < zone["Low"]).values
                    ][0],
                    "Type": "Bearish Breaker",
                    "Price": zone["Low"],
                    "Zone Low": zone["Low"],
                    "Zone High": zone["High"]
                })

        else:

            if (
                (later["Low"] <= zone["High"])
                & (later["High"] >= zone["Low"])
            ).any():

                events.append({
                    "Time": later.index[
                        (
                            (later["Low"] <= zone["High"])
                            & (later["High"] >= zone["Low"])
                        ).values
                    ][0],
                    "Type": "Bearish Mitigation",
                    "Price": zone["High"],
                    "Zone Low": zone["Low"],
                    "Zone High": zone["High"]
                })

            if (later["Close"] > zone["High"]).any():

                events.append({
                    "Time": later.index[
                        (later["Close"] > zone["High"]).values
                    ][0],
                    "Type": "Bullish Breaker",
                    "Price": zone["High"],
                    "Zone Low": zone["Low"],
                    "Zone High": zone["High"]
                })

    # ==================================================
    # EQUAL HIGH / EQUAL LOW
    # ==================================================

    for i in range(10, len(df)):

        previous = df.iloc[i-10:i]

        high = float(df["High"].iloc[i])
        low = float(df["Low"].iloc[i])

        tolerance = max(
            high * 0.001,
            0.01
        )

        if abs(high - previous["High"].max()) <= tolerance:

            events.append({
                "Time": df.index[i],
                "Type": "Equal High (EQH)",
                "Price": high
            })

        if abs(low - previous["Low"].min()) <= tolerance:

            events.append({
                "Time": df.index[i],
                "Type": "Equal Low (EQL)",
                "Price": low
            })

    # ==================================================
    # DISPLACEMENT
    # ==================================================

    body = abs(df["Close"] - df["Open"])

    atr = (
        df["High"] - df["Low"]
    ).rolling(14).mean()

    for i in range(14, len(df)):

        if (
            pd.notna(atr.iloc[i])
            and body.iloc[i] > atr.iloc[i] * 1.5
        ):

            direction = (
                "Bullish"
                if df["Close"].iloc[i] > df["Open"].iloc[i]
                else "Bearish"
            )

            events.append({
                "Time": df.index[i],
                "Type": direction + " Displacement",
                "Price": float(df["Close"].iloc[i])
            })

    # ==================================================
    # PREMIUM / DISCOUNT / OTE
    # ==================================================

    range_high = float(df["High"].max())
    range_low = float(df["Low"].min())

    equilibrium = (range_high + range_low) / 2

    current = df.iloc[-1]

    if current["Close"] > equilibrium:
        pd_zone = "PREMIUM"
    elif current["Close"] < equilibrium:
        pd_zone = "DISCOUNT"
    else:
        pd_zone = "EQUILIBRIUM"

    # Simplified OTE reference from 30-day range
    ote = {}

    if current["Close"] >= equilibrium:

        ote["Low"] = range_high - (
            range_high - range_low
        ) * 0.79

        ote["High"] = range_high - (
            range_high - range_low
        ) * 0.62

        ote["Direction"] = "Bullish reference"

    else:

        ote["Low"] = range_low + (
            range_high - range_low
        ) * 0.62

        ote["High"] = range_low + (
            range_high - range_low
        ) * 0.79

        ote["Direction"] = "Bearish reference"

    # ==================================================
    # VOLUME CONFIRMATION
    # ==================================================

    avg_volume = df["Volume"].iloc[-21:-1].mean()

    volume_ok = (
        pd.notna(avg_volume)
        and avg_volume > 0
        and current["Volume"] > avg_volume
    )

    # ==================================================
    # FINAL SIGNAL
    # ==================================================

    last_index = len(df) - 1

    latest_bull_sweep = (
        bull_sweeps[-1] if bull_sweeps else None
    )

    latest_bear_sweep = (
        bear_sweeps[-1] if bear_sweeps else None
    )

    latest_events = [
        e for e in events
        if e["Time"] == df.index[-1]
    ]

    bull_fvg = any(
        e["Type"] == "Bullish FVG"
        for e in latest_events
    )

    bear_fvg = any(
        e["Type"] == "Bearish FVG"
        for e in latest_events
    )

    bull_structure = [
        e for e in events
        if e["Type"] in [
            "Bullish BOS", "Bullish CHOCH", "Bullish MSS"
        ]
    ]

    bear_structure = [
        e for e in events
        if e["Type"] in [
            "Bearish BOS", "Bearish CHOCH", "Bearish MSS"
        ]
    ]

    latest_bull_break = (
        bull_structure[-1]
        if bull_structure else None
    )

    latest_bear_break = (
        bear_structure[-1]
        if bear_structure else None
    )

    bull_ok = (
        latest_bull_sweep is not None
        and last_index - latest_bull_sweep <= 12
        and latest_bull_break is not None
        and latest_bull_break["Time"] > df.index[latest_bull_sweep]
        and bull_fvg
        and volume_ok
        and current["Close"] > current["Open"]
    )

    bear_ok = (
        latest_bear_sweep is not None
        and last_index - latest_bear_sweep <= 12
        and latest_bear_break is not None
        and latest_bear_break["Time"] > df.index[latest_bear_sweep]
        and bear_fvg
        and volume_ok
        and current["Close"] < current["Open"]
    )

    if bull_ok:

        signals.append({
            "Stock": ticker.replace(".NS", ""),
            "Signal": "BULLISH",
            "Signal Time": df.index[-1].strftime("%d-%m %H:%M"),
            "Price": round(float(current["Close"]), 2),
            "FVG Low": round(
                next(e["Zone Low"] for e in latest_events
                     if e["Type"] == "Bullish FVG"), 2
            ),
            "FVG High": round(
                next(e["Zone High"] for e in latest_events
                     if e["Type"] == "Bullish FVG"), 2
            ),
            "Volume": "HIGH",
            "Premium/Discount": pd_zone
        })

    if bear_ok:

        signals.append({
            "Stock": ticker.replace(".NS", ""),
            "Signal": "BEARISH",
            "Signal Time": df.index[-1].strftime("%d-%m %H:%M"),
            "Price": round(float(current["Close"]), 2),
            "FVG Low": round(
                next(e["Zone Low"] for e in latest_events
                     if e["Type"] == "Bearish FVG"), 2
            ),
            "FVG High": round(
                next(e["Zone High"] for e in latest_events
                     if e["Type"] == "Bearish FVG"), 2
            ),
            "Volume": "HIGH",
            "Premium/Discount": pd_zone
        })

    summary = {
        "PDH / PDL": get_previous_levels(df),
        "Range High": range_high,
        "Range Low": range_low,
        "Equilibrium": equilibrium,
        "Premium / Discount": pd_zone,
        "OTE": ote,
        "MTF": get_mtf_analysis(df)
    }

    return signals, events, summary


# ==================================================
# SMC CHART
# ==================================================

def show_smc_chart(df, ticker):

    signals, events, summary = analyze_smc(df, ticker)

    chart_df = df.tail(300).copy()

    fig = go.Figure()

    fig.add_trace(go.Candlestick(
        x=chart_df.index,
        open=chart_df["Open"],
        high=chart_df["High"],
        low=chart_df["Low"],
        close=chart_df["Close"],
        name="Price"
    ))

    fig.add_trace(go.Bar(
        x=chart_df.index,
        y=chart_df["Volume"],
        name="Volume",
        marker_color="rgba(150,150,150,0.35)",
        yaxis="y2"
    ))

    start = chart_df.index[0]
    end = chart_df.index[-1] + timedelta(minutes=5)

    # Previous market levels
    levels = summary.get("PDH / PDL", {})

    level_colors = {
        "PDH": "orange",
        "PDL": "orange",
        "PWH": "purple",
        "PWL": "purple",
        "PMH": "cyan",
        "PML": "cyan"
    }

    for name, price in levels.items():

        fig.add_hline(
            y=price,
            line_dash="dash",
            line_color=level_colors.get(name, "white"),
            annotation_text=f"{name}: {price:.2f}"
        )

    # 30-day range
    fig.add_hline(
        y=summary["Equilibrium"],
        line_dash="dot",
        line_color="yellow",
        annotation_text="EQ 50%"
    )

    # Event labels
    visible_events = [
        e for e in events
        if start <= e["Time"] <= end
    ][-60:]

    for event in visible_events:

        name = event["Type"]
        price = event["Price"]
        t = event["Time"]

        if "Bullish" in name or name in [
            "SSL Sweep", "Swing Low", "Equal Low (EQL)"
        ]:
            color = "lime"
        elif "Bearish" in name or name in [
            "BSL Sweep", "Swing High", "Equal High (EQH)"
        ]:
            color = "red"
        else:
            color = "yellow"

        fig.add_annotation(
            x=t,
            y=price,
            text=name,
            showarrow=True,
            arrowhead=2,
            arrowcolor=color,
            font=dict(
                color=color,
                size=9
            )
        )

    # Latest FVG and OB zones
    for event in visible_events:

        if "Zone Low" not in event:
            continue

        name = event["Type"]

        if "FVG" not in name and "Order Block" not in name:
            continue

        color = (
            "rgba(0,255,0,0.15)"
            if "Bullish" in name
            else "rgba(255,0,0,0.15)"
        )

        fig.add_shape(
            type="rect",
            x0=event["Time"],
            x1=end,
            y0=event["Zone Low"],
            y1=event["Zone High"],
            fillcolor=color,
            line=dict(width=1),
            layer="below"
        )

    # OTE reference zone
    ote = summary["OTE"]

    fig.add_hrect(
        y0=ote["Low"],
        y1=ote["High"],
        fillcolor="rgba(255,215,0,0.10)",
        line_width=0,
        annotation_text="OTE Reference"
    )

    fig.update_layout(
        title=f"{ticker} - 30 Day SMC Analysis",
        template="plotly_dark",
        height=750,
        xaxis_rangeslider_visible=False,
        hovermode="x unified",
        yaxis=dict(domain=[0.25, 1]),
        yaxis2=dict(
            domain=[0, 0.18],
            title="Volume"
        ),
        legend=dict(orientation="h")
    )

    st.plotly_chart(
        fig,
        use_container_width=True
    )

    st.caption(
        "Historical SMC labels are rule-based estimates. "
        "They do not verify institutional orders."
    )

    # Event history
    st.subheader("📋 Historical SMC Events")

    if events:

        event_df = pd.DataFrame(events)

        event_df["Time"] = pd.to_datetime(
            event_df["Time"]
        ).dt.strftime("%d-%m-%Y %H:%M")

        st.dataframe(
            event_df.sort_values(
                "Time", ascending=False
            ),
            use_container_width=True,
            hide_index=True
        )

    # Summary
    st.subheader("📊 Multi-Timeframe Trend")

    st.dataframe(
        pd.DataFrame(
            list(summary["MTF"].items()),
            columns=["Timeframe", "Trend"]
        ),
        use_container_width=True,
        hide_index=True
    )

    st.subheader("📍 Market Levels")

    st.json({
        "Previous Levels": summary["PDH / PDL"],
        "30D High": round(summary["Range High"], 2),
        "30D Low": round(summary["Range Low"], 2),
        "Equilibrium": round(summary["Equilibrium"], 2),
        "Market Zone": summary["Premium / Discount"],
        "OTE": summary["OTE"]
    })


# ==================================================
# SCAN ALL NSE STOCKS
# ==================================================

def scan_market():

    now = datetime.now(IST)

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

        batch = stocks[start:start + batch_size]

        try:

            data = download_batch(tuple(batch))

            for ticker in batch:

                try:

                    ticker_df = get_ticker_data(
                        data, ticker
                    )

                    ticker_df = prepare_data(
                        ticker_df, now
                    )

                    if ticker_df.empty:
                        continue

                    found, _, _ = analyze_smc(
                        ticker_df, ticker
                    )

                    all_signals.extend(found)

                except Exception:
                    continue

        except Exception:
            failed_batches += 1

        progress.progress(
            batch_no / total_batches
        )

        status.write(
            f"Scanning {batch_no}/{total_batches} "
            f"| Signals: {len(all_signals)}"
        )

    progress.empty()
    status.empty()

    st.session_state["smc_results"] = all_signals

    st.session_state["smc_scan_time"] = (
        now.strftime("%d-%m-%Y %H:%M:%S")
    )

    if failed_batches:

        st.warning(
            f"{failed_batches} batches failed."
        )


# ==================================================
# WEBSITE
# ==================================================

st.title("📊 ADVANCED NSE SMART MONEY SCANNER")

st.write(
    "NSE All Stocks | 30 Days History | "
    "5M + 15M + 1H + 4H + Daily"
)

st.info(
    "Liquidity + BOS + CHOCH + MSS + FVG + IFVG + "
    "Order Block + Breaker + Mitigation + OTE + Volume"
)

st.warning(
    "Yahoo Finance data may be delayed or incomplete. "
    "Signals are educational estimates, not trading advice."
)

if "smc_results" not in st.session_state:
    st.session_state["smc_results"] = []

if "smc_scan_time" not in st.session_state:
    st.session_state["smc_scan_time"] = "Not scanned"

if st.button(
    "🔄 SCAN ALL NSE STOCKS",
    type="primary",
    use_container_width=True
):

    scan_market()

results = st.session_state["smc_results"]

if results:

    result_df = pd.DataFrame(results)

    result_df = result_df.drop_duplicates(
        subset=["Stock", "Signal"],
        keep="last"
    )

    col1, col2, col3 = st.columns(3)

    col1.metric(
        "Total Signals",
        len(result_df)
    )

    col2.metric(
        "Bullish",
        len(result_df[
            result_df["Signal"] == "BULLISH"
        ])
    )

    col3.metric(
        "Bearish",
        len(result_df[
            result_df["Signal"] == "BEARISH"
        ])
    )

    st.subheader("📋 SMC Scanner Results")

    st.dataframe(
        result_df,
        use_container_width=True,
        hide_index=True
    )

    st.download_button(
        "📥 Download CSV",
        result_df.to_csv(
            index=False
        ).encode("utf-8"),
        "NSE_Advanced_SMC.csv",
        "text/csv"
    )

    st.divider()

    st.subheader("📈 Individual Stock SMC Chart")

    selected_stock = st.selectbox(
        "Select Stock",
        sorted(result_df["Stock"].unique())
    )

    if st.button(
        "SHOW FULL SMC CHART",
        type="primary"
    ):

        ticker = selected_stock + ".NS"

        now = datetime.now(IST)

        with st.spinner(
            f"Loading 30-day SMC data for {ticker}..."
        ):

            try:

                raw = yf.download(
                    ticker,
                    period=YAHOO_PERIOD,
                    interval=INTERVAL,
                    auto_adjust=False,
                    progress=False,
                    timeout=30
                )

                if isinstance(
                    raw.columns, pd.MultiIndex
                ):
                    raw.columns = raw.columns.get_level_values(0)

                chart_data = prepare_data(
                    raw, now
                )

                if not chart_data.empty:

                    show_smc_chart(
                        chart_data,
                        ticker
                    )

                else:
                    st.warning(
                        "Chart data கிடைக்கவில்லை."
                    )

            except Exception as e:

                st.error(
                    f"Chart error: {e}"
                )

    st.caption(
        "Last Scan: " +
        st.session_state["smc_scan_time"]
    )

else:

    st.info(
        "முதலில் SCAN ALL NSE STOCKS அழுத்துங்கள். "
        "Signal கிடைத்தால் Stock Chart பார்க்கலாம்."
    )

st.divider()

st.caption(
    "30-day historical analysis. "
    "Only completed 5-minute candles are used. "
    "Manual scan required. "
    "Yahoo Finance data is not guaranteed live."
)
