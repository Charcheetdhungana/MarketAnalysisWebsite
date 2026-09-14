#!/usr/bin/env python3
"""
QQQ Market Dashboard - daily automation script.

What this file does, in order:
  1. Work out whether the US market actually closed today.
  2. Download 2 years of daily prices for QQQ, NQ futures and 11 sector ETFs.
  3. Work out the day's change, the EMAs (9/20/50/200) and Relative Strength.
  4. Ask Gemini (with Google Search on) what news drove the day.
  5. Ask Gemini to act as a swing trader and give a view for tomorrow.
  6. Ask Gemini for the upcoming economic calendar.
  7. Save everything into Supabase.

Run it by hand with:      python main.py
Ignore the market check:  python main.py --force
Skip all AI calls:        python main.py --no-ai
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import traceback
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

# ======================================================================
# CONFIGURATION - change tickers here if you want more or fewer
# ======================================================================

NY = ZoneInfo("America/New_York")

BENCHMARK = "QQQ"

INSTRUMENTS: dict[str, dict[str, str]] = {
    "QQQ":  {"name": "Invesco QQQ Trust",        "asset_class": "index"},
    "NQ=F": {"name": "Nasdaq 100 Futures",       "asset_class": "futures"},
    "XLK":  {"name": "Technology",               "asset_class": "sector"},
    "XLF":  {"name": "Financials",               "asset_class": "sector"},
    "XLY":  {"name": "Consumer Discretionary",   "asset_class": "sector"},
    "XLP":  {"name": "Consumer Staples",         "asset_class": "sector"},
    "XLV":  {"name": "Health Care",              "asset_class": "sector"},
    "XLE":  {"name": "Energy",                   "asset_class": "sector"},
    "XLI":  {"name": "Industrials",              "asset_class": "sector"},
    "XLB":  {"name": "Materials",                "asset_class": "sector"},
    "XLRE": {"name": "Real Estate",              "asset_class": "sector"},
    "XLU":  {"name": "Utilities",                "asset_class": "sector"},
    "XLC":  {"name": "Communication Services",   "asset_class": "sector"},
}

EMA_SPANS = (9, 20, 50, 200)

# 2 years of daily bars. EMA 200 needs roughly 200 trading days of history
# before it means anything, and 2 years gives about 500. Plenty.
HISTORY_PERIOD = "2y"

# How many past sessions of chart data to save for the front-end charts.
CHART_LOOKBACK = 90

DEFAULT_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash")


def log(msg: str) -> None:
    """Print with a timestamp so the GitHub Actions log is readable."""
    print(f"[{datetime.now(NY):%Y-%m-%d %H:%M:%S %Z}] {msg}", flush=True)


# ======================================================================
# PART 1 - MATHS  (pure functions: no internet, easy to test)
# ======================================================================

def ema(values: pd.Series, span: int) -> float | None:
    """
    Exponential Moving Average, the way charting platforms do it.

    A simple average treats every day equally. An EMA gives today more
    weight than a month ago, so it reacts faster. Think of it like your
    opinion of a restaurant: last night's meal counts more than one you
    had two years ago, but the old ones still nudge the score.

    Method (this matches TradingView / StockCharts):
      - seed the line with a plain average of the first `span` days
      - then roll forward:  new = old + k * (today - old)
        where k = 2 / (span + 1)

    Returns the latest value, or None if there is not enough history.
    """
    s = pd.to_numeric(values, errors="coerce").dropna()
    if len(s) < span:
        return None

    k = 2.0 / (span + 1.0)
    current = float(s.iloc[:span].mean())          # the seed
    for v in s.iloc[span:]:
        current += k * (float(v) - current)
    return current


def ema_series(values: pd.Series, span: int) -> pd.Series:
    """Same maths as ema(), but returns the whole line (for charts)."""
    s = pd.to_numeric(values, errors="coerce").dropna()
    out = pd.Series(index=s.index, dtype="float64")
    if len(s) < span:
        return out

    k = 2.0 / (span + 1.0)
    current = float(s.iloc[:span].mean())
    out.iloc[span - 1] = current
    for i in range(span, len(s)):
        current += k * (float(s.iloc[i]) - current)
        out.iloc[i] = current
    return out


def index_date(series: pd.Series) -> str:
    """
    Get the date of the last bar as a 'YYYY-MM-DD' string.

    yfinance always hands back a proper date index, so the first branch is
    what runs in real life. The fallback is only there so a surprise from
    Yahoo produces today's date instead of crashing the whole job.
    """
    last = series.index[-1]
    # Deliberately a type check, not a try/except around pd.Timestamp().
    # pd.Timestamp(0) happily returns 1970-01-01, so a broken index would
    # quietly write a 1970 date into the database. A wrong date is worse
    # than no date, so only accept something that really is one.
    if isinstance(last, (pd.Timestamp, datetime, date)):
        return pd.Timestamp(last).date().isoformat()

    log(f"  ! index value {last!r} is not a date; using today's NY date instead")
    return datetime.now(NY).date().isoformat()


def round_or_none(x: Any, nd: int = 4) -> float | None:
    """Round a number, but pass None through instead of crashing."""
    if x is None:
        return None
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    if pd.isna(f):
        return None
    return round(f, nd)


def build_rows(closes: dict[str, pd.Series],
               volumes: dict[str, pd.Series] | None = None) -> list[dict]:
    """
    Turn raw closing-price history into one finished record per ticker.

    `closes` looks like {"QQQ": <Series indexed by date>, "XLK": ...}.
    Returns a list of dicts ready to go straight into the database.

    This function never touches the internet, which is why it can be
    tested on its own.
    """
    volumes = volumes or {}
    rows: list[dict] = []

    for symbol, series in closes.items():
        s = pd.to_numeric(series, errors="coerce").dropna()
        if len(s) < 2:
            log(f"  ! {symbol}: not enough data ({len(s)} bars) - skipped")
            continue

        meta = INSTRUMENTS.get(symbol, {})
        close = float(s.iloc[-1])
        prev = float(s.iloc[-2])
        change = close - prev
        change_pct = (change / prev * 100.0) if prev else None

        vol = None
        if symbol in volumes:
            v = pd.to_numeric(volumes[symbol], errors="coerce").dropna()
            if len(v):
                vol = int(v.iloc[-1])

        rows.append({
            "trade_date":  index_date(s),
            "symbol":      symbol,
            "name":        meta.get("name", symbol),
            "asset_class": meta.get("asset_class", "sector"),
            "close":       round_or_none(close),
            "prev_close":  round_or_none(prev),
            "change":      round_or_none(change),
            "change_pct":  round_or_none(change_pct),
            "volume":      vol,
            "ema9":        round_or_none(ema(s, 9)),
            "ema20":       round_or_none(ema(s, 20)),
            "ema50":       round_or_none(ema(s, 50)),
            "ema200":      round_or_none(ema(s, 200)),
        })

    return rows


def add_relative_strength(rows: list[dict]) -> list[dict]:
    """
    Relative Strength = this sector's % move minus QQQ's % move.

    Plain English: if the whole market rose 1% and Technology rose 1.6%,
    Technology has +0.6 relative strength. Money moved INTO tech today.
    If Utilities rose only 0.2%, that is -0.8: money moved out.

    It is like everyone in a class getting a pay rise. The number that
    matters is whether you got more or less than the class average.

    Sectors are then ranked, 1 = strongest.
    """
    bench = next((r for r in rows if r["symbol"] == BENCHMARK), None)
    if not bench or bench.get("change_pct") is None:
        log("  ! No QQQ change - relative strength skipped")
        return rows

    bench_pct = float(bench["change_pct"])

    for r in rows:
        if r.get("change_pct") is None:
            r["rs_vs_qqq"] = None
        else:
            r["rs_vs_qqq"] = round(float(r["change_pct"]) - bench_pct, 4)
        r["rs_rank"] = None

    sectors = [r for r in rows
               if r["asset_class"] == "sector" and r.get("rs_vs_qqq") is not None]
    sectors.sort(key=lambda r: r["rs_vs_qqq"], reverse=True)
    for i, r in enumerate(sectors, start=1):
        r["rs_rank"] = i

    return rows


def top_sectors(rows: list[dict], n: int = 3) -> list[str]:
    """The n strongest sectors of the day, by relative strength."""
    ranked = [r for r in rows if r.get("rs_rank")]
    ranked.sort(key=lambda r: r["rs_rank"])
    return [r["symbol"] for r in ranked[:n]]


def build_chart_series(closes: dict[str, pd.Series],
                       symbols: list[str],
                       lookback: int = CHART_LOOKBACK) -> dict:
    """
    Pack the last ~90 sessions of price + EMA lines into plain JSON so the
    website can draw charts on day one, before the database has built up
    its own history.
    """
    out: dict[str, dict] = {}
    for sym in symbols:
        s = pd.to_numeric(closes.get(sym, pd.Series(dtype=float)),
                          errors="coerce").dropna()
        if len(s) < 2:
            continue
        lines = {f"ema{span}": ema_series(s, span) for span in EMA_SPANS}
        tail = s.tail(lookback)
        out[sym] = {
            "name":  INSTRUMENTS.get(sym, {}).get("name", sym),
            "dates": [d.date().isoformat() for d in tail.index],
            "close": [round(float(v), 4) for v in tail.values],
        }
        for key, line in lines.items():
            seg = line.reindex(tail.index)
            out[sym][key] = [None if pd.isna(v) else round(float(v), 4)
                             for v in seg.values]
    return out


# ======================================================================
# PART 2 - MARKET DATA (this part uses the internet)
# ======================================================================

def fetch_history(symbols: list[str], period: str = HISTORY_PERIOD
                  ) -> tuple[dict[str, pd.Series], dict[str, pd.Series]]:
    """
    Download daily bars from Yahoo Finance via yfinance.

    Returns two dictionaries: closing prices and volumes, keyed by ticker.

    NOTE: yfinance is an unofficial library. It reads Yahoo's public
    website. It is free and normally reliable, but Yahoo can change things
    without warning. If this function returns nothing, that is almost
    always the cause - not a bug in your code.
    """
    import yfinance as yf

    log(f"Downloading {len(symbols)} tickers, period={period} ...")

    last_error = None
    for attempt in range(1, 4):
        try:
            df = yf.download(
                symbols,
                period=period,
                interval="1d",
                auto_adjust=False,
                progress=False,
                group_by="column",
                threads=True,
            )
            if df is not None and not df.empty:
                break
            last_error = "empty dataframe"
        except Exception as exc:                      # noqa: BLE001
            last_error = repr(exc)
        wait = attempt * 5
        log(f"  attempt {attempt} failed ({last_error}); retrying in {wait}s")
        time.sleep(wait)
    else:
        raise RuntimeError(
            f"yfinance returned no data after 3 attempts: {last_error}. "
            "This usually means Yahoo Finance changed or blocked the request."
        )

    closes: dict[str, pd.Series] = {}
    volumes: dict[str, pd.Series] = {}

    if isinstance(df.columns, pd.MultiIndex):
        # Shape is (Price, Ticker) e.g. ('Close', 'QQQ')
        for sym in symbols:
            try:
                closes[sym] = df[("Close", sym)].dropna()
            except KeyError:
                log(f"  ! {sym}: no Close column returned")
                continue
            try:
                volumes[sym] = df[("Volume", sym)].dropna()
            except KeyError:
                pass
    else:
        # Only happens when a single ticker is requested
        sym = symbols[0]
        closes[sym] = df["Close"].dropna()
        if "Volume" in df:
            volumes[sym] = df["Volume"].dropna()

    for sym, s in closes.items():
        log(f"  {sym:5s} {len(s):4d} bars, last {s.index[-1].date()} "
            f"@ {float(s.iloc[-1]):.2f}")

    return closes, volumes


def market_closed_today(closes: dict[str, pd.Series], force: bool) -> bool:
    """
    Decide whether there is a fresh close to record.

    GitHub Actions only understands UTC and does not know about US daylight
    saving, so we schedule the job twice and let this check throw away the
    run that fires at the wrong local time. That is cheaper and far more
    reliable than trying to be clever with cron.
    """
    if force:
        log("--force given: skipping the market-day check")
        return True

    bench = closes.get(BENCHMARK)
    if bench is None or bench.empty:
        return False

    now_ny = datetime.now(NY)
    last_bar = bench.index[-1].date()
    today_ny = now_ny.date()

    # Guard 1: the bar must be today's.
    if last_bar != today_ny:
        log(f"Latest QQQ bar is {last_bar}, today in New York is {today_ny}. "
            "No fresh close yet (weekend, holiday, or the job ran early).")
        return False

    # Guard 2: it must actually be AFTER the 16:00 ET close.
    # This matters. Because we schedule two UTC times to cover US daylight
    # saving, one of them lands at 15:15 ET in winter - while the market is
    # still open. Yahoo happily returns a part-finished bar dated today, and
    # without this check we would save a mid-session price as "the close".
    minutes_now = now_ny.hour * 60 + now_ny.minute
    if minutes_now < 16 * 60 + 5:
        log(f"It is {now_ny:%H:%M} in New York - the market has not closed yet. "
            "Skipping this run; the later scheduled run will handle today.")
        return False

    return True


def already_recorded(sb, trade_date: str) -> bool:
    """
    Has a finished report for this date already been written?

    We schedule two runs a day so one of them always lands after the close
    no matter the time of year. If the first one did the job properly,
    the second should not waste three more Gemini calls redoing it.
    """
    try:
        res = (sb.table("daily_report")
                 .select("trade_date, ai_outlook, updated_at")
                 .eq("trade_date", trade_date)
                 .limit(1)
                 .execute())
        rows = res.data or []
        if not rows:
            return False
        if not (rows[0].get("ai_outlook") or "").strip():
            return False

        # The report must also have been WRITTEN after the close to count.
        #
        # Why: a --force test run during market hours writes a complete-looking
        # report built from a half-finished bar. Without this check the real
        # post-close run would see "already done" and skip, leaving the
        # mid-session snapshot in place all day. Comparing the write time to
        # 16:00 New York means a daytime test run is always overwritten by the
        # genuine close, and a proper close run is still never repeated.
        stamp = rows[0].get("updated_at")
        if not stamp:
            return False
        written = pd.Timestamp(stamp)
        written = (written.tz_localize(NY) if written.tz is None
                   else written.tz_convert(NY))
        if written.date().isoformat() != trade_date:
            return True                     # written on a later day: it's final
        return written.hour * 60 + written.minute >= 16 * 60

    except Exception as exc:                          # noqa: BLE001
        log(f"  (could not check for an existing report: {exc!r})")
        return False


# ======================================================================
# PART 3 - GEMINI  (the AI writer)
# ======================================================================

class Gemini:
    """
    Thin wrapper around the Gemini API.

    Every call is wrapped so that if the AI fails, the price data still
    gets saved. A dashboard with numbers and no commentary is a bad day.
    A dashboard with nothing at all is a broken product.
    """

    def __init__(self, api_key: str, model: str = DEFAULT_MODEL):
        from google import genai
        self.client = genai.Client(api_key=api_key)
        self.model = model
        self.sources: list[dict] = []

    def ask(self, prompt: str, search: bool = True, retries: int = 2) -> str:
        tools = [{"type": "google_search"}] if search else []
        last = None
        for attempt in range(retries + 1):
            try:
                interaction = self.client.interactions.create(
                    model=self.model,
                    input=prompt,
                    tools=tools,
                )
                self._collect_sources(interaction)
                return (interaction.output_text or "").strip()
            except Exception as exc:                  # noqa: BLE001
                last = exc
                wait = (attempt + 1) * 8
                log(f"  Gemini call failed ({exc!r}); retry in {wait}s")
                time.sleep(wait)
        raise RuntimeError(f"Gemini failed after {retries + 1} attempts: {last!r}")

    def _collect_sources(self, interaction) -> None:
        """Pull the real web links Gemini used, so the page can cite them."""
        try:
            for step in (interaction.steps or []):
                if getattr(step, "type", None) != "model_output":
                    continue
                for block in (getattr(step, "content", None) or []):
                    for ann in (getattr(block, "annotations", None) or []):
                        if getattr(ann, "type", None) == "url_citation":
                            item = {"title": getattr(ann, "title", "") or "source",
                                    "url": getattr(ann, "url", "")}
                            if item["url"] and item not in self.sources:
                                self.sources.append(item)
        except Exception:                             # noqa: BLE001
            pass


def extract_json(text: str) -> Any:
    """
    Pull a JSON object or list out of the model's reply.

    Language models like to wrap JSON in ```json fences or add a sentence
    before it. This strips all that and finds the actual JSON.
    """
    if not text:
        return None

    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    candidate = fenced.group(1) if fenced else text

    try:
        return json.loads(candidate.strip())
    except json.JSONDecodeError:
        pass

    # Find both an object and a list, then take whichever STARTS FIRST.
    #
    # This ordering matters. Given 'Here you go: [{"x": 1}] done', looking
    # for '{' first would return the inner object {"x": 1} and silently
    # throw away the list around it. The outermost bracket is the real
    # answer, and the outermost one is the one that appears earliest.
    best: tuple[int, Any] | None = None
    for opener, closer in (("{", "}"), ("[", "]")):
        start = candidate.find(opener)
        end = candidate.rfind(closer)
        if start == -1 or end <= start:
            continue
        try:
            parsed = json.loads(candidate[start:end + 1])
        except json.JSONDecodeError:
            continue
        if best is None or start < best[0]:
            best = (start, parsed)

    return best[1] if best else None


def build_market_table(rows: list[dict]) -> str:
    """A compact plain-text table to feed the model. Cheaper than JSON."""
    lines = ["SYMBOL | NAME | CLOSE | CHG% | RS_vs_QQQ | EMA9 | EMA20 | EMA50 | EMA200"]
    order = {"index": 0, "futures": 1, "sector": 2}
    for r in sorted(rows, key=lambda r: (order.get(r["asset_class"], 3),
                                         r.get("rs_rank") or 0)):
        def f(key, nd=2):
            v = r.get(key)
            return "n/a" if v is None else f"{float(v):.{nd}f}"
        lines.append(
            f"{r['symbol']} | {r['name']} | {f('close')} | {f('change_pct')} | "
            f"{f('rs_vs_qqq')} | {f('ema9')} | {f('ema20')} | {f('ema50')} | {f('ema200')}"
        )
    return "\n".join(lines)


def get_news(g: Gemini, trade_date: str, rows: list[dict]) -> dict:
    """Gemini call 1: what actually moved the market today."""
    qqq = next((r for r in rows if r["symbol"] == BENCHMARK), {})
    prompt = f"""You are a financial news editor. Today is {trade_date} (US market date).
The Nasdaq 100 ETF (QQQ) closed at {qqq.get('close')}, a move of {qqq.get('change_pct')}%.

Search the web for today's US stock market news and identify the real
macroeconomic and market drivers of this session.

Return ONLY valid JSON in exactly this shape, no other text:
{{
  "summary": "2 to 4 sentences in plain English explaining what drove US equities today.",
  "headlines": [
    {{"title": "...", "summary": "one sentence", "source": "publication name", "url": "https://..."}}
  ]
}}

Rules:
- Give between 4 and 6 headlines.
- Only real, verifiable stories from today's session. No invented news.
- Prefer macro drivers (Fed, inflation, jobs, rates, oil, big tech earnings)
  over single small-cap stock stories.
- Use simple, clear language. No jargon without explanation."""

    raw = g.ask(prompt, search=True)
    data = extract_json(raw) or {}
    if not isinstance(data, dict):
        data = {}
    return {
        "summary": str(data.get("summary") or "").strip(),
        "headlines": data.get("headlines") if isinstance(data.get("headlines"), list) else [],
    }


def get_outlook(g: Gemini, trade_date: str, rows: list[dict],
                news: dict, leaders: list[str]) -> dict:
    """Gemini call 2: the swing trader's read and tomorrow's outlook."""
    table = build_market_table(rows)
    headline_text = "\n".join(
        f"- {h.get('title','')}: {h.get('summary','')}"
        for h in news.get("headlines", [])[:6]
    ) or "(no headlines available)"

    prompt = f"""You are a professional swing trader with 15 years of experience
trading the Nasdaq 100 and US sector ETFs. Today is {trade_date}.

Here is today's closing data. RS_vs_QQQ is the sector's percentage move
minus QQQ's percentage move, so a positive number means the sector beat
the index today.

{table}

Today's strongest sectors by relative strength: {', '.join(leaders) or 'n/a'}

Today's news drivers:
{headline_text}

Search the web for any scheduled US economic releases or major earnings in
the NEXT trading session, then give your professional read.

Return ONLY valid JSON in exactly this shape, no other text:
{{
  "technical": "3 to 5 sentences. Where is QQQ relative to its EMA 9, 20, 50 and 200? Is the short EMA above or below the long EMA, and what does that say about trend? Comment on the sector rotation you see in the RS numbers.",
  "outlook": "3 to 5 sentences. Your view for the next session, and what would confirm or invalidate it.",
  "bias": "bullish or neutral or bearish",
  "levels": {{"support": [numbers], "resistance": [numbers]}},
  "risks": ["short risk one", "short risk two", "short risk three"],
  "next_session_events": ["event name and time if known"]
}}

Rules:
- Write in simple, clear English. Explain any term you use.
- Be specific about price levels, taken from the EMA values above.
- Do not give financial advice or tell anyone to buy or sell. Describe
  the setup and the scenarios only.
- If the data does not support a strong view, say "neutral"."""

    raw = g.ask(prompt, search=True)
    data = extract_json(raw) or {}
    if not isinstance(data, dict):
        data = {}

    bias = str(data.get("bias") or "neutral").lower().strip()
    if bias not in ("bullish", "neutral", "bearish"):
        bias = "neutral"

    return {
        "technical": str(data.get("technical") or "").strip(),
        "outlook": str(data.get("outlook") or "").strip(),
        "bias": bias,
        "levels": data.get("levels") if isinstance(data.get("levels"), dict) else {},
        "risks": data.get("risks") if isinstance(data.get("risks"), list) else [],
        "events": data.get("next_session_events") if isinstance(
            data.get("next_session_events"), list) else [],
    }


def get_calendar(g: Gemini, trade_date: str) -> list[dict]:
    """Gemini call 3: the rolling economic calendar for the next 10 days."""
    start = date.fromisoformat(trade_date)
    end = start + timedelta(days=10)

    prompt = f"""Search the web for the official US economic calendar between
{start.isoformat()} and {end.isoformat()}.

Return ONLY valid JSON, a list, in exactly this shape, no other text:
[
  {{"event_date": "YYYY-MM-DD", "event_time": "08:30 ET", "event": "CPI (August)",
    "importance": "high", "forecast": "0.2%", "previous": "0.3%"}}
]

Rules:
- Only genuine, scheduled US releases and events: CPI, PPI, PCE, FOMC
  meetings and minutes, Fed speakers, non-farm payrolls, jobless claims,
  retail sales, ISM, GDP, consumer confidence, Treasury auctions.
- importance must be exactly "high", "medium" or "low".
- Use "" for forecast or previous if you do not know them. Do not guess.
- Between 5 and 15 events. Most important first."""

    raw = g.ask(prompt, search=True)
    data = extract_json(raw)
    if not isinstance(data, list):
        return []

    cleaned: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for item in data:
        if not isinstance(item, dict):
            continue
        try:
            ev_date = date.fromisoformat(str(item.get("event_date", "")).strip())
        except ValueError:
            continue
        event = str(item.get("event", "")).strip()
        if not event:
            continue
        key = (ev_date.isoformat(), event)
        if key in seen:
            continue
        seen.add(key)

        importance = str(item.get("importance", "medium")).lower().strip()
        if importance not in ("high", "medium", "low"):
            importance = "medium"

        cleaned.append({
            "event_date": ev_date.isoformat(),
            "event_time": str(item.get("event_time", "") or "").strip()[:40],
            "event": event[:200],
            "importance": importance,
            "forecast": str(item.get("forecast", "") or "").strip()[:60],
            "previous": str(item.get("previous", "") or "").strip()[:60],
            "refreshed_on": trade_date,
        })
    return cleaned


# ======================================================================
# PART 4 - DATABASE
# ======================================================================

def get_supabase():
    from supabase import create_client

    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_KEY")
    if not url or not key:
        raise SystemExit(
            "Missing SUPABASE_URL or SUPABASE_SERVICE_KEY.\n"
            "Locally: copy .env.example to .env and fill it in.\n"
            "On GitHub: Settings -> Secrets and variables -> Actions."
        )
    return create_client(url, key)


def save_everything(sb, rows: list[dict], report: dict, events: list[dict]) -> None:
    """
    Write to the database using UPSERT.

    Upsert = insert if new, update if it already exists. This is what makes
    the job safe to run twice on the same day, which matters because we
    schedule two runs to cover US daylight saving.
    """
    log(f"Saving {len(rows)} price rows ...")
    sb.table("daily_prices").upsert(
        rows, on_conflict="trade_date,symbol").execute()

    log("Saving daily report ...")
    sb.table("daily_report").upsert(
        report, on_conflict="trade_date").execute()

    if events:
        log(f"Saving {len(events)} calendar events ...")
        sb.table("econ_calendar").upsert(
            events, on_conflict="event_date,event").execute()

    # Housekeeping: drop calendar entries that are more than 30 days old,
    # so the free 500 MB never fills up with stale rows.
    cutoff = (date.today() - timedelta(days=30)).isoformat()
    sb.table("econ_calendar").delete().lt("event_date", cutoff).execute()

    log("Database write complete.")


# ======================================================================
# MAIN
# ======================================================================

def main() -> int:
    parser = argparse.ArgumentParser(description="Daily market dashboard job")
    parser.add_argument("--force", action="store_true",
                        help="Run even if today is not a US trading day")
    parser.add_argument("--no-ai", action="store_true",
                        help="Skip all Gemini calls (prices only)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Do everything except write to the database")
    args = parser.parse_args()

    log("=" * 62)
    log("QQQ Market Dashboard - daily run starting")
    log("=" * 62)

    # ---- Step 1: prices -------------------------------------------------
    symbols = list(INSTRUMENTS.keys())
    closes, volumes = fetch_history(symbols)

    if not market_closed_today(closes, args.force):
        log("Nothing to do. Exiting cleanly.")
        return 0

    rows = build_rows(closes, volumes)
    if not rows:
        log("ERROR: no usable rows were built. Aborting.")
        return 1

    rows = add_relative_strength(rows)
    leaders = top_sectors(rows, 3)
    trade_date = rows[0]["trade_date"]
    qqq = next((r for r in rows if r["symbol"] == BENCHMARK), {})

    log(f"Trade date: {trade_date}")
    log(f"QQQ close {qqq.get('close')} ({qqq.get('change_pct')}%)")
    log(f"Strongest sectors: {', '.join(leaders)}")

    chart_symbols = [BENCHMARK] + leaders
    chart = build_chart_series(closes, chart_symbols)

    # ---- Step 2: connect to the database early, so we can check whether
    #      today's report was already written by the earlier scheduled run.
    sb = None
    if not args.dry_run:
        sb = get_supabase()
        if already_recorded(sb, trade_date) and not args.force:
            log(f"A complete report for {trade_date} already exists. "
                "Nothing to do. (Use --force to rebuild it.)")
            return 0

    # ---- Step 3: AI -----------------------------------------------------
    news = {"summary": "", "headlines": []}
    view = {"technical": "", "outlook": "", "bias": "neutral",
            "levels": {}, "risks": [], "events": []}
    events: list[dict] = []
    sources: list[dict] = []
    model_used = None

    api_key = os.environ.get("GEMINI_API_KEY")
    if args.no_ai:
        log("--no-ai given: skipping Gemini.")
    elif not api_key:
        log("WARNING: GEMINI_API_KEY not set. Saving prices only.")
    else:
        g = Gemini(api_key)
        model_used = g.model
        log(f"Using Gemini model: {g.model}")

        for label, fn in (
            ("news",     lambda: get_news(g, trade_date, rows)),
            ("outlook",  lambda: get_outlook(g, trade_date, rows, news, leaders)),
            ("calendar", lambda: get_calendar(g, trade_date)),
        ):
            try:
                log(f"Gemini: {label} ...")
                result = fn()
                if label == "news":
                    news = result
                    log(f"  got {len(news['headlines'])} headlines")
                elif label == "outlook":
                    view = result
                    log(f"  bias: {view['bias']}")
                else:
                    events = result
                    log(f"  got {len(events)} calendar events")
            except Exception as exc:                  # noqa: BLE001
                log(f"  ! {label} failed, continuing without it: {exc!r}")

        sources = g.sources

    # ---- Step 4: assemble the report -----------------------------------
    report = {
        "trade_date":     trade_date,
        "qqq_close":      qqq.get("close"),
        "qqq_change_pct": qqq.get("change_pct"),
        "headlines":      news["headlines"],
        "news_summary":   news["summary"],
        "ai_technical":   view["technical"],
        "ai_outlook":     view["outlook"],
        "ai_bias":        view["bias"],
        "ai_levels":      view["levels"],
        "ai_risks":       view["risks"],
        "top_sectors":    leaders,
        "chart_series":   chart,
        "sources":        sources,
        "model":          model_used,
        "updated_at":     datetime.now(NY).isoformat(),
    }

    # ---- Step 5: save ---------------------------------------------------
    if args.dry_run or sb is None:
        log("--dry-run: not writing to the database. Preview:")
        print(json.dumps({"rows": rows[:3], "report_keys": list(report)},
                         indent=2, default=str))
        return 0

    save_everything(sb, rows, report, events)

    log("=" * 62)
    log("Done.")
    log("=" * 62)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception:                                 # noqa: BLE001
        log("FATAL ERROR")
        traceback.print_exc()
        sys.exit(1)
