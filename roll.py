#!/usr/bin/env python3
"""Keep flights.csv covering today..today+2 UTC by copying the most recent date's flight set forward.
Idempotent; never deletes. Run before each probe pass."""
import csv
from datetime import datetime, timezone, timedelta
rows = list(csv.DictReader(open("flights.csv", newline="")))
if not rows: raise SystemExit
dates = sorted({r["date"] for r in rows})
latest = dates[-1]
template = [r for r in rows if r["date"] == latest]
today = datetime.now(timezone.utc).date()
want = [(today + timedelta(days=i)).isoformat() for i in range(3)]
have = set(dates); added = 0
for d in want:
    if d in have: continue
    for r in template:
        rows.append({**r, "date": d}); added += 1
if added:
    fields = list(rows[0].keys())
    with open("flights.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields); w.writeheader()
        w.writerows(sorted(rows, key=lambda r: (r["date"], r["flight"])))
print(f"roll: +{added} rows" if added else "roll: nothing to add")
