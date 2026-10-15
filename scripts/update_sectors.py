#!/usr/bin/env python3
"""Sector + segment strength snapshot for the Premarket Paper dashboard.

Horizons (all ending at "now", the last intraday print):
  weekly   = now / close 5 trading days ago - 1
  daily    = now / yesterday's close - 1
  intraday = now / today's open - 1  (null when the market hasn't opened)

Universe: 11 GICS sector SPDRs + 9 market segments.
Output: data/sectors.json (single latest snapshot; the Sector Strength tab reads it).

Usage: be-venv python update_sectors.py
"""
import json, os, sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import yfinance as yf

CT = ZoneInfo("America/Chicago")
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "sectors.json")

UNIVERSE = [
    # (ticker, name, group)
    ("XLK", "Technology", "sector"),
    ("XLF", "Financials", "sector"),
    ("XLV", "Health Care", "sector"),
    ("XLE", "Energy", "sector"),
    ("XLI", "Industrials", "sector"),
    ("XLB", "Materials", "sector"),
    ("XLP", "Consumer Staples", "sector"),
    ("XLY", "Consumer Discretionary", "sector"),
    ("XLC", "Communication Services", "sector"),
    ("XLRE", "Real Estate", "sector"),
    ("XLU", "Utilities", "sector"),
    ("SPY", "S&P 500", "segment"),
    ("QQQ", "Nasdaq-100", "segment"),
    ("DIA", "Dow 30", "segment"),
    ("IWM", "Russell 2000 (small cap)", "segment"),
    ("SMH", "Semiconductors", "segment"),
    ("XBI", "Biotech", "segment"),
    ("KRE", "Regional Banks", "segment"),
    ("XME", "Metals & Mining", "segment"),
    ("GDX", "Gold Miners", "segment"),
]


def main():
    tickers = [u[0] for u in UNIVERSE]
    # Daily closes (adjusted) for anchors.
    daily = yf.download(tickers, period="15d", interval="1d",
                        auto_adjust=True, group_by="ticker",
                        progress=False, threads=True)
    # Today's intraday bars for open + now.
    intra = yf.download(tickers, period="1d", interval="5m",
                        group_by="ticker", progress=False,
                        threads=True, prepost=False)

    now_ct = datetime.now(CT)
    date_str = now_ct.strftime("%Y-%m-%d")
    rows = []
    now_stamp = None

    for t, name, group in UNIVERSE:
        try:
            d = daily[t]["Close"].dropna()
            # Completed closes = all daily bars except today's partial bar.
            completed = d[d.index.date.astype(str) < date_str]
            if len(completed) < 6:
                print(f"SKIP {t}: only {len(completed)} completed closes", file=sys.stderr)
                continue
            anchor5 = float(completed.iloc[-5])   # close 5 trading days ago
            anchor1 = float(completed.iloc[-1])   # yesterday's close
            today_open, now = None, None
            try:
                bars = intra[t].dropna()
                if len(bars):
                    today_open = float(bars["Open"].iloc[0])
                    now = float(bars["Close"].iloc[-1])
                    ts = bars.index[-1]
                    now_stamp = ts.tz_convert(CT) if getattr(ts, "tzinfo", None) else ts
            except Exception:
                pass
            if now is None:
                now = anchor1  # market not open yet: anchor at yesterday's close
            weekly = (now / anchor5 - 1) * 100
            daily_r = (now / anchor1 - 1) * 100
            intraday = ((now / today_open - 1) * 100) if today_open else None
            rows.append({"ticker": t, "name": name, "group": group,
                         "weekly": round(weekly, 2), "daily": round(daily_r, 2),
                         "intraday": round(intraday, 2) if intraday is not None else None,
                         "price": round(now, 2)})
        except Exception as e:
            print(f"SKIP {t}: {e}", file=sys.stderr)

    rows.sort(key=lambda r: r["weekly"], reverse=True)
    payload = {
        "date": date_str,
        "as_of_ct": (now_stamp.strftime("%Y-%m-%d %H:%M CT")
                     if now_stamp is not None else f"{date_str} (pre-open, anchored to prior close)"),
        "note": "Weekly = now vs close 5 trading days ago. Daily = now vs yesterday's close. "
                "Intraday = now vs today's open. All horizons end at the same 'now'.",
        "rows": rows,
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w") as f:
        json.dump(payload, f, indent=2)
    print(f"wrote {OUT} ({len(rows)} rows) as_of={payload['as_of_ct']}")
    print(f"{'ticker':6} {'name':24} {'weekly':>8} {'daily':>8} {'intraday':>9}")
    for r in rows:
        intra_s = f"{r['intraday']:+.2f}%" if r["intraday"] is not None else "   —   "
        print(f"{r['ticker']:6} {r['name']:24} {r['weekly']:+8.2f}% {r['daily']:+8.2f}% {intra_s:>9}")


if __name__ == "__main__":
    main()
