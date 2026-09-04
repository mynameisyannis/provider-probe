#!/usr/bin/env python3
"""Score observations.csv: who had the registration first, who was right, who disagreed.

    python3 score.py                 # reads observations.csv, prints the report
    python3 score.py --csv           # also writes per-flight.csv

Ground truth for a flight+date is, in order of preference:
  1. fr24_truth (flight-summary after the flight) -- ADS-B observed
  2. fr24_live  (a position while airborne)
  3. none -> the flight is scored for lead time and disagreement only
"""
import csv, os, sys
from collections import defaultdict
from datetime import datetime

def parse(ts):
    return datetime.fromisoformat(ts.replace("Z", "+00:00")) if ts else None

if not os.path.exists("observations.csv"):
    print("no observations.csv yet"); sys.exit(0)
rows = list(csv.DictReader(open("observations.csv", newline="")))

def norm_sched(v):
    """'2026-09-03 20:45Z' (ADB) or '2026-09-03T20:45:00Z' (AeroAPI) -> aware datetime, or None."""
    if not v: return None
    v = v.strip().replace(" ", "T").replace("Z", "+00:00")
    if len(v) == 22: v = v[:16] + ":00" + v[16:]   # ADB has no seconds
    try: return datetime.fromisoformat(v)
    except Exception: return None

# Group by flight+date, then split into instances by scheduled departure (a number can fly twice a day).
# Truth rows (FR24) carry a takeoff time instead; they are attached to the nearest instance below.
by = defaultdict(list)
for r in rows:
    by[(r["flight"], r["date_local"])].append(r)

instances = {}   # (flight, date, sched_iso) -> list of provider rows
for (flight, date), obs in by.items():
    scheds = sorted({norm_sched(o["instance_scheduled_out_utc"]) for o in obs
                     if o["provider"] in ("adb", "aeroapi") and norm_sched(o["instance_scheduled_out_utc"])})
    if not scheds:
        instances[(flight, date, "")] = obs; continue
    # collapse schedules within 90 minutes of each other (ADB revisedTime vs AeroAPI scheduled_out drift)
    buckets = []
    for sc in scheds:
        if buckets and (sc - buckets[-1][-1]).total_seconds() < 5400: buckets[-1].append(sc)
        else: buckets.append([sc])
    keys = [b[0] for b in buckets]
    def nearest(t):
        return min(keys, key=lambda k: abs((k - t).total_seconds()))
    for o in obs:
        if o["provider"] in ("adb", "aeroapi"):
            sc = norm_sched(o["instance_scheduled_out_utc"])
            k = nearest(sc) if sc else keys[0]
        else:  # fr24_truth carries datetime_takeoff; fr24_live carries nothing useful -> first instance
            t = norm_sched(o["instance_scheduled_out_utc"])
            k = nearest(t) if t else keys[0]
        instances.setdefault((flight, date, k.isoformat()), []).append(o)
by = {(f"{fl}@{k[11:16]}" if k else fl, d): v for (fl, d, k), v in instances.items()}

def airline(flight):
    """IATA carrier code: two letters, or letter+digit (U2, W6, D8, A3), or three letters (TOM)."""
    flight = flight.split("@")[0]
    if len(flight) > 3 and flight[:3].isalpha() and flight[3].isdigit(): return flight[:3]
    return flight[:2]

per_flight = []
lead = defaultdict(list)          # provider -> hours before departure the reg first appeared
correct = defaultdict(lambda: [0, 0])   # provider -> [right, wrong] on pre-departure observations
coverage = defaultdict(lambda: defaultdict(lambda: [0, 0]))  # airline -> provider -> [had, total flights]
disagree = []
swaps = defaultdict(int)

for (flight, date), obs in sorted(by.items()):
    truth = next((o["registration"] for o in obs if o["provider"] == "fr24_truth" and o["registration"]), None) \
         or next((o["registration"] for o in obs if o["provider"] == "fr24_live" and o["registration"]), None)
    sched = next((parse(o["instance_scheduled_out_utc"]) for o in obs
                  if o["provider"] == "aeroapi" and o["instance_scheduled_out_utc"]), None) \
         or next((parse(o["instance_scheduled_out_utc"].replace(" ", "T").replace("Z", "+00:00"))
                  for o in obs if o["provider"] == "adb" and o["instance_scheduled_out_utc"]), None)
    al = airline(flight)
    line = {"flight": flight, "date": date, "truth": truth or "", "scheduled_out": sched.isoformat() if sched else ""}
    pre = {}
    for prov in ("adb", "aeroapi"):
        seq = sorted((o for o in obs if o["provider"] == prov), key=lambda o: o["observed_at_utc"])
        if sched:
            seq = [o for o in seq if parse(o["observed_at_utc"]) < sched]
        coverage[al][prov][1] += 1
        first = next((o for o in seq if o["registration"]), None)
        if first:
            coverage[al][prov][0] += 1
            if sched:
                lead[prov].append((sched - parse(first["observed_at_utc"])).total_seconds() / 3600)
        vals = [o["registration"] for o in seq if o["registration"]]
        if len(set(vals)) > 1:
            swaps[prov] += 1
        last = vals[-1] if vals else ""
        pre[prov] = last
        line[f"{prov}_first_reg"] = first["registration"] if first else ""
        line[f"{prov}_first_at"] = first["observed_at_utc"] if first else ""
        line[f"{prov}_last_reg"] = last
        if truth and last:
            correct[prov][0 if last == truth else 1] += 1
    if pre.get("adb") and pre.get("aeroapi") and pre["adb"] != pre["aeroapi"]:
        disagree.append((flight, date, pre["adb"], pre["aeroapi"], truth or "?"))
    per_flight.append(line)

def med(xs):
    xs = sorted(xs); n = len(xs)
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2 if n else float("nan")

print(f"\n{len(by)} flight-dates, {len(rows)} observations\n")
print("LEAD TIME (hours before departure the registration first appeared, pre-departure obs only)")
for p in ("adb", "aeroapi"):
    if lead[p]:
        print(f"  {p:8s} n={len(lead[p]):3d}  median={med(lead[p]):5.1f}h  min={min(lead[p]):5.1f}h  max={max(lead[p]):5.1f}h")
print("\nACCURACY (last pre-departure registration vs ADS-B truth)")
for p in ("adb", "aeroapi"):
    r, w = correct[p]
    if r + w: print(f"  {p:8s} right={r} wrong={w}  ({100*r/(r+w):.0f}%)")
print(f"\nSWAPS (registration changed between observations before departure): " +
      ", ".join(f"{p}={n}" for p, n in swaps.items()) if swaps else "\nSWAPS: none")
print("\nDISAGREEMENTS (both had a value, values differ) -> adb / aeroapi / truth")
for d in disagree or [("none",)]:
    print("  " + " / ".join(map(str, d)))
print("\nCOVERAGE (flights where the provider had a registration before departure)")
for al in sorted(coverage):
    print(f"  {al:4s} " + "  ".join(f"{p}={coverage[al][p][0]}/{coverage[al][p][1]}" for p in ("adb", "aeroapi")))

# ---- when does the registration settle? change rate between consecutive observations ----
import re
def hb(h): return "<3h" if h < 3 else "3-6h" if h < 6 else "6-12h" if h < 12 else "12-24h" if h < 24 else "24h+"
def haul(mi): return "unknown" if mi is None else "short" if mi < 900 else "medium" if mi < 2500 else "long"
by_hours = defaultdict(lambda: [0, 0]); by_haul = defaultdict(lambda: [0, 0]); inst_haul = {}
for (flight, date), obs in by.items():
    for o in obs:
        m = re.search(r"dist=(\d+)", o.get("raw_note", "") or "")
        if m: inst_haul[(flight, date)] = int(m.group(1))
for (flight, date), obs in by.items():
    sched = next((parse(o["instance_scheduled_out_utc"]) for o in obs if o["provider"] == "aeroapi" and o["instance_scheduled_out_utc"]), None)
    if not sched: continue
    for prov in ("adb", "aeroapi"):
        seq = sorted(((parse(o["observed_at_utc"]), o["registration"]) for o in obs
                      if o["provider"] == prov and o["registration"] and parse(o["observed_at_utc"]) < sched))
        for i in range(1, len(seq)):
            hours = (sched - seq[i][0]).total_seconds() / 3600
            changed = seq[i][1] != seq[i - 1][1]
            by_hours[hb(hours)][1] += 1; by_hours[hb(hours)][0] += changed
            hz = haul(inst_haul.get((flight, date)))
            by_haul[hz][1] += 1; by_haul[hz][0] += changed
print("\nSETTLING (registration changed since the previous observation, by hours before departure)")
for b in ("24h+", "12-24h", "6-12h", "3-6h", "<3h"):
    c, n = by_hours[b]
    if n: print(f"  {b:7s} {c:3d} of {n:4d}  ({100*c/n:4.1f}%)")
print("\nSETTLING BY HAUL (route distance from AeroAPI; short <900mi, medium <2500mi, long beyond)")
for b in ("short", "medium", "long", "unknown"):
    c, n = by_haul[b]
    if n: print(f"  {b:8s} {c:3d} of {n:4d}  ({100*c/n:4.1f}%)")

if "--csv" in sys.argv:
    keys = sorted({k for l in per_flight for k in l})
    with open("per-flight.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=keys); w.writeheader(); w.writerows(per_flight)
    print("\nwrote per-flight.csv")
