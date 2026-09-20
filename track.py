"""Alaska Airlines nonstop round-trip fare tracker.

Checks each trip twice, once unfiltered and once with basic economy excluded,
appends every fare it sees to history.csv, and rebuilds dashboard.html from
that history. No API keys, no database, no notifications.
"""

from __future__ import annotations

import csv
import html
import os
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from fast_flights import FlightQuery, Passengers, create_query, get_flights
from fast_flights.exceptions import FlightsNotFound

# Change airports, dates or the airline here. One line per trip. Nothing else
# in this file needs touching.

# ---- EDIT THIS ----
AIRLINE   = "AS"     # IATA code, or "" for all airlines
MAX_STOPS = 0        # 0 = nonstop only, None = allow connections
ADULTS    = 1
TRIPS = [
    ("BUR", "SEA", "2026-11-06", "2026-11-07"),
    ("LAX", "SEA", "2026-11-06", "2026-11-07"),
]   # (home airport, destination, depart date, return date)
# ---- END EDIT ----

SEAT = "economy"
CURRENCY = "USD"
LANGUAGE = "en-US"

HISTORY_CSV = "history.csv"
DASHBOARD_HTML = "dashboard.html"

NL = chr(10)

CSV_HEADER = [
    "checked_at",
    "origin",
    "destination",
    "depart_date",
    "return_date",
    "days_out",
    "fare_brand",
    "outbound_time",
    "total_price",
]

# "any" is the cheapest fare of any brand. "main" excludes basic economy.
BRANDS = [("any", False), ("main", True)]

PACIFIC = ZoneInfo("America/Los_Angeles")


# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------

def to_pacific(moment):
    """Aware UTC datetime -> (Pacific datetime, 'PST'/'PDT')."""
    local = moment.astimezone(PACIFIC)
    return local, local.strftime("%Z") or "PT"


def pacific_today():
    """Today's date, Pacific time."""
    return datetime.now(timezone.utc).astimezone(PACIFIC).date()


def parse_iso(value):
    """'2026-09-20T18:03:00Z' -> aware UTC datetime, or None."""
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
    except (TypeError, ValueError):
        return None


def parse_day(value):
    """'2026-11-06' -> date, or None."""
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def pacific_stamp(iso_utc):
    """A quiet, human date: 'Sep 20, 2026 at 11:03 AM PDT'."""
    moment = parse_iso(iso_utc)
    if moment is None:
        return "an unknown time"
    local, label = to_pacific(moment)
    hour = local.hour % 12 or 12
    meridiem = "AM" if local.hour < 12 else "PM"
    return (
        f"{local.strftime('%b')} {local.day}, {local.year} at "
        f"{hour}:{local.minute:02d} {meridiem} {label}"
    )


def pacific_short(iso_utc):
    """'Sep 20' for an axis label."""
    moment = parse_iso(iso_utc)
    if moment is None:
        return ""
    local, _ = to_pacific(moment)
    return f"{local.strftime('%b')} {local.day}"


def hhmm(value):
    """SimpleDatetime.time -> 'HH:MM'.

    Google omits zero components, so the tuple can be (8,), (None, 31) or have
    a None in either slot. Every omitted component means zero.
    """
    parts = list(value or ())
    hour = (parts[0] if len(parts) > 0 else None) or 0
    minute = (parts[1] if len(parts) > 1 else None) or 0
    try:
        hour, minute = int(hour), int(minute)
    except (TypeError, ValueError):
        hour, minute = 0, 0
    return f"{hour % 24:02d}:{minute % 60:02d}"


def to_12h(value):
    """'13:05' -> '1:05 PM'. Hands back anything it cannot parse."""
    try:
        hour, minute = (int(part) for part in str(value).split(":"))
    except (TypeError, ValueError):
        return str(value) if value else "--"
    meridiem = "AM" if hour < 12 else "PM"
    return f"{hour % 12 or 12}:{minute:02d} {meridiem}"


def short_day(value):
    """'2026-11-06' -> 'Nov 6'."""
    day = parse_day(value)
    if day is None:
        return str(value)
    return f"{day.strftime('%b')} {day.day}"


def trip_dates(depart_date, return_date):
    """'Nov 6 to Nov 7, 2026'."""
    depart = parse_day(depart_date)
    out = f"{short_day(depart_date)} to {short_day(return_date)}"
    return f"{out}, {depart.year}" if depart else out


def to_int(value):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def money(value):
    return f"${value:,}"


# ---------------------------------------------------------------------------
# Fetching
# ---------------------------------------------------------------------------

def fetch_trip(origin, destination, depart_date, return_date, exclude_basic):
    """One round-trip query.

    Returns (fares, unpriced, unexpected_shape) where `fares` maps an outbound
    departure "HH:MM" to the cheapest total round-trip price leaving at that
    time. Raises on anything that goes wrong; the caller decides what to do.
    """
    airlines = [AIRLINE] if AIRLINE else None
    query = create_query(
        flights=[
            FlightQuery(
                date=depart_date,
                from_airport=origin,
                to_airport=destination,
                airlines=airlines,
                max_stops=MAX_STOPS,
            ),
            FlightQuery(
                date=return_date,
                from_airport=destination,
                to_airport=origin,
                airlines=airlines,
                max_stops=MAX_STOPS,
            ),
        ],
        trip="round-trip",
        seat=SEAT,
        passengers=Passengers(adults=ADULTS),
        currency=CURRENCY,
        language=LANGUAGE,
        exclude_basic_economy=exclude_basic,
    )
    results = get_flights(query)

    fares = {}
    unpriced = 0
    unexpected = 0

    for result in results:
        price = result.price
        # Google hands back $0 placeholders for itineraries it could not
        # price. Those are not fares.
        if isinstance(price, bool) or not isinstance(price, int) or price <= 0:
            unpriced += 1
            continue

        # A round-trip result carries the outbound journey only; the return is
        # not in the response. With MAX_STOPS=None that journey can be several
        # segments, and the first one always begins it, so read the departure
        # from there rather than assuming a single nonstop leg.
        segments = list(result.flights or ())
        if not segments:
            unexpected += 1
            continue

        departs = hhmm(segments[0].departure.time)
        # Several return pairings can share an outbound. Keep the cheapest.
        if departs not in fares or price < fares[departs]:
            fares[departs] = price

    return fares, unpriced, unexpected


# ---------------------------------------------------------------------------
# History
# ---------------------------------------------------------------------------

def append_history(rows):
    """Append rows, writing the header only when the file is new or empty."""
    has_content = os.path.exists(HISTORY_CSV) and os.path.getsize(HISTORY_CSV) > 0
    with open(HISTORY_CSV, "a", newline="", encoding="utf-8") as handle:
        # csv defaults to CRLF. Pin it to LF so the file reads the same
        # whether a run happened here or on the Linux runner.
        writer = csv.writer(handle, lineterminator=NL)
        if not has_content:
            writer.writerow(CSV_HEADER)
        writer.writerows(rows)


def read_history():
    """Every usable row in history.csv. A missing file means no history."""
    if not os.path.exists(HISTORY_CSV):
        return []

    rows = []
    with open(HISTORY_CSV, newline="", encoding="utf-8") as handle:
        for raw in csv.DictReader(handle):
            price = to_int(raw.get("total_price"))
            brand = (raw.get("fare_brand") or "").strip()
            if price is None or price <= 0:
                continue
            if not raw.get("checked_at") or brand not in ("any", "main"):
                continue
            rows.append(
                {
                    "checked_at": raw.get("checked_at", ""),
                    "origin": raw.get("origin", ""),
                    "destination": raw.get("destination", ""),
                    "depart_date": raw.get("depart_date", ""),
                    "return_date": raw.get("return_date", ""),
                    "days_out": to_int(raw.get("days_out")),
                    "fare_brand": brand,
                    "outbound_time": raw.get("outbound_time", ""),
                    "total_price": price,
                }
            )
    return rows


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

CHART_W = 560
CHART_H = 175
PAD_L = 58
PAD_R = 14
PAD_T = 16
PAD_B = 34

# Line style carries the brand as well as colour, so the chart still reads
# when printed, or to anyone who cannot separate the two hues.
BRAND_STYLE = {
    "any": ("Cheapest", "s-any", ""),
    "main": ("Main cabin", "s-main", "6 4"),
}


def build_chart(slots, series):
    """Inline SVG line chart.

    `slots` is the shared x axis: checked_at strings, oldest first. `series` is
    [(brand, {slot_index: price}), ...]. Brands are drawn with their own colour
    and dash pattern.
    """
    prices = [p for _, points in series for p in points.values()]
    if not prices:
        return '<p class="muted">No price history yet.</p>'

    low, high = min(prices), max(prices)
    left, right = PAD_L, CHART_W - PAD_R
    top, bottom = PAD_T, CHART_H - PAD_B
    span_x = right - left
    span_y = bottom - top
    middle = top + span_y / 2

    flat = high == low
    count = len(slots)

    def x_at(index):
        # A single slot has no span to divide by; put it in the middle.
        if count < 2:
            return left + span_x / 2
        return left + span_x * index / (count - 1)

    def y_at(price):
        # A zero range would divide by zero; draw it level instead.
        if flat:
            return middle
        return bottom - span_y * (price - low) / (high - low)

    drawn = ""
    for brand, points in series:
        if not points:
            continue
        _, cls, dash = BRAND_STYLE.get(brand, (brand, "s-any", ""))
        coords = [(x_at(i), y_at(points[i])) for i in sorted(points)]
        if len(coords) > 1:
            path = " ".join(f"{x:.1f},{y:.1f}" for x, y in coords)
            dash_attr = f' stroke-dasharray="{dash}"' if dash else ""
            drawn += f'<polyline points="{path}" class="line {cls}"{dash_attr} />'
        for x, y in coords:
            drawn += f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3" class="dot {cls}" />'

    # Both ends of the y axis get a dollar label, even when they are equal.
    labels = (
        f'<text x="{left - 8}" y="{top + 4}" class="axis end">{money(high)}</text>'
        f'<text x="{left - 8}" y="{bottom + 4}" class="axis end">{money(low)}</text>'
    )
    first = html.escape(pacific_short(slots[0]))
    last = html.escape(pacific_short(slots[-1]))
    if count < 2:
        labels += (
            f'<text x="{x_at(0):.1f}" y="{CHART_H - 12}" class="axis mid">'
            f"{first}</text>"
        )
    else:
        labels += (
            f'<text x="{left}" y="{CHART_H - 12}" class="axis">{first}</text>'
            f'<text x="{right}" y="{CHART_H - 12}" class="axis end">{last}</text>'
        )

    return (
        f'<svg class="chart" viewBox="0 0 {CHART_W} {CHART_H}" role="img" '
        f'aria-label="Total round-trip price over time, {money(low)} to {money(high)}">'
        f'<line x1="{left}" y1="{top}" x2="{right}" y2="{top}" class="grid" />'
        f'<line x1="{left}" y1="{bottom}" x2="{right}" y2="{bottom}" class="grid" />'
        f"{drawn}{labels}"
        "</svg>"
    )


def build_legend():
    """Swatches that repeat the line style, not just the colour."""
    parts = []
    for brand, _ in BRANDS:
        label, cls, dash = BRAND_STYLE[brand]
        dash_attr = f' stroke-dasharray="{dash}"' if dash else ""
        parts.append(
            '<span class="key">'
            f'<svg viewBox="0 0 28 10" class="swatch" aria-hidden="true">'
            f'<line x1="1" y1="5" x2="27" y2="5" class="line {cls}"{dash_attr} />'
            "</svg>"
            f"{html.escape(label)}</span>"
        )
    return '<p class="legend">' + "".join(parts) + "</p>"


def build_card(origin, destination, depart_date, return_date, rows):
    """One trip card: the latest check as a table, plus price history."""
    sub = html.escape(
        f"{trip_dates(depart_date, return_date)} - round trip - "
        f"{'Alaska' if AIRLINE == 'AS' else (AIRLINE or 'any airline')} - "
        f"{'nonstop' if MAX_STOPS == 0 else 'connections allowed'}"
    )
    head = [
        '<section class="card">',
        f'<h2>{origin} <span class="arrows">&#8596;</span> {destination}</h2>',
        f'<p class="sub">{sub}</p>',
    ]

    if not rows:
        return NL + NL.join(
            head
            + [
                '<p class="muted">No fares recorded for this trip yet.</p>',
                "</section>",
            ]
        ) + NL

    latest_stamp = max(row["checked_at"] for row in rows)
    latest = [row for row in rows if row["checked_at"] == latest_stamp]

    # Cheapest price per departure time, per brand, at the latest check.
    by_brand = {}
    for row in latest:
        slot = by_brand.setdefault(row["fare_brand"], {})
        time = row["outbound_time"]
        if time not in slot or row["total_price"] < slot[time]:
            slot[time] = row["total_price"]

    cheap = by_brand.get("any", {})
    main = by_brand.get("main", {})

    body = ""
    # Sorted by departure time, not price: the cheapest fare is often the one
    # that leaves too late to be useful, and that should be visible.
    for time in sorted(set(cheap) | set(main)):
        cheap_price = cheap.get(time)
        main_price = main.get(time)
        if cheap_price is None:
            cheap_cell = '<span class="muted">--</span>'
        else:
            cheap_cell = money(cheap_price)
        if main_price is None:
            main_cell = '<span class="muted">no Main fare</span>'
            extra_cell = '<span class="muted">--</span>'
        else:
            main_cell = money(main_price)
            # Identical brands means no cheap inventory left, which is $0
            # of premium, not a missing number.
            extra_cell = (
                money(main_price - cheap_price)
                if cheap_price is not None
                else '<span class="muted">--</span>'
            )
        body += (
            f"{NL}<tr><td>{html.escape(to_12h(time))}</td>"
            f"<td class='price'>{cheap_cell}</td>"
            f"<td class='price'>{main_cell}</td>"
            f"<td class='price'>{extra_cell}</td></tr>"
        )

    parts = head + [
        "<table><thead><tr>",
        "<th>Departure</th><th>Cheapest</th><th>Main cabin</th>"
        "<th>Extra to pick a seat</th>",
        "</tr></thead><tbody>",
        body.lstrip(NL),
        "</tbody></table>",
    ]

    if not main:
        parts.append(
            '<p class="muted">No Main cabin fare was recorded at the last '
            "check. That can mean none was offered, or that the check did "
            "not finish.</p>"
        )

    parts.append(
        f'<p class="sub">Checked {html.escape(pacific_stamp(latest_stamp))}. '
        "Departure times are local to the departure airport.</p>"
    )

    # Shared x axis across both brands, oldest first.
    slots = sorted({row["checked_at"] for row in rows})
    index = {stamp: i for i, stamp in enumerate(slots)}
    series = []
    for brand, _ in BRANDS:
        points = {}
        for row in rows:
            if row["fare_brand"] != brand:
                continue
            i = index[row["checked_at"]]
            if i not in points or row["total_price"] < points[i]:
                points[i] = row["total_price"]
        series.append((brand, points))

    parts += [
        "<h3>Cheapest total over time</h3>",
        build_chart(slots, series),
        build_legend(),
        "</section>",
    ]
    return NL + NL.join(parts) + NL


PAGE_CSS = """
:root {
  color-scheme: light dark;
  --bg: #f7f7f5;
  --card: #ffffff;
  --ink: #1d1d1b;
  --muted: #62625e;
  --rule: #e2e2dd;
  --c1: #2f6f4f;
  --c2: #5a53c0;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #17181a;
    --card: #202225;
    --ink: #e9e9e6;
    --muted: #9a9a95;
    --rule: #34363a;
    --c1: #74c49a;
    --c2: #a49cf0;
  }
}
* { box-sizing: border-box; }
html, body { max-width: 100%; overflow-x: hidden; }
body {
  margin: 0;
  padding: 24px 16px 48px;
  background: var(--bg);
  color: var(--ink);
  font: 16px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
        Helvetica, Arial, sans-serif;
  -webkit-text-size-adjust: 100%;
}
.wrap { max-width: 720px; margin: 0 auto; }
h1 { font-size: 1.4rem; font-weight: 600; margin: 0 0 4px; }
h2 { font-size: 1.1rem; font-weight: 600; margin: 0; }
h3 { font-size: .8rem; font-weight: 600; margin: 22px 0 6px;
     color: var(--muted); text-transform: uppercase; letter-spacing: .04em; }
.arrows { color: var(--muted); font-weight: 400; }
.sub, .muted { color: var(--muted); font-size: .85rem; margin: 2px 0 0; }
.lede { margin: 0 0 6px; }
.intro { margin: 0 0 22px; }
.card {
  background: var(--card);
  border: 1px solid var(--rule);
  border-radius: 10px;
  padding: 18px 16px;
  margin: 0 0 18px;
}
table { width: 100%; border-collapse: collapse; margin: 14px 0 10px; }
th, td { text-align: left; padding: 8px 4px; border-bottom: 1px solid var(--rule); }
th { font-size: .68rem; font-weight: 600; text-transform: uppercase;
     letter-spacing: .03em; color: var(--muted); vertical-align: bottom; }
th:not(:first-child), td:not(:first-child) { text-align: right; }
td { font-size: .95rem; }
tbody tr:last-child td { border-bottom: none; }
.price { font-variant-numeric: tabular-nums; white-space: nowrap; }
.chart { width: 100%; height: auto; display: block; }
.chart .line, .swatch .line { fill: none; stroke-width: 2; }
.s-any { stroke: var(--c1); }
.s-main { stroke: var(--c2); }
circle.s-any { fill: var(--c1); stroke: none; }
circle.s-main { fill: var(--c2); stroke: none; }
.chart .grid { stroke: var(--rule); stroke-width: 1; }
.chart .axis { fill: var(--muted); font-size: 11px; font-family: inherit; }
.chart .end { text-anchor: end; }
.chart .mid { text-anchor: middle; }
.legend { margin: 6px 0 0; font-size: .78rem; color: var(--muted); }
.key { display: inline-flex; align-items: center; margin-right: 14px; }
.swatch { width: 28px; height: 10px; margin-right: 6px; flex: none; }
.note { color: var(--muted); font-size: .8rem; margin-top: 28px; }
@media (max-width: 420px) {
  td, th { padding: 8px 2px; }
  td { font-size: .88rem; }
  th { font-size: .62rem; }
}
"""


def days_out_phrase(history):
    """'47 days until departure', from the most recent check."""
    if not history:
        return ""
    newest = max(row["checked_at"] for row in history)
    values = sorted(
        {
            row["days_out"]
            for row in history
            if row["checked_at"] == newest and row["days_out"] is not None
        }
    )
    if not values:
        return ""
    if len(values) == 1:
        count = values[0]
        if count < 0:
            return "Departure date has passed."
        if count == 0:
            return "Departing today."
        return f"{count} day{'s' if count != 1 else ''} until departure."
    return f"{values[0]} to {values[-1]} days until departure."


def render_page(history):
    """The full dashboard. An empty history is a normal case, not an error."""
    if history:
        newest = max(row["checked_at"] for row in history)
        updated = f"Last updated {html.escape(pacific_stamp(newest))}."
    else:
        updated = (
            "No prices have been recorded yet. "
            "The next scheduled run will fill this in."
        )

    groups = {}
    for row in history:
        key = (
            row["origin"],
            row["destination"],
            row["depart_date"],
            row["return_date"],
        )
        groups.setdefault(key, []).append(row)

    body = ""
    seen = set()
    # Configured trips first, in the order they appear in TRIPS.
    for origin, destination, depart_date, return_date in TRIPS:
        key = (origin, destination, depart_date, return_date)
        if key in seen:
            continue
        seen.add(key)
        body += build_card(
            origin, destination, depart_date, return_date, groups.get(key, [])
        )
    # Then anything still in the history that is no longer configured.
    for key in sorted(groups):
        if key not in seen:
            body += build_card(key[0], key[1], key[2], key[3], groups[key])

    days = days_out_phrase(history)
    days_line = f'<p class="sub lede">{html.escape(days)}</p>' if days else ""

    return (
        "<!doctype html>"
        f'{NL}<html lang="en">{NL}<head>{NL}<meta charset="utf-8">{NL}'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"{NL}<title>Flight prices</title>{NL}"
        f"<style>{PAGE_CSS}</style>{NL}"
        f"</head>{NL}<body>{NL}"
        f'<div class="wrap">{NL}'
        f"<h1>Flight prices</h1>{NL}"
        f"{days_line}{NL}"
        f'<p class="sub">{updated}</p>{NL}'
        '<p class="sub intro">Every price is the <strong>total round trip for '
        "one adult</strong>, not one leg and not per person beyond the first. "
        "The return time is chosen when you book: the search prices the "
        "cheapest available pairing rather than a fixed return flight, so only "
        f"the outbound departure is listed.</p>{NL}"
        f"{body}{NL}"
        '<p class="note">Base fare only, before bags or seat selection. The '
        "cheaper fare is Alaska's Saver basic economy, where your seat is "
        "assigned at check-in and you board last; the extra buys a seat you "
        "pick yourself and more room to change your mind. Checked and carry-on "
        f"bag allowance is the same either way.</p>{NL}"
        f"</div>{NL}</body>{NL}</html>{NL}"
    )


def write_dashboard():
    history = read_history()
    with open(DASHBOARD_HTML, "w", encoding="utf-8", newline=NL) as handle:
        handle.write(render_page(history))
    return len(history)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    # Once every tracked date has passed there is nothing left to price. Say so
    # and stop cleanly, rather than failing every run until TRIPS is edited.
    today = pacific_today()
    live = []
    for trip in TRIPS:
        days = [parse_day(trip[2]), parse_day(trip[3])]
        # A date we cannot read is not a date in the past.
        if any(day is None or day >= today for day in days):
            live.append(trip)

    if TRIPS and not live:
        print(
            "All tracked dates are in the past. Nothing to check. "
            "Edit TRIPS in track.py to track new dates."
        )
        return 0

    checked_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    planned = len(TRIPS) * len(BRANDS)
    print(f"Checking {len(TRIPS)} trip(s), {planned} queries, at {checked_at}")

    rows = []
    productive = 0
    failures = []

    for origin, destination, depart_date, return_date in TRIPS:
        depart = parse_day(depart_date)
        days_out = (depart - today).days if depart else ""
        print()
        print(
            f"{origin} <-> {destination}  {depart_date} / {return_date}"
            f"  ({days_out} days out)"
        )

        found = {}
        for brand, exclude in BRANDS:
            tag = f"  {brand:<4}"
            try:
                fares, unpriced, unexpected = fetch_trip(
                    origin, destination, depart_date, return_date, exclude
                )
            except FlightsNotFound as exc:
                failures.append(f"{origin}-{destination} {brand}")
                print(
                    f"{tag}  FAILED  FlightsNotFound: {exc}", file=sys.stderr
                )
                continue
            except Exception as exc:
                failures.append(f"{origin}-{destination} {brand}")
                print(
                    f"{tag}  FAILED  {type(exc).__name__}: {exc}",
                    file=sys.stderr,
                )
                continue

            notes = []
            if unpriced:
                notes.append(f"{unpriced} unpriced skipped")
            if unexpected:
                notes.append(f"{unexpected} unexpected shape skipped")
            suffix = f"  ({', '.join(notes)})" if notes else ""

            if not fares:
                print(f"{tag}  empty   no fares returned{suffix}")
                continue

            productive += 1
            found[brand] = fares
            print(
                f"{tag}  ok      {len(fares)} fare(s), "
                f"cheapest {money(min(fares.values()))}{suffix}"
            )
            for time in sorted(fares):
                rows.append(
                    [
                        checked_at,
                        origin,
                        destination,
                        depart_date,
                        return_date,
                        days_out,
                        brand,
                        time,
                        fares[time],
                    ]
                )

        cheap = found.get("any", {})
        main_fares = found.get("main", {})
        if cheap or main_fares:
            print(f"    {'departs':<10}{'cheapest':>10}{'main':>10}{'extra':>10}")
            for time in sorted(set(cheap) | set(main_fares)):
                a = cheap.get(time)
                m = main_fares.get(time)
                extra = money(m - a) if (a is not None and m is not None) else "--"
                print(
                    f"    {to_12h(time):<10}{money(a) if a else '--':>10}"
                    f"{money(m) if m else 'none':>10}{extra:>10}"
                )

    if productive == 0:
        print(
            f"{NL}All {planned} queries failed to produce a fare. Leaving "
            f"{HISTORY_CSV} and {DASHBOARD_HTML} untouched.",
            file=sys.stderr,
        )
        return 1

    append_history(rows)
    total = write_dashboard()
    if failures:
        # Partial success still exits 0, but the run must not look clean.
        print(
            f"WARNING: {len(failures)} of {planned} queries failed "
            f"({', '.join(failures)}). The page cannot tell a fare that was "
            "not offered from one that was never checked.",
            file=sys.stderr,
        )
    print(
        f"{NL}{productive} of {planned} queries produced fares. "
        f"Wrote {len(rows)} row(s) to {HISTORY_CSV} ({total} total). "
        f"Rebuilt {DASHBOARD_HTML}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
