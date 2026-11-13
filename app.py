import os
import requests
import pandas as pd
import yfinance as yf
import time
from io import StringIO
from datetime import datetime, timedelta, timezone

# Telegram settings
BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

# Bullish Marubozu definition
BODY_PERCENT = 90
WICK_PERCENT = 5

def send_telegram(message):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    try:
        requests.post(
            url,
            data={"chat_id": CHAT_ID, "text": message},
            timeout=15
        )
    except Exception as e:
        print("Telegram error:", e)

def get_nse_stocks():
    url = "https://archives.nseindia.com/content/equities/EQUITY_L.csv"
    headers = {"User-Agent": "Mozilla/5.0"}
    response = requests.get(url, headers=headers, timeout=30)
    response.raise_for_status()
    df = pd.read_csv(StringIO(response.text))
    return [
        str(symbol).strip() + ".NS"
        for symbol in df["SYMBOL"].dropna()
    ]

def is_bullish_marubozu(candle):
    o = float(candle["Open"])
    h = float(candle["High"])
    l = float(candle["Low"])
    c = float(candle["Close"])

    candle_range = h - l
    if candle_range <= 0 or c <= o:
        return False

    body = c - o
    upper_wick = h - c
    lower_wick = o - l

    body_ratio = body / candle_range * 100
    upper_ratio = upper_wick / candle_range * 100
    lower_ratio = lower_wick / candle_range * 100

    return (
        body_ratio >= BODY_PERCENT
        and upper_ratio <= WICK_PERCENT
        and lower_ratio <= WICK_PERCENT
    )

def scan_stocks():
    print("Starting NSE scanner...")
    stocks = get_nse_stocks()
    print("Total stocks:", len(stocks))

    alerts = []

    # Scan in batches to reduce data request failures
    for start in range(0, len(stocks), 50):
        batch = stocks[start:start + 50]

        try:
            data = yf.download(
                batch,
                period="1d",
                interval="5m",
                group_by="ticker",
                progress=False,
                threads=True,
                timeout=30
            )

            for symbol in batch:
                try:
                    if len(batch) == 1:
                        df = data
                    else:
                        df = data[symbol]

                    df = df.dropna(subset=["Open", "High", "Low", "Close"])

                    if len(df) < 2:
                        continue

                    # Last completed candle
                    candle = df.iloc[-2]

                    if is_bullish_marubozu(candle):
                        price = float(candle["Close"])
                        candle_time = str(df.index[-2])

                        message = (
                            "🚀 BULLISH MARUBOZU ALERT\n\n"
                            f"Stock: {symbol.replace('.NS', '')}\n"
                            f"Close: ₹{price:.2f}\n"
                            f"Time: {candle_time}\n"
                            "Timeframe: 5 Minutes\n"
                        )

                        alerts.append(message)

                except Exception:
                    continue

        except Exception as e:
            print("Batch error:", e)

        time.sleep(1)

    for alert in alerts:
        send_telegram(alert)
        print(alert)

    print("Scan completed:", datetime.now())

if __name__ == "__main__":
    scan_stocks()
