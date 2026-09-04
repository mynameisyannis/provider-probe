#!/usr/bin/env python3
"""Sample every provider for a list of flights and append what each said to a CSV.

Stdlib only. Keys come from the environment: ADB_KEY, AEROAPI_KEY, FR24_TOKEN.

    python3 probe.py flights.csv                # ADB + AeroAPI, one row per provider per instance
    python3 probe.py flights.csv --fr24         # also FR24 live positions (8 credits on a hit, 1 on a miss)
    python3 probe.py flights.csv --truth        # FR24 flight-summary for the date (3 credits/record) -> ground truth
    --from-today                                 # skip rows whose date is in the past (for the probe pass)
    --yesterday                                  # only rows dated yesterday UTC (for the truth pass)

flights.csv columns (header required):
    flight,date[,icao]
    BA1466,2026-09-04
    EI183,2026-09-04,EIN183      # optional ICAO ident for AeroAPI; derived from the table below if absent

Every response is appended to observations.csv with the UTC time it was taken, so the
same flight probed at T-24h, T-12h, T-6h, T-3h, T-1h and T+15m builds a curve.
"""
import csv, json, os, sys, time, urllib.request, urllib.parse, urllib.error
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

OUT = "observations.csv"
FIELDS = ["observed_at_utc", "flight", "date_local", "provider", "instance_scheduled_out_utc",
          "registration", "status", "provider_last_updated", "inbound_id", "operating", "raw_note"]

IATA_TO_ICAO = {
    "BA": "BAW", "EI": "EIN", "KL": "KLM", "LX": "SWR", "LH": "DLH", "U2": "EZY", "FR": "RYR",
    "AF": "AFR", "IB": "IBE", "VS": "VIR", "AA": "AAL", "UA": "UAL", "DL": "DAL", "EK": "UAE",
    "QR": "QTR", "TK": "THY", "SK": "SAS", "AY": "FIN", "OS": "AUA", "SN": "BEL", "TP": "TAP",
    "AZ": "ITY", "VY": "VLG", "W6": "WZZ", "LO": "LOT", "EW": "EWG", "BE": "BEE", "LS": "EXS",
    "TOM": "TOM", "BY": "TOM", "DY": "NAX", "D8": "NAX", "AC": "ACA", "NZ": "ANZ", "QF": "QFA",
    "SQ": "SIA", "CX": "CPA", "JL": "JAL", "NH": "ANA", "EY": "ETD", "SV": "SVA", "MS": "MSR",
    "LG": "LGL", "GR": "AUR", "KM": "KMM", "FI": "ICE", "EW": "EWG", "HV": "TRA", "PC": "PGT", "A3": "AEE",
}

UA = "curl/8.7.1"   # api.market's Cloudflare returns 1010 for Python-urllib's default signature

def get(url, headers, retries=4):
    """Returns (status, parsed_json_or_None, note). Retries 429 with a growing pause."""
    req = urllib.request.Request(url, headers={"User-Agent": UA, **headers})
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                body = r.read().decode()
                return r.status, (json.loads(body) if body.strip() else None), ""
        except urllib.error.HTTPError as e:
            snippet = ""
            try: snippet = e.read().decode(errors="replace")[:160].replace("\n", " ")
            except Exception: pass
            if e.code == 429 and attempt < retries:
                time.sleep(15 * (attempt + 1)); continue
            return e.code, None, snippet
        except Exception as e:  # network, decode
            return -1, None, str(e)[:160]

def now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()

def split_flight(f):
    f = f.strip().upper().replace(" ", "")
    i = 0
    while i < len(f) and not f[i].isdigit(): i += 1
    if i in (2, 3) and f[:i] in IATA_TO_ICAO: return f[:i], f[i:]
    # two-letter IATA by default
    return f[:2], f[2:]

def emit(w, **row):
    w.writerow({k: row.get(k, "") for k in FIELDS})

# ---------- AeroDataBox (api.market) ----------
def probe_adb(w, flight, date):
    key = os.environ.get("ADB_KEY")
    if not key: return
    url = (f"https://prod.api.market/api/v1/aedbx/aerodatabox/flights/Number/{flight}/{date}"
           "?dateLocalRole=Departure&withAircraftImage=false&withLocation=false&withFlightPlan=false")
    st, body, note = get(url, {"x-api-market-key": key, "accept": "application/json"})
    ts = now()
    if st != 200 or not isinstance(body, list):
        emit(w, observed_at_utc=ts, flight=flight, date_local=date, provider="adb", raw_note=f"http {st} {note}".strip())
        return
    for x in body:
        emit(w, observed_at_utc=ts, flight=flight, date_local=date, provider="adb",
             instance_scheduled_out_utc=(x.get("departure", {}).get("scheduledTime", {}) or {}).get("utc", ""),
             registration=(x.get("aircraft") or {}).get("reg", "") or "",
             status=x.get("status", ""), provider_last_updated=x.get("lastUpdatedUtc", ""),
             operating=x.get("codeshareStatus", ""))

# ---------- FlightAware AeroAPI ----------
def probe_aeroapi(w, flight, date, icao):
    key = os.environ.get("AEROAPI_KEY")
    if not key: return
    d = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    nowdt = datetime.now(timezone.utc)
    latest = nowdt + timedelta(hours=47)                       # AeroAPI refuses end > ~2 days ahead
    end = min(d + timedelta(days=1), latest)
    start = d - timedelta(days=1)
    if end <= start + timedelta(hours=1):
        emit(w, observed_at_utc=now(), flight=flight, date_local=date, provider="aeroapi", raw_note="skipped: beyond AeroAPI window")
        return
    fmt = lambda t: t.strftime("%Y-%m-%dT%H:%M:%SZ")
    q = urllib.parse.urlencode({"ident_type": "designator", "start": fmt(start), "end": fmt(end), "max_pages": 1})
    st, body, note = get(f"https://aeroapi.flightaware.com/aeroapi/flights/{icao}?{q}", {"x-apikey": key})
    ts = now()
    if st != 200 or not isinstance(body, dict):
        emit(w, observed_at_utc=ts, flight=flight, date_local=date, provider="aeroapi", raw_note=f"http {st} {note}".strip())
        return
    matched = 0
    for f in body.get("flights", []):
        so = f.get("scheduled_out") or ""
        tz = (f.get("origin") or {}).get("timezone")
        local_date = ""
        if so and tz:
            try:
                local_date = datetime.fromisoformat(so.replace("Z", "+00:00")).astimezone(ZoneInfo(tz)).date().isoformat()
            except Exception:
                pass
        if local_date and local_date != date:
            continue  # a different service date of the same number
        matched += 1
        emit(w, observed_at_utc=ts, flight=flight, date_local=date, provider="aeroapi",
             instance_scheduled_out_utc=so, registration=f.get("registration") or "",
             status=f.get("status", ""), inbound_id=f.get("inbound_fa_flight_id") or "",
             operating=f.get("operator_icao") or f.get("operator") or "",
             raw_note="codeshares=" + ",".join(f.get("codeshares_iata") or []) + f" dist={f.get('route_distance') or ''}")
    if matched == 0:
        emit(w, observed_at_utc=ts, flight=flight, date_local=date, provider="aeroapi",
             raw_note=f"200 but no instance on {date} in window {fmt(start)}..{fmt(end)} ({len(body.get('flights', []))} other)")

# ---------- Flightradar24 ----------
FR24_H = lambda: {"Authorization": f"Bearer {os.environ['FR24_TOKEN']}", "Accept-Version": "v1"}

def probe_fr24_live(w, flight, date):
    if not os.environ.get("FR24_TOKEN"): return
    st, body, note = get(f"https://fr24api.flightradar24.com/api/live/flight-positions/full?flights={flight}", FR24_H())
    ts = now()
    rows = (body or {}).get("data", []) if isinstance(body, dict) else []
    if not rows:
        emit(w, observed_at_utc=ts, flight=flight, date_local=date, provider="fr24_live", raw_note=f"empty http {st} {note}".strip())
    for x in rows:
        emit(w, observed_at_utc=ts, flight=flight, date_local=date, provider="fr24_live",
             registration=x.get("reg", ""), status="airborne", provider_last_updated=x.get("timestamp", ""),
             operating=x.get("operating_as", ""), raw_note=f"{x.get('orig_iata')}-{x.get('dest_iata')}")

def probe_fr24_truth(w, flight, date, origin_tz="Europe/London"):
    """flight-summary for the local date, window built from origin tz. 3 credits per record."""
    if not os.environ.get("FR24_TOKEN"): return
    d0 = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=ZoneInfo(origin_tz))
    frm = d0.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    to = (d0 + timedelta(days=1)).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    q = urllib.parse.urlencode({"flights": flight, "flight_datetime_from": frm, "flight_datetime_to": to})
    st, body, note = get(f"https://fr24api.flightradar24.com/api/flight-summary/full?{q}", FR24_H())
    ts = now()
    rows = (body or {}).get("data", []) if isinstance(body, dict) else []
    if not rows:
        emit(w, observed_at_utc=ts, flight=flight, date_local=date, provider="fr24_truth", raw_note=f"empty http {st} {note}".strip())
    for x in rows:
        emit(w, observed_at_utc=ts, flight=flight, date_local=date, provider="fr24_truth",
             instance_scheduled_out_utc=x.get("datetime_takeoff", ""), registration=x.get("reg", ""),
             status="ended" if x.get("flight_ended") else "flying", provider_last_updated=x.get("last_seen", ""),
             operating=x.get("operating_as", ""), raw_note=f"{x.get('orig_iata')}-{x.get('dest_iata')} first_seen={x.get('first_seen')}")

def main():
    if len(sys.argv) < 2:
        print(__doc__); sys.exit(1)
    src = sys.argv[1]; flags = set(sys.argv[2:])
    new = not os.path.exists(OUT)
    with open(src, newline="") as fh, open(OUT, "a", newline="") as out:
        w = csv.DictWriter(out, fieldnames=FIELDS)
        if new: w.writeheader()
        today = datetime.now(timezone.utc).date()
        for r in csv.DictReader(fh):
            flight = r["flight"].strip().upper().replace(" ", "")
            date = r["date"].strip()
            d = datetime.strptime(date, "%Y-%m-%d").date()
            if "--from-today" in flags and d < today: continue
            if "--yesterday" in flags and d != today - timedelta(days=1): continue
            icao = (r.get("icao") or "").strip().upper()
            if not icao:
                al, num = split_flight(flight)
                icao = IATA_TO_ICAO.get(al, al) + num
            if "--truth" in flags:
                probe_fr24_truth(w, flight, date, (r.get("origin_tz") or "Europe/London").strip())
            else:
                probe_adb(w, flight, date); time.sleep(0.5)
                probe_aeroapi(w, flight, date, icao); time.sleep(1.5)
                if "--fr24" in flags:
                    probe_fr24_live(w, flight, date); time.sleep(0.3)
            out.flush()
    print(f"appended to {OUT}")

if __name__ == "__main__":
    main()
