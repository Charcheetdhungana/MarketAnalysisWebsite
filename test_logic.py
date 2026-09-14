#!/usr/bin/env python3
"""
Offline checks for the maths in main.py.

These run without the internet. They prove the EMA, the relative strength
and the row builder are correct before the script ever touches Yahoo.

Run with:  python test_logic.py
"""
import sys
import numpy as np
import pandas as pd

import main


def approx(a, b, tol=1e-9):
    return abs(a - b) < tol


results = []
def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))


# ---------------------------------------------------------------------
print("\n1. EMA - hand-worked example")
# ---------------------------------------------------------------------
# span=3  ->  k = 2/(3+1) = 0.5
# prices: 10, 11, 12, 13, 14
# seed = mean(10,11,12) = 11
# day 4: 11   + 0.5*(13-11)   = 12
# day 5: 12   + 0.5*(14-12)   = 13
prices = pd.Series([10, 11, 12, 13, 14], dtype=float)
got = main.ema(prices, 3)
check("ema(span=3) == 13.0 by hand", approx(got, 13.0), f"got {got}")

series = main.ema_series(prices, 3)
check("ema_series matches: [nan, nan, 11, 12, 13]",
      pd.isna(series.iloc[0]) and pd.isna(series.iloc[1])
      and approx(series.iloc[2], 11.0)
      and approx(series.iloc[3], 12.0)
      and approx(series.iloc[4], 13.0),
      f"got {list(series.round(6))}")

check("ema_series last value == ema()",
      approx(float(series.iloc[-1]), got))


# ---------------------------------------------------------------------
print("\n2. EMA - independent implementation on a realistic series")
# ---------------------------------------------------------------------
# Build 600 sessions of a realistic-looking price path.
rng = np.random.default_rng(20260914)
path = 380 * np.exp(np.cumsum(rng.normal(0.0004, 0.011, 600)))
s = pd.Series(path, index=pd.bdate_range("2024-01-01", periods=600))


def reference_ema(values, span):
    """
    Completely separate implementation using pandas' own ewm engine.
    We seed it the same way (SMA of the first `span` bars) by replacing
    the head of the series with that average, then let pandas do the
    recursion. Different code path, same definition.
    """
    v = values.astype(float).copy()
    seed = v.iloc[:span].mean()
    v.iloc[:span] = seed
    return v.ewm(alpha=2.0 / (span + 1.0), adjust=False).mean().iloc[-1]


for span in (9, 20, 50, 200):
    mine = main.ema(s, span)
    ref = reference_ema(s, span)
    check(f"EMA {span} matches independent pandas ewm",
          approx(mine, ref, 1e-8), f"mine {mine:.10f} / ref {ref:.10f}")


# ---------------------------------------------------------------------
print("\n3. EMA - properties that must hold")
# ---------------------------------------------------------------------
flat = pd.Series([100.0] * 300)
check("EMA of a flat series is that flat value",
      all(approx(main.ema(flat, sp), 100.0) for sp in (9, 20, 50, 200)))

check("EMA returns None when history is too short",
      main.ema(pd.Series([1.0, 2.0, 3.0]), 200) is None)

rising = pd.Series(np.arange(1, 401, dtype=float))
e9, e200 = main.ema(rising, 9), main.ema(rising, 200)
check("On a rising series, EMA 9 sits above EMA 200", e9 > e200,
      f"{e9:.2f} > {e200:.2f}")

falling = pd.Series(np.arange(400, 0, -1, dtype=float))
check("On a falling series, EMA 9 sits below EMA 200",
      main.ema(falling, 9) < main.ema(falling, 200))


# ---------------------------------------------------------------------
print("\n4. build_rows - change and percentage change")
# ---------------------------------------------------------------------
idx = pd.bdate_range("2024-01-01", periods=600)
closes = {
    "QQQ":  pd.Series(path, index=idx),
    "XLK":  pd.Series(path * 1.02, index=idx),
    "XLU":  pd.Series(path * 0.98, index=idx),
    "NQ=F": pd.Series(path * 41.0, index=idx),
}
# Force known final moves so the arithmetic is checkable by hand.
closes["QQQ"].iloc[-2] = 400.0; closes["QQQ"].iloc[-1] = 404.0   # +1.00%
closes["XLK"].iloc[-2] = 200.0; closes["XLK"].iloc[-1] = 203.0   # +1.50%
closes["XLU"].iloc[-2] = 80.0;  closes["XLU"].iloc[-1] = 79.6    # -0.50%

rows = main.build_rows(closes)
by = {r["symbol"]: r for r in rows}

check("one row per ticker", len(rows) == 4, f"got {len(rows)}")
check("QQQ change == 4.0", approx(by["QQQ"]["change"], 4.0))
check("QQQ change_pct == 1.00", approx(by["QQQ"]["change_pct"], 1.0))
check("XLK change_pct == 1.50", approx(by["XLK"]["change_pct"], 1.5))
check("XLU change_pct == -0.50", approx(by["XLU"]["change_pct"], -0.5))
check("trade_date is the last bar's date",
      by["QQQ"]["trade_date"] == idx[-1].date().isoformat(),
      by["QQQ"]["trade_date"])
check("asset_class comes from the config",
      by["QQQ"]["asset_class"] == "index"
      and by["NQ=F"]["asset_class"] == "futures"
      and by["XLK"]["asset_class"] == "sector")
check("all four EMAs are populated",
      all(by["QQQ"][k] is not None for k in ("ema9", "ema20", "ema50", "ema200")))

short = {"QQQ": pd.Series([1.0], index=idx[:1])}
check("a ticker with one bar is skipped, not crashed",
      main.build_rows(short) == [])


# ---------------------------------------------------------------------
print("\n5. Relative strength")
# ---------------------------------------------------------------------
rows = main.add_relative_strength(rows)
by = {r["symbol"]: r for r in rows}

# XLK +1.50 minus QQQ +1.00 = +0.50
check("XLK relative strength == +0.50", approx(by["XLK"]["rs_vs_qqq"], 0.5))
# XLU -0.50 minus QQQ +1.00 = -1.50
check("XLU relative strength == -1.50", approx(by["XLU"]["rs_vs_qqq"], -1.5))
check("QQQ relative strength against itself is 0",
      approx(by["QQQ"]["rs_vs_qqq"], 0.0))
check("rank 1 goes to the strongest sector", by["XLK"]["rs_rank"] == 1)
check("the weaker sector ranks below it", by["XLU"]["rs_rank"] == 2)
check("non-sectors are not ranked",
      by["QQQ"]["rs_rank"] is None and by["NQ=F"]["rs_rank"] is None)
check("top_sectors returns strongest first",
      main.top_sectors(rows, 3) == ["XLK", "XLU"])

# Relative strength must still work when the whole market falls.
two = pd.bdate_range("2026-09-10", periods=2)
down = {
    "QQQ": pd.Series([100.0, 98.0], index=two),   # -2.00%
    "XLE": pd.Series([50.0, 49.5], index=two),    # -1.00%  beat the market
    "XLK": pd.Series([200.0, 194.0], index=two),  # -3.00%  lagged
}
d = main.add_relative_strength(main.build_rows(down))
dby = {r["symbol"]: r for r in d}
check("in a down market, the smaller loss ranks first",
      dby["XLE"]["rs_rank"] == 1 and approx(dby["XLE"]["rs_vs_qqq"], 1.0),
      f"XLE rs {dby['XLE']['rs_vs_qqq']}")
check("in a down market, the bigger loser is negative",
      approx(dby["XLK"]["rs_vs_qqq"], -1.0))

no_bench = main.add_relative_strength(main.build_rows({"XLK": closes["XLK"]}))
check("missing benchmark does not crash", isinstance(no_bench, list))


# ---------------------------------------------------------------------
print("\n6. Chart series for the front end")
# ---------------------------------------------------------------------
chart = main.build_chart_series(closes, ["QQQ", "XLK"], lookback=90)
q = chart["QQQ"]
check("chart has QQQ and XLK", set(chart) == {"QQQ", "XLK"})
check("90 dates returned", len(q["dates"]) == 90, str(len(q["dates"])))
check("every line has the same length as dates",
      all(len(q[k]) == 90 for k in ("close", "ema9", "ema20", "ema50", "ema200")))
check("the last EMA200 in the chart equals the stored EMA200",
      approx(q["ema200"][-1], by["QQQ"]["ema200"], 1e-4),
      f"{q['ema200'][-1]} vs {by['QQQ']['ema200']}")
check("the last close in the chart equals the stored close",
      approx(q["close"][-1], by["QQQ"]["close"], 1e-4))
check("chart values are JSON-safe (no NaN)",
      all(v is None or isinstance(v, float) for v in q["ema200"]))


# ---------------------------------------------------------------------
print("\n7. Parsing Gemini's replies")
# ---------------------------------------------------------------------
check("plain JSON object",
      main.extract_json('{"a": 1}') == {"a": 1})
check("JSON inside a ```json fence",
      main.extract_json('```json\n{"a": 2}\n```') == {"a": 2})
check("JSON inside a bare ``` fence",
      main.extract_json('```\n[1, 2, 3]\n```') == [1, 2, 3])
check("JSON with chatter before and after",
      main.extract_json('Sure! Here you go:\n{"a": 3}\nHope that helps.') == {"a": 3})
check("a JSON list with chatter around it keeps the LIST, not the inner object",
      main.extract_json('Here:\n[{"x": 1}]\ndone') == [{"x": 1}],
      repr(main.extract_json('Here:\n[{"x": 1}]\ndone')))
check("a calendar-shaped list of objects survives",
      main.extract_json('Sure.\n[{"event":"CPI"},{"event":"FOMC"}]\nThanks!')
      == [{"event": "CPI"}, {"event": "FOMC"}])
check("an object containing a list is still an object",
      main.extract_json('{"headlines": [{"t": 1}]}') == {"headlines": [{"t": 1}]})
check("unparseable text returns None",
      main.extract_json("I could not find anything.") is None)
check("empty string returns None", main.extract_json("") is None)


# ---------------------------------------------------------------------
print("\n8. The market table sent to Gemini")
# ---------------------------------------------------------------------
tbl = main.build_market_table(rows)
check("header row present", tbl.splitlines()[0].startswith("SYMBOL |"))
check("one line per ticker plus header",
      len(tbl.splitlines()) == len(rows) + 1)
check("QQQ appears before the sectors",
      tbl.splitlines()[1].startswith("QQQ"))
check("missing values print as n/a, not None", "None" not in tbl)


# ---------------------------------------------------------------------
print("\n9. Rounding helper")
# ---------------------------------------------------------------------
check("round_or_none(None) is None", main.round_or_none(None) is None)
check("round_or_none(NaN) is None", main.round_or_none(float("nan")) is None)
check("round_or_none rounds to 4 dp",
      approx(main.round_or_none(1.23456789), 1.2346))
check("round_or_none handles text", main.round_or_none("abc") is None)


# ---------------------------------------------------------------------
print("\n10. Bad input does not bring the job down")
# ---------------------------------------------------------------------
# A non-date index should degrade to today, not raise.
odd = {"QQQ": pd.Series([100.0, 101.0])}
try:
    r = main.build_rows(odd)
    import datetime as _dt
    today = _dt.date.today().isoformat()
    check("integer index falls back to TODAY, never to 1970",
          len(r) == 1 and r[0]["trade_date"][:2] == "20"
          and abs((_dt.date.fromisoformat(r[0]["trade_date"])
                   - _dt.date.today()).days) <= 1,
          f"{r[0]['trade_date']} (today is {today})")
except Exception as exc:                                  # noqa: BLE001
    check("integer index falls back instead of crashing", False, repr(exc))

# Gaps in the data should be dropped, not poison the EMA.
gappy = pd.Series([10.0, None, 12.0, 13.0, 14.0], dtype="float64")
check("None values inside a series are ignored",
      main.ema(gappy, 3) is not None)

# A zero previous close must not divide by zero.
zero = {"QQQ": pd.Series([0.0, 5.0], index=two)}
zr = main.build_rows(zero)
check("a zero previous close gives change_pct None, not a crash",
      zr[0]["change_pct"] is None, str(zr[0]["change_pct"]))


# ---------------------------------------------------------------------
failed = [n for n, ok, _ in results if not ok]
print("\n" + "=" * 60)
print(f"{len(results) - len(failed)} passed, {len(failed)} failed")
print("=" * 60)
if failed:
    for n in failed:
        print("  FAILED:", n)
    sys.exit(1)
print("All checks passed.")
