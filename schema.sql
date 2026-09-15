-- =====================================================================
--  QQQ Market Dashboard - Supabase database schema
--  Run this ONCE in Supabase -> SQL Editor -> New query -> Run.
--  It is safe to run again: everything uses "if not exists" / "or replace".
-- =====================================================================


-- ---------------------------------------------------------------------
-- TABLE 1: daily_prices
-- One row per ticker per trading day.
-- Example: on 2026-09-14 there will be 12 rows (QQQ, 11 sectors).
-- ---------------------------------------------------------------------
create table if not exists public.daily_prices (
    id            bigint generated always as identity primary key,
    trade_date    date        not null,
    symbol        text        not null,
    name          text,
    asset_class   text,                      -- 'index' | 'futures' | 'sector'

    close         numeric(14,4),
    prev_close    numeric(14,4),
    change        numeric(14,4),             -- close - prev_close
    change_pct    numeric(10,4),             -- percent change for the day
    rs_vs_qqq     numeric(10,4),             -- change_pct minus QQQ change_pct
    rs_rank       int,                       -- 1 = strongest sector that day
    volume        bigint,

    ema8          numeric(14,4),
    ema21         numeric(14,4),
    ema50         numeric(14,4),
    ema100        numeric(14,4),
    ema200        numeric(14,4),

    created_at    timestamptz default now(),

    -- This is the important bit. It makes a re-run UPDATE the row
    -- instead of adding a duplicate. Like saving over the same file.
    constraint daily_prices_date_symbol_key unique (trade_date, symbol)
);

create index if not exists daily_prices_date_idx
    on public.daily_prices (trade_date desc);
create index if not exists daily_prices_symbol_date_idx
    on public.daily_prices (symbol, trade_date desc);


-- ---------------------------------------------------------------------
-- TABLE 2: daily_report
-- One row per trading day. Holds the news and everything the AI wrote.
-- ---------------------------------------------------------------------
create table if not exists public.daily_report (
    trade_date       date primary key,

    qqq_close        numeric(14,4),
    qqq_change_pct   numeric(10,4),

    headlines        jsonb,   -- [{"title","summary","url","source"}]
    news_summary     text,    -- short paragraph: what drove the day
    ai_technical     text,    -- swing trader read of the chart
    ai_outlook       text,    -- view for the next session
    ai_bias          text,    -- 'bullish' | 'neutral' | 'bearish'
    ai_levels        jsonb,   -- {"support":[...], "resistance":[...]}
    ai_risks         jsonb,   -- ["risk one", "risk two"]

    top_sectors      jsonb,   -- ["XLK","XLF","XLY"] strongest 3 that day
    chart_series     jsonb,   -- last ~90 sessions of close + EMAs for charts
    sources          jsonb,   -- [{"title","url"}] links Gemini actually used

    model            text,    -- which Gemini model wrote it
    created_at       timestamptz default now(),
    updated_at       timestamptz default now()
);


-- ---------------------------------------------------------------------
-- TABLE 3: econ_calendar
-- Upcoming market-moving economic events. Refreshed every run.
-- ---------------------------------------------------------------------
create table if not exists public.econ_calendar (
    id            bigint generated always as identity primary key,
    event_date    date not null,
    event_time    text,                      -- e.g. '08:30 ET'
    event         text not null,             -- e.g. 'CPI (Aug)'
    importance    text,                      -- 'high' | 'medium' | 'low'
    forecast      text,
    previous      text,
    refreshed_on  date not null,
    created_at    timestamptz default now(),

    constraint econ_calendar_date_event_key unique (event_date, event)
);

create index if not exists econ_calendar_date_idx
    on public.econ_calendar (event_date);


-- ---------------------------------------------------------------------
-- VIEW: weekly_performance
-- A view is a saved question, not stored data. Every time the website
-- asks for it, Postgres works out the answer fresh from daily_prices.
--
-- week_return_pct compounds the daily returns properly:
--   +1% then +1% is +2.01%, not +2%.
-- The greatest(...) guard stops a maths error if a day ever showed -100%.
-- ---------------------------------------------------------------------
create or replace view public.weekly_performance as
select
    (date_trunc('week', trade_date))::date      as week_start,
    symbol,
    max(name)                                   as name,
    max(asset_class)                            as asset_class,
    count(*)                                    as sessions,
    min(trade_date)                             as first_session,
    max(trade_date)                             as last_session,
    round(
        ((exp(sum(ln(greatest(1 + change_pct / 100.0, 0.0001)))) - 1) * 100)::numeric,
        4
    )                                           as week_return_pct
from public.daily_prices
where change_pct is not null
group by 1, 2;


-- ---------------------------------------------------------------------
-- SECURITY (Row Level Security)
--
-- Think of RLS as a locked front door.
--  - The website uses the "anon" key. It gets a read-only visitor pass.
--  - The Python robot uses the "service_role" key, which is the master
--    key and skips RLS entirely. That key lives only in GitHub Secrets.
-- ---------------------------------------------------------------------
alter table public.daily_prices  enable row level security;
alter table public.daily_report  enable row level security;
alter table public.econ_calendar enable row level security;

drop policy if exists "public read prices"   on public.daily_prices;
drop policy if exists "public read report"   on public.daily_report;
drop policy if exists "public read calendar" on public.econ_calendar;

create policy "public read prices"
    on public.daily_prices for select to anon, authenticated using (true);

create policy "public read report"
    on public.daily_report for select to anon, authenticated using (true);

create policy "public read calendar"
    on public.econ_calendar for select to anon, authenticated using (true);

-- Views inherit the permissions of the tables underneath, but the anon
-- role still needs permission to look at the view itself.
grant select on public.weekly_performance to anon, authenticated;
