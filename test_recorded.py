"""Check already_recorded() treats a daytime test run as NOT final."""
import sys
sys.path.insert(0, "/home/claude/qqq-dashboard")
import main

class FakeSB:
    def __init__(self, rows): self.rows = rows
    def table(self, *_): return self
    def select(self, *_): return self
    def eq(self, *_): return self
    def limit(self, *_): return self
    def execute(self):
        return type("R", (), {"data": self.rows})()

D = "2026-09-14"
cases = [
    ("no row at all",                        [],                                                             False),
    ("row with no outlook",                  [{"trade_date": D, "ai_outlook": "", "updated_at": f"{D}T16:20:00-04:00"}], False),
    ("FORCED test run at 10:05 ET",          [{"trade_date": D, "ai_outlook": "x", "updated_at": f"{D}T10:05:00-04:00"}], False),
    ("forced run at 15:59 ET (still open)",  [{"trade_date": D, "ai_outlook": "x", "updated_at": f"{D}T15:59:00-04:00"}], False),
    ("real run at 16:00 ET exactly",         [{"trade_date": D, "ai_outlook": "x", "updated_at": f"{D}T16:00:00-04:00"}], True),
    ("real run at 16:20 ET",                 [{"trade_date": D, "ai_outlook": "x", "updated_at": f"{D}T16:20:00-04:00"}], True),
    ("second cron at 17:15 ET",              [{"trade_date": D, "ai_outlook": "x", "updated_at": f"{D}T17:15:00-04:00"}], True),
    ("backfilled the next morning",          [{"trade_date": D, "ai_outlook": "x", "updated_at": "2026-09-15T09:00:00-04:00"}], True),
    ("naive timestamp, post-close",          [{"trade_date": D, "ai_outlook": "x", "updated_at": f"{D}T16:20:00"}],       True),
    ("naive timestamp, mid-session",         [{"trade_date": D, "ai_outlook": "x", "updated_at": f"{D}T10:05:00"}],       False),
    ("missing updated_at",                   [{"trade_date": D, "ai_outlook": "x"}],                          False),
    ("UTC stamp 20:20Z = 16:20 ET",          [{"trade_date": D, "ai_outlook": "x", "updated_at": f"{D}T20:20:00+00:00"}], True),
    ("UTC stamp 14:05Z = 10:05 ET",          [{"trade_date": D, "ai_outlook": "x", "updated_at": f"{D}T14:05:00+00:00"}], False),
]

fails = 0
for name, rows, expect in cases:
    got = main.already_recorded(FakeSB(rows), D)
    ok = got == expect
    fails += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name:38s} -> skip={got} (want {expect})")

print()
print("All good." if not fails else f"{fails} FAILED")
sys.exit(1 if fails else 0)
