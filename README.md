# Nasdaq 100 Market Dashboard

A market dashboard that updates itself every weekday after the US close, and
costs nothing to run.

```
GitHub Actions          Python (main.py)         Supabase            Vercel
(the alarm clock)   ->  (the journalist)     ->  (the archive)   ->  (the newsstand)
  fires 16:15 ET          gets prices,             stores every        serves the
  Mon-Fri                 does the maths,          day forever         web page
                          asks Gemini
```

---

## What it shows

**Section A - Daily Market Summary**
QQQ and Nasdaq futures closes, all 11 sector SPDR ETFs ranked by relative
strength, and an AI-written summary of the macro news that drove the session,
with real source links.

**Section B - Technicals and Outlook**
EMA 9 / 20 / 50 / 200 for QQQ and the three strongest sectors, charted and
tabled, plus a swing-trader read of the setup and a view for the next session.

**Section C - This Week and Calendar**
Week-to-date performance for every ticker, properly compounded, and a rolling
calendar of upcoming market-moving events.

---

## Cost

| Service | Free limit | What this uses |
|---|---|---|
| GitHub Actions | unlimited on public repos, 2,000 min/month private | ~2 min per weekday (~45 min/month) |
| yfinance | free, no key needed | 13 tickers once a day |
| Supabase | 500 MB database | a few MB per year |
| Gemini API | free tier daily cap | 3 requests per weekday |
| Vercel Hobby | free | one static page |

Total: **$0**.

---

## Setup - about 20 minutes

### Step 1. Create the repository

Make a new GitHub repository and put these files in it:

```
your-repo/
├── .github/workflows/deploy.yml
├── main.py
├── requirements.txt
├── schema.sql
├── test_logic.py
├── vercel.json
├── .env.example
├── .gitignore
└── web/index.html
```

A **public** repo gets unlimited free Actions minutes. A private one gets
2,000 minutes a month, which is still far more than this needs.

### Step 2. Set up Supabase

1. Go to supabase.com and sign up.
2. If you have no organization yet, click **New organization** first. It is
   just a folder to hold projects - name it anything, type Personal, plan
   **Free**. No card required.
3. Click **New project**. Pick any name and the closest region (Sydney or
   Singapore from Australia). Save the database password somewhere - you
   will not need it here, but losing it is annoying later.
4. Wait about two minutes for the project to finish building.
5. Open **SQL Editor** in the left sidebar, click **New query**.
6. Paste the whole of `schema.sql`, click **Run**. You should see
   "Success. No rows returned".
7. Go to **Settings -> Data API** and copy the **Project URL**.
   It looks like `https://abcdefghijk.supabase.co`.
8. Go to **Settings -> API Keys**. Copy two things:

   | Key | Starts with | Goes into | Safe in public? |
   |---|---|---|---|
   | **Publishable key** | `sb_publishable_` | `web/index.html` | yes |
   | **Secret key** | `sb_secret_` | GitHub Secret `SUPABASE_SERVICE_KEY` | **no** |

> **Use the new keys, not the legacy ones.** You may also see a "Legacy API
> keys" tab holding `anon` and `service_role` keys that start with `eyJ`.
> Those still work, but Supabase is retiring them by the end of 2026. Older
> tutorials all reference them. Start on the new keys and you won't have to
> redo this later. `index.html` handles either style automatically.

> **What is the difference?** The publishable key is like a library card: it
> can read the shelves and nothing more, because `schema.sql` turned on Row
> Level Security with a read-only rule. The secret key is the librarian's
> master key and bypasses every one of those rules. It goes in GitHub Secrets
> and nowhere else. Supabase also rejects a secret key with a 401 if it is
> ever used from a browser.

### Step 3. Get a Gemini API key

1. Go to aistudio.google.com and sign in with a Google account.
2. Click **Get API key -> Create API key**.
3. Copy it. It starts with `AIza`.

### Step 4. Put the secrets into GitHub

In your repository: **Settings -> Secrets and variables -> Actions ->
New repository secret**. Add three, one at a time:

| Name | Value |
|---|---|
| `SUPABASE_URL` | your Project URL |
| `SUPABASE_SERVICE_KEY` | the **secret** key (`sb_secret_...`) |
| `GEMINI_API_KEY` | your Gemini key |

Names must match exactly - they are case sensitive. Watch for a stray space
at the end when you paste.

Optionally add a **variable** (not a secret) called `GEMINI_MODEL` if you
want a different model. It defaults to `gemini-3.5-flash`.

### Step 5. Run it once by hand

Go to the **Actions** tab, pick **Daily Market Update**, click
**Run workflow**. Set force to `yes` for this first run so it does not skip
because the market is shut.

Watch the log. A good run ends with `Done.` Then go back to Supabase ->
**Table Editor** and you should see rows in `daily_prices` and one row in
`daily_report`.

### Step 6. Point the web page at your database

Open `web/index.html` and edit the two lines near the top of the
`<script>` block:

```js
const SUPABASE_URL      = "https://YOUR-PROJECT-REF.supabase.co";
const SUPABASE_ANON_KEY = "YOUR-ANON-KEY";
```

Use the **publishable** key here (`sb_publishable_...`), never the secret key.
Commit and push.

### Step 7. Deploy to Vercel

1. Go to vercel.com, sign in with GitHub.
2. **Add New -> Project**, pick your repository.
3. Framework Preset: **Other**. Leave the build command empty.
   `vercel.json` already tells Vercel to serve the `web` folder.
4. Click **Deploy**.

You get a URL like `your-repo.vercel.app`. Every push to the repo redeploys
it automatically.

---

## Running it on your own machine

```bash
pip install -r requirements.txt
cp .env.example .env        # then fill .env in

# load the .env and run
export $(grep -v '^#' .env | xargs)

python main.py --dry-run    # fetch and calculate, write nothing
python main.py --no-ai      # prices only, no Gemini calls
python main.py --force      # ignore the "is the market shut?" check
python test_logic.py        # run the maths checks, no internet needed
```

---

## How the scheduling actually works

GitHub Actions cron is always UTC and knows nothing about US daylight saving.
16:15 New York time is 20:15 UTC in summer and 21:15 UTC in winter, so the
workflow fires at **both** times all year round.

`main.py` then does two checks:

1. **Is there a fresh close?** The last QQQ bar must be dated today in New
   York, **and** the New York clock must be past 16:05. Without that second
   check the winter 20:15 UTC run would fire at 15:15 ET, while the market is
   still open, and Yahoo would hand back a half-finished bar that would get
   saved as "the close".
2. **Has today already been done?** If a complete report exists for today,
   the second run exits instead of spending three more Gemini calls.

So on a normal day exactly one run does the work. If the first one fails, the
second becomes a free automatic retry. And because every database write is an
`upsert`, running twice can never create duplicate rows.

---

## Design notes

**EMA maths.** `ema()` seeds with a simple average of the first `span` bars,
then rolls forward with `k = 2 / (span + 1)`. That is how TradingView and
StockCharts do it, so the numbers match what you see on a normal chart.
`test_logic.py` verifies it against a hand-worked example and against a
separate implementation using pandas' own `ewm` engine.

**Weekly returns compound, they do not add.** `+1%` then `+1%` is `+2.01%`,
not `+2%`. The `weekly_performance` view uses
`(exp(sum(ln(1 + r))) - 1)` to get this right.

**Two colour languages, used consistently.**

- Green and red, always with a ▲ or ▼ arrow and a signed number, mean
  *the price moved up or down*. This is the trading convention and it only
  appears as text, never as a chart mark.
- Blue and red mean *relative strength* - above or below the zero line.
  This is used in the relative-strength chart, the weekly chart, and the
  "RS vs QQQ" table column, so the same number is never blue in one place
  and green in another.

Green and red are deliberately not used for the chart bars: that pair has a
colour-blind separation of ΔE 4.1, well below the safe threshold of 8, so
red/green bars would be unreadable for a large number of people. Every bar
also carries its signed value as text, so colour is never the only signal.

**Why 2 years of history?** EMA 200 needs at least 200 trading days before it
means anything. Two years gives about 500, with room to spare.

---

## When something breaks

| Symptom | Most likely cause |
|---|---|
| Action fails with "yfinance returned no data" | Yahoo changed or throttled. Try `pip install -U yfinance`. It is an unofficial library. |
| Dashboard shows the red error card | Wrong URL/anon key in `index.html`, or `schema.sql` was never run. |
| Everything worked, then stopped after a holiday break | Supabase pauses free projects after about 7 days of no activity. Open the dashboard to wake it. The daily job normally keeps it awake by itself. |
| Prices appear but the commentary is empty | Gemini's free daily quota ran out, or the key is wrong. Prices are saved first on purpose, so this degrades instead of breaking. |
| Numbers look like a mid-session snapshot | Should not happen - the 16:05 ET guard exists for this. If it does, check the runner's clock in the Action log. |

---

## Not financial advice

The written outlook is a language model reading a table of numbers. It can be
confidently wrong. Prices come from Yahoo Finance through an unofficial
library and are not guaranteed. This is a dashboard, not a signal service.
