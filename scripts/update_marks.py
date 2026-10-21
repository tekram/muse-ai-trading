#!/usr/bin/env python3
"""Intraday open-position marks for the Premarket Paper dashboard.

Refreshes the `mark` on OPEN positions in data/trades/*.json from Yahoo
quotes (latest intraday print; falls back to the last completed close),
and stamps each refreshed trade with `mark_as_of`.

This script ONLY marks. It never closes a position, never touches fills,
stops, targets, exits, realized P&L, or closed trades — the 4:15 PM CT
exit check remains authoritative for exits and first-touch accounting.

Edits are surgical: only the `mark` value (and `mark_as_of`) inside each
open trade's JSON object is rewritten in the raw file text, so every other
byte of the trade files is preserved exactly.

Output: the same data/trades/*.json files, updated in place.

Usage: be-venv python update_marks.py [--dry-run]
"""
import json, os, re, sys
from datetime import datetime
from zoneinfo import ZoneInfo

import yfinance as yf

CT = ZoneInfo("America/Chicago")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TRADES = os.path.join(ROOT, "data", "trades")
DRY = "--dry-run" in sys.argv


def object_spans(text):
    """Yield (start, end) spans of every JSON object in `text` that parses
    to a dict carrying both 'ticker' and 'status' (i.e. a trade object)."""
    spans = []
    i, n = 0, len(text)
    while i < n:
        if text[i] == "{":
            depth, j, in_str, esc = 0, i, False, False
            while j < n:
                c = text[j]
                if in_str:
                    if esc:
                        esc = False
                    elif c == "\\":
                        esc = True
                    elif c == '"':
                        in_str = False
                elif c == '"':
                    in_str = True
                elif c == "{":
                    depth += 1
                elif c == "}":
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            if depth == 0:
                try:
                    obj = json.loads(text[i:j + 1])
                except Exception:
                    obj = None
                if isinstance(obj, dict) and "ticker" in obj and "status" in obj:
                    # Trade objects hold only scalars — safe to skip past.
                    spans.append((i, j + 1))
                    i = j + 1
                    continue
                # Not a trade object (e.g. the day file itself): step inside
                # it and keep scanning for nested trade objects.
        i += 1
    return spans


def fetch_prices(tickers):
    """Latest price per ticker: last 5m intraday close, else last daily close.
    Returns {ticker: (price, 'YYYY-MM-DD HH:MM CT')}."""
    out = {}
    intra = yf.download(tickers, period="1d", interval="5m",
                        group_by="ticker", progress=False, threads=True,
                        prepost=False)
    def frame(df, t):
        if len(tickers) == 1:
            return df
        try:
            return df[t]
        except Exception:
            return None
    for t in tickers:
        try:
            bars = frame(intra, t)
            if bars is not None:
                bars = bars.dropna()
                if len(bars):
                    ts = bars.index[-1]
                    ts_ct = ts.tz_convert(CT) if getattr(ts, "tzinfo", None) else ts
                    out[t] = (float(bars["Close"].iloc[-1]),
                              ts_ct.strftime("%Y-%m-%d %H:%M CT"))
        except Exception as e:
            print(f"WARN {t}: intraday {e}", file=sys.stderr)
    missing = [t for t in tickers if t not in out]
    if missing:
        daily = yf.download(missing, period="5d", interval="1d",
                            auto_adjust=False, group_by="ticker",
                            progress=False, threads=True)
        for t in missing:
            try:
                bars = frame(daily, t)
                if bars is not None:
                    bars = bars.dropna()
                    if len(bars):
                        ts = bars.index[-1]
                        out[t] = (float(bars["Close"].iloc[-1]),
                                  f"{ts.strftime('%Y-%m-%d')} close")
            except Exception as e:
                print(f"WARN {t}: daily {e}", file=sys.stderr)
    return out


MARK_RE = re.compile(r'"mark":\s*(null|-?\d+(?:\.\d+)?)')
ASOF_RE = re.compile(r'"mark_as_of":\s*"[^"]*"')


FILLDATE_RE = re.compile(r'"fill_date":\s*"[^"]*"')


def refresh_span(span_text, price, as_of):
    """Rewrite mark (+ mark_as_of) inside one open trade's object text."""
    m = MARK_RE.search(span_text)
    if not m:
        # No mark key yet (fresh positions from the morning logger): insert
        # mark + mark_as_of after fill_date (alphabetical key order).
        f = FILLDATE_RE.search(span_text)
        if not f:
            return span_text
        line_start = span_text.rfind("\n", 0, f.start()) + 1
        indent = span_text[line_start:f.start()]
        ins = (f',\n{indent}"mark": {json.dumps(round(price, 2))}'
               f',\n{indent}"mark_as_of": {json.dumps(as_of)}')
        return span_text[:f.end()] + ins + span_text[f.end():]
    new = span_text[:m.start()] + f'"mark": {json.dumps(round(price, 2))}' + span_text[m.end():]
    a = ASOF_RE.search(new)
    if a:
        new = new[:a.start()] + f'"mark_as_of": {json.dumps(as_of)}' + new[a.end():]
    else:
        # Insert mark_as_of right after the mark entry, matching its indent
        # (keys are alphabetically sorted in these files; mark_as_of sorts
        # immediately after mark).
        m2 = MARK_RE.search(new)
        line_start = new.rfind("\n", 0, m2.start()) + 1
        indent = new[line_start:m2.start()]
        new = (new[:m2.end()] + f',\n{indent}"mark_as_of": {json.dumps(as_of)}'
               + new[m2.end():])
    return new


def assert_only_marks_changed(old, new, path):
    """Guard rail: parsed old vs new may differ ONLY in mark/mark_as_of of
    trades whose status is 'open'. Anything else aborts the write."""
    for side in ("longs", "shorts"):
        o_list, n_list = old.get(side, []), new.get(side, [])
        assert len(o_list) == len(n_list), f"{path}: {side} length changed"
        for o, n in zip(o_list, n_list):
            if o.get("status") != "open":
                assert o == n, f"{path}: closed trade changed: {o.get('ticker')}"
            else:
                o2, n2 = dict(o), dict(n)
                o2.pop("mark", None); n2.pop("mark", None)
                o2.pop("mark_as_of", None); n2.pop("mark_as_of", None)
                assert o2 == n2, f"{path}: non-mark field changed on {o.get('ticker')}"
    for k in old:
        if k not in ("longs", "shorts"):
            assert old[k] == new[k], f"{path}: top-level '{k}' changed"


def main():
    idx = json.load(open(os.path.join(TRADES, "index.json")))
    days = sorted(idx.get("days", []))
    files = {}
    open_tickers = set()
    for d in days:
        p = os.path.join(TRADES, f"{d}.json")
        if not os.path.exists(p):
            continue
        text = open(p).read()
        data = json.loads(text)
        files[d] = (p, text, data)
        for side in ("longs", "shorts"):
            for t in data.get(side, []):
                if t.get("status") == "open":
                    open_tickers.add(t["ticker"])
    if not open_tickers:
        print("no open positions; nothing to mark")
        return
    prices = fetch_prices(sorted(open_tickers))
    print(f"open tickers: {len(open_tickers)}, priced: {len(prices)}")
    for t in sorted(open_tickers):
        if t not in prices:
            print(f"WARN {t}: no Yahoo price; mark left unchanged", file=sys.stderr)

    changed_files = []
    rows = []
    for d in days:
        if d not in files:
            continue
        p, text, data = files[d]
        spans = object_spans(text)
        edits = []
        for (s, e) in spans:
            obj = json.loads(text[s:e])
            if obj.get("status") != "open" or obj["ticker"] not in prices:
                continue
            price, as_of = prices[obj["ticker"]]
            new_span = refresh_span(text[s:e], price, as_of)
            if new_span != text[s:e]:
                edits.append((s, e, new_span))
                rows.append((d, obj["ticker"], obj["side"], obj.get("mark"), round(price, 2)))
        if not edits:
            continue
        new_text = text
        for (s, e, new_span) in sorted(edits, reverse=True):
            new_text = new_text[:s] + new_span + new_text[e:]
        assert_only_marks_changed(data, json.loads(new_text), p)
        if DRY:
            print(f"DRY-RUN would update {p} ({len(edits)} marks)")
        else:
            with open(p, "w") as f:
                f.write(new_text)
            print(f"updated {p} ({len(edits)} marks)")
        changed_files.append(d)
    print(f"{'day':10} {'ticker':6} {'side':5} {'old mark':>10} {'new mark':>10}")
    for d, t, side, old, new in rows:
        print(f"{d:10} {t:6} {side:5} {('%10.2f' % old) if old is not None else '       —  '} {new:10.2f}")
    print(f"files changed: {changed_files if changed_files else 'none'}")


if __name__ == "__main__":
    main()
