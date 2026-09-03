# Provider probe — a week of evidence before a migration

Two stdlib-only scripts. Nothing installs, nothing touches the repo or hosted.

    export ADB_KEY=...  AEROAPI_KEY=...  FR24_TOKEN=...
    python3 probe.py flights.csv            # ADB + AeroAPI for every flight in the list
    python3 probe.py flights.csv --fr24     # + FR24 live position (only worth it near/after departure)
    python3 probe.py flights.csv --truth    # next day: FR24 flight-summary = what actually flew
    python3 score.py --csv                  # the report

## 1. Pick the sample

Aim for 20–30 flights a day across airlines, not just BA. The best sample is
what Cleared's travellers actually track — no PII in this, it is airline,
number and date only. In the hosted SQL editor:

    select airline_iata || flight_number as flight, departure_date_local as date
    from public.flights
    where departure_date_local between current_date and current_date + 2
    group by 1, 2 order by 2, 1;

Paste the result into `flights.csv` (header `flight,date`). Top it up by hand
so every one of these is represented at least twice:

- BA mainline, BA CityFlyer          (AeroAPI led today)
- Aer Lingus                          (ADB led today)
- KLM / KLM Cityhopper                (nobody had it today)
- Swiss / Helvetic, Lufthansa, Air France
- easyJet, Ryanair                    (the bulk of European leisure travel)
- one US carrier transatlantic, one Gulf carrier
- at least three departures before 08:00 local (overnight assignment)

## 2. Run it on a cadence

Each flight is worth probing at roughly **T−24h, T−12h, T−6h, T−3h, T−1h, and
T+15m**. The easiest way is to run the whole list every three hours and let
`score.py` work out the lead time from the timestamps. On a Mac, `crontab -e`:

    0 */3 * * *   cd ~/provider-probe && python3 probe.py flights.csv >> probe.log 2>&1
    30 23 * * *   cd ~/provider-probe && python3 probe.py yesterday.csv --truth >> probe.log 2>&1

(`yesterday.csv` is the previous day's flights; `--truth` fetches what ADS-B
saw for each, 3 credits a record.) Keep `flights.csv` rolling — drop flights
once flown, add the next two days' each morning.

## 3. What it costs

Per flight, per probe: 1 ADB unit + $0.005 AeroAPI. Per flight, once: 3 FR24
credits for truth. 25 flights × 8 probes/day × 5 days:

| Provider | Spend |
|---|---|
| AeroDataBox | ~1,000 units |
| AeroAPI | ~$5 (well inside the Personal tier's $5/month free allowance; evaluation use) |
| FR24 | ~375 credits for truth, plus 1 per empty live call if `--fr24` is used |

## 4. What decides the rule

`score.py` prints five things. The decision falls out of them:

| Metric | What it answers | Decision |
|---|---|---|
| **Lead time** per provider | Who has the registration earlier, and by how much | Whether a second source is worth anything at all |
| **Accuracy** vs ADS-B truth | Who is *right* pre-departure | Whether AeroAPI can be trusted to override a present ADB value |
| **Disagreements**, with truth | When they conflict, who wins | Gap-fill (ADB never wrong) vs precedence (ADB sometimes wrong) |
| **Swaps** | How often an early registration changes before departure | Whether any early value needs a freshness/provenance guard |
| **Coverage** by airline | Where each provider has nothing | Which airlines need the fallback, and whether either provider is ever redundant |

Thresholds worth agreeing before the data comes in, so they are not fitted to
it afterwards:

- If AeroAPI is **wrong on ≥ 1 flight where ADB was right**, plain AeroAPI
  precedence is out; either gap-fill or the inbound-leg provenance rule.
- If ADB is **wrong on ≥ 2 flights where AeroAPI was right**, gap-fill is
  out; some form of precedence is needed.
- If neither is ever wrong where the other is right, **gap-fill**, both
  directions, ADB primary — the smallest rule.
- If AeroAPI's median lead over ADB is **under two hours** on the airlines
  travellers fly, the whole registration half of P18 is not worth its licence
  fee and the phase narrows to comparison only.

## 5. What this does not validate, and what does

- **Licensing.** A written answer from FlightAware support on whether a
  consumer app can run on Personal, or must be on Standard. Ask now; it is on
  the critical path under every outcome above.
- **Rate limits.** AeroAPI Personal is rate-limited; the scripts sleep 0.3 s
  between calls. If a probe run logs `http 429`, space the list out.
- **Cleared's identity model.** Both providers returned two BA249 instances
  on one local date. Check whether it can happen in the tracked data:

      select airline_iata, flight_number, departure_date_local, count(*)
      from public.flights group by 1, 2, 3 having count(*) > 1;

- **Monthly cost at real volume.** Tracked flights per day × 2 AeroAPI
  calls × $0.005, against the Standard tier's $100 minimum:

      select count(*) from public.flights
      where departure_date_local between current_date - 30 and current_date;
