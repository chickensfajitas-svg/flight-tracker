"""Alaska Airlines nonstop fare tracker.

Checks a handful of one-way routes, appends every fare it sees to history.csv,
and rebuilds dashboard.html from that history. No API keys, no database,
no notifications.
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

# ---------------------------------------------------------------------------
# Config. Edit these.
# ---------------------------------------------------------------------------

# (origin, destination, departure date as YYYY-MM-DD)
ROUTES = [
    ("BUR", "SEA", "2026-11-06"),
    ("BUR", "SEA", "2026-11-07"),
    ("LAX", "SEA", "2026-11-06"),
    ("LAX", "SEA", "2026-11-07"),
]

AIRLINES = ["AS"]   # IATA codes. Filters at the source, not after the fact.
MAX_STOPS = 0       # 0 = nonstop only
SEAT = "economy"
ADULTS = 1
CURRENCY = "USD"
LANGUAGE = "en-US"

HISTORY_CSV = "history.csv"
DASHBOARD_HTML = "dashboard.html"

# ---------------------------------------------------------------------------

CSV_HEADER = [
    "checked_at",
    "origin",
    "destination",
    "depart_date",
    "depart_time",
    "arrive_time",
    "duration_min",
    "price",
]


# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------

PACIFIC = ZoneInfo("America/Los_Angeles")


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


def pacific_stamp(iso_utc, with_year=True):
    """A quiet, human date: 'Sep 20, 2026 at 11:03 AM PDT'."""
    moment = parse_iso(iso_utc)
    if moment is None:
        return "an unknown time"
    local, label = to_pacific(moment)
    hour = local.hour % 12 or 12
    meridiem = "AM" if local.hour < 12 else "PM"
    year = f", {local.year}" if with_year else ""
    return (
        f"{local.strftime('%b')} {local.day}{year} at "
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


def format_duration(minutes):
    """140 -> '2h 20m'."""
    try:
        total = int(minutes)
    except (TypeError, ValueError):
        return "--"
    if total <= 0:
        return "--"
    hours, rest = divmod(total, 60)
    return f"{hours}h {rest:02d}m" if hours else f"{rest}m"


def format_route_date(value):
    """'2026-11-06' -> 'Fri, Nov 6, 2026'."""
    try:
        day = datetime.strptime(value, "%Y-%m-%d")
    except (TypeError, ValueError):
        return str(value)
    return f"{day.strftime('%a')}, {day.strftime('%b')} {day.day}, {day.year}"


def to_int(value):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Fetching
# ---------------------------------------------------------------------------

def fetch_route(origin, destination, date):
    """Return (itineraries, unpriced_skipped, connecting_skipped).

    Raises on anything that goes wrong; the caller decides what that means.
    """
    query = create_query(
        flights=[
            FlightQuery(
                date=date,
                from_airport=origin,
                to_airport=destination,
                airlines=AIRLINES,
                max_stops=MAX_STOPS,
            )
        ],
        trip="one-way",
        seat=SEAT,
        passengers=Passengers(adults=ADULTS),
        currency=CURRENCY,
        language=LANGUAGE,
    )
    results = get_flights(query)

    itineraries = []
    unpriced = 0
    connecting = 0

    for result in results:
        price = result.price
        # Google hands back $0 placeholders for itineraries it could not
        # price. Those are not fares.
        if isinstance(price, bool) or not isinstance(price, int) or price <= 0:
            unpriced += 1
            continue

        segments = list(result.flights or ())
        if len(segments) != 1:
            # max_stops=0 should make this impossible. If Google ignores the
            # filter, refuse to record a connection as though it were nonstop.
            connecting += 1
            continue

        leg = segments[0]
        duration = leg.duration
        if isinstance(duration, bool) or not isinstance(duration, int) or duration <= 0:
            duration = ""

        itineraries.append(
            {
                "depart_time": hhmm(leg.departure.time),
                "arrive_time": hhmm(leg.arrival.time),
                "duration_min": duration,
                "price": price,
            }
        )

    return itineraries, unpriced, connecting


# ---------------------------------------------------------------------------
# History
# ---------------------------------------------------------------------------

def append_history(rows):
    """Append rows, writing the header only when the file is new or empty."""
    has_content = os.path.exists(HISTORY_CSV) and os.path.getsize(HISTORY_CSV) > 0
    with open(HISTORY_CSV, "a", newline="", encoding="utf-8") as handle:
        # csv defaults to CRLF. Pin it to LF so the file reads the same
        # whether a run happened here or on the Linux runner.
        writer = csv.writer(handle, lineterminator=chr(10))
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
            price = to_int(raw.get("price"))
            if price is None or price <= 0:
                continue
            if not raw.get("checked_at"):
                continue
            rows.append(
                {
                    "checked_at": raw.get("checked_at", ""),
                    "origin": raw.get("origin", ""),
                    "destination": raw.get("destination", ""),
                    "depart_date": raw.get("depart_date", ""),
                    "depart_time": raw.get("depart_time", ""),
                    "arrive_time": raw.get("arrive_time", ""),
                    "duration_min": raw.get("duration_min", ""),
                    "price": price,
                }
            )
    return rows


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

CHART_W = 560
CHART_H = 170
PAD_L = 58
PAD_R = 14
PAD_T = 16
PAD_B = 34


def build_chart(points):
    """Inline SVG line chart. `points` is [(checked_at, cheapest_price), ...]."""
    if not points:
        return '<p class="muted">No price history yet.</p>'

    prices = [price for _, price in points]
    low, high = min(prices), max(prices)
    left, right = PAD_L, CHART_W - PAD_R
    top, bottom = PAD_T, CHART_H - PAD_B
    span_x = right - left
    span_y = bottom - top
    middle = top + span_y / 2

    flat = high == low
    count = len(points)

    def x_at(index):
        # A single point has no span to divide by; put it in the middle.
        if count == 1:
            return left + span_x / 2
        return left + span_x * index / (count - 1)

    def y_at(price):
        # A zero range would divide by zero; draw it level instead.
        if flat:
            return middle
        return bottom - span_y * (price - low) / (high - low)

    coords = [(x_at(i), y_at(price)) for i, (_, price) in enumerate(points)]
    dots = "".join(
        f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3" class="dot" />' for x, y in coords
    )
    line = ""
    if count > 1:
        path = " ".join(f"{x:.1f},{y:.1f}" for x, y in coords)
        line = f'<polyline points="{path}" class="line" />'

    # Both ends of the y axis get a dollar label, even when they are equal.
    y_labels = (
        f'<text x="{left - 8}" y="{top + 4}" class="axis end">${high:,}</text>'
        f'<text x="{left - 8}" y="{bottom + 4}" class="axis end">${low:,}</text>'
    )

    first_label = html.escape(pacific_short(points[0][0]))
    last_label = html.escape(pacific_short(points[-1][0]))
    if count == 1:
        x_labels = (
            f'<text x="{x_at(0):.1f}" y="{CHART_H - 12}" class="axis mid">'
            f"{first_label}</text>"
        )
    else:
        x_labels = (
            f'<text x="{left}" y="{CHART_H - 12}" class="axis">{first_label}</text>'
            f'<text x="{right}" y="{CHART_H - 12}" class="axis end">{last_label}</text>'
        )

    return (
        f'<svg class="chart" viewBox="0 0 {CHART_W} {CHART_H}" role="img" '
        f'aria-label="Cheapest fare over time, ${low:,} to ${high:,}">'
        f'<line x1="{left}" y1="{top}" x2="{right}" y2="{top}" class="grid" />'
        f'<line x1="{left}" y1="{bottom}" x2="{right}" y2="{bottom}" class="grid" />'
        f"{line}{dots}{y_labels}{x_labels}"
        "</svg>"
    )


NL = chr(10)


def build_card(origin, destination, depart_date, rows):
    """One route/date card: the latest flight table plus a price chart.

    Each element goes on its own line so a single fare change shows up as a
    one-line diff rather than rewriting the whole file.
    """
    title = html.escape(f"{origin} to {destination}")
    subtitle = html.escape(format_route_date(depart_date))

    head = [
        '<section class="card">',
        f"<h2>{title}</h2>",
        f'<p class="sub">{subtitle}</p>',
    ]

    if not rows:
        parts = head + [
            '<p class="muted">No fares recorded for this route yet.</p>',
            "</section>",
        ]
        return NL + NL.join(parts) + NL

    latest_stamp = max(row["checked_at"] for row in rows)
    latest = sorted(
        (row for row in rows if row["checked_at"] == latest_stamp),
        key=lambda row: row["depart_time"],
    )
    cheapest = min(row["price"] for row in latest)

    parts = head + [
        "<table><thead><tr>",
        "<th>Departs</th><th>Arrives</th><th>Duration</th><th>Price</th>",
        "</tr></thead><tbody>",
    ]

    for row in latest:
        best = row["price"] == cheapest
        cls = ' class="best"' if best else ""
        tag = ' <span class="tag">cheapest</span>' if best else ""
        parts.append(
            f"<tr{cls}>"
            f'<td>{html.escape(to_12h(row["depart_time"]))}</td>'
            f'<td>{html.escape(to_12h(row["arrive_time"]))}</td>'
            f'<td>{html.escape(format_duration(row["duration_min"]))}</td>'
            f'<td class="price">${row["price"]:,}{tag}</td>'
            "</tr>"
        )

    # Cheapest fare per check, oldest first.
    by_check = {}
    for row in rows:
        stamp = row["checked_at"]
        if stamp not in by_check or row["price"] < by_check[stamp]:
            by_check[stamp] = row["price"]
    points = sorted(by_check.items())

    parts += [
        "</tbody></table>",
        f'<p class="sub">Checked {html.escape(pacific_stamp(latest_stamp))}. '
        "Times are local to each airport.</p>",
        "<h3>Cheapest fare over time</h3>",
        build_chart(points),
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
  --best: #f1f6ed;
  --accent: #3f6f3f;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #17181a;
    --card: #202225;
    --ink: #e9e9e6;
    --muted: #9a9a95;
    --rule: #34363a;
    --best: #22301e;
    --accent: #8fbf80;
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
.sub, .muted { color: var(--muted); font-size: .85rem; margin: 2px 0 0; }
.updated { margin: 0 0 24px; }
.card {
  background: var(--card);
  border: 1px solid var(--rule);
  border-radius: 10px;
  padding: 18px 16px;
  margin: 0 0 18px;
}
table { width: 100%; border-collapse: collapse; margin: 14px 0 10px; }
th, td { text-align: left; padding: 8px 4px; border-bottom: 1px solid var(--rule); }
th { font-size: .7rem; font-weight: 600; text-transform: uppercase;
     letter-spacing: .04em; color: var(--muted); }
th:last-child, td:last-child { text-align: right; }
td { font-size: .95rem; }
tbody tr:last-child td { border-bottom: none; }
tr.best td { background: var(--best); }
.price { font-variant-numeric: tabular-nums; white-space: nowrap; }
.tag {
  display: inline-block; margin-left: 6px; padding: 1px 6px;
  border-radius: 999px; background: var(--accent); color: var(--card);
  font-size: .6rem; text-transform: uppercase; letter-spacing: .04em;
  vertical-align: 1px;
}
.chart { width: 100%; height: auto; display: block; }
.chart .line { fill: none; stroke: var(--accent); stroke-width: 2; }
.chart .dot { fill: var(--accent); }
.chart .grid { stroke: var(--rule); stroke-width: 1; }
.chart .axis { fill: var(--muted); font-size: 11px; font-family: inherit; }
.chart .end { text-anchor: end; }
.chart .mid { text-anchor: middle; }
.note { color: var(--muted); font-size: .8rem; margin-top: 28px; }
@media (max-width: 420px) {
  td, th { padding: 8px 2px; }
  td { font-size: .9rem; }
}
"""


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
        key = (row["origin"], row["destination"], row["depart_date"])
        groups.setdefault(key, []).append(row)

    body = ""
    seen = set()
    # Configured routes first, in the order they appear in ROUTES.
    for origin, destination, depart_date in ROUTES:
        key = (origin, destination, depart_date)
        if key in seen:
            continue
        seen.add(key)
        body += build_card(origin, destination, depart_date, groups.get(key, []))
    # Then anything still in the history that is no longer configured.
    for key in sorted(groups):
        if key not in seen:
            body += build_card(key[0], key[1], key[2], groups[key])

    return (
        "<!doctype html>\n"
        '<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        "<title>Flight prices</title>\n"
        f"<style>{PAGE_CSS}</style>\n"
        "</head>\n<body>\n"
        '<div class="wrap">\n'
        "<h1>Flight prices</h1>\n"
        f'<p class="sub updated">{updated}</p>\n'
        f"{body}\n"
        '<p class="note">One adult, one way, economy, nonstop on Alaska '
        "Airlines. Base fare only, before bags or seat selection. Departure "
        "and arrival times are local to each airport.</p>\n"
        "</div>\n</body>\n</html>\n"
    )


def write_dashboard():
    history = read_history()
    with open(DASHBOARD_HTML, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(render_page(history))
    return len(history)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    # Once every tracked date has passed there is nothing left to price. Say so
    # and stop cleanly, rather than failing every run until ROUTES is edited.
    today = pacific_today()
    upcoming = []
    for route in ROUTES:
        try:
            day = datetime.strptime(route[2], "%Y-%m-%d").date()
        except (TypeError, ValueError, IndexError):
            # A date we cannot read is not a date in the past. Let the fetch
            # try it and report whatever goes wrong.
            upcoming.append(route)
            continue
        if day >= today:
            upcoming.append(route)

    if ROUTES and not upcoming:
        print(
            "All tracked dates are in the past. Nothing to check. "
            "Edit ROUTES in track.py to track new dates."
        )
        return 0

    checked_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"Checking {len(ROUTES)} route(s) at {checked_at}\n")

    rows = []
    productive = 0

    for origin, destination, depart_date in ROUTES:
        label = f"{origin} -> {destination} on {depart_date}"
        try:
            itineraries, unpriced, connecting = fetch_route(
                origin, destination, depart_date
            )
        except FlightsNotFound as exc:
            print(f"{label}: no flights found -- FlightsNotFound: {exc}")
            continue
        except Exception as exc:
            print(f"{label}: failed -- {type(exc).__name__}: {exc}")
            continue

        notes = []
        if unpriced:
            notes.append(f"{unpriced} unpriced skipped")
        if connecting:
            notes.append(f"{connecting} connecting skipped")
        suffix = f"  ({', '.join(notes)})" if notes else ""

        if not itineraries:
            print(f"{label}: nothing usable returned{suffix}")
            continue

        productive += 1
        cheapest = min(item["price"] for item in itineraries)
        print(f"{label}: {len(itineraries)} fare(s), cheapest ${cheapest:,}{suffix}")
        for item in sorted(itineraries, key=lambda i: i["depart_time"]):
            print(
                f"    {to_12h(item['depart_time']):>8} -> "
                f"{to_12h(item['arrive_time']):>8}  "
                f"{format_duration(item['duration_min']):>7}  "
                f"${item['price']:,}"
            )
            rows.append(
                [
                    checked_at,
                    origin,
                    destination,
                    depart_date,
                    item["depart_time"],
                    item["arrive_time"],
                    item["duration_min"],
                    item["price"],
                ]
            )

    if productive == 0:
        print(
            f"\nNo route produced a fare. Leaving {HISTORY_CSV} and "
            f"{DASHBOARD_HTML} untouched.",
            file=sys.stderr,
        )
        return 1

    append_history(rows)
    total = write_dashboard()
    print(
        f"\nWrote {len(rows)} row(s) to {HISTORY_CSV} ({total} total). "
        f"Rebuilt {DASHBOARD_HTML}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
