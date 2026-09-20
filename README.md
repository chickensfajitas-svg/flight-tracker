# Flight price tracker

Checks a few Alaska Airlines nonstop fares three times a day and builds a
plain web page showing the flights and what they cost. Everything runs on
GitHub Actions. There is no API key, no account, and nothing to install.

Two files are produced and committed back to the repository on every run:

- `history.csv` — one row per flight per check, appended forever
- `dashboard.html` — the page, rebuilt from the whole of `history.csv`

The page shows one card per route and date. Each card lists the flights from
the most recent check with their departure time, arrival time, duration and
price, marks the cheapest one, and draws a small chart of the cheapest fare
over time.

## Setup

1. **You should not need to change workflow permissions.** The workflow asks
   for write access itself, in its own `permissions:` block, so it can push
   `history.csv` and `dashboard.html` back even though a new repository
   defaults the token to read-only. This is verified: a run on a repository
   with the default setting reported `Contents: write` and pushed normally.

   If a push ever does fail with a permissions error, which can happen when
   an organization policy forces read-only tokens, go to **Settings ->
   Actions -> General -> Workflow permissions**, select **Read and write
   permissions**, and save.

2. **Turn on GitHub Pages** so you can send someone a link. Go to
   **Settings -> Pages**, set **Source** to **Deploy from a branch**, pick
   your default branch and the `/ (root)` folder, and save. After a minute
   the page is at:

       https://YOUR-USERNAME.github.io/YOUR-REPO/dashboard.html

   That link is public to anyone who has it. The repository itself can stay
   private or public; Pages on a private repository requires a paid plan.

3. **Run it once by hand** so there is something to look at. See below.

## Running it manually

Go to the **Actions** tab, pick **Track flight prices** in the sidebar, then
click **Run workflow**. It takes under a minute.

To run it on your own machine instead:

    pip install -r requirements.txt
    python track.py

It writes `history.csv` and `dashboard.html` into the current folder and
prints what it found. Open `dashboard.html` in any browser; it is a single
self-contained file and works offline.

## Changing what is tracked

Everything you would want to change is at the top of `track.py`, under
`# Config. Edit these.`

`ROUTES` is the list of things to check. Each entry is an origin airport, a
destination airport, and a departure date:

    ROUTES = [
        ("BUR", "SEA", "2026-11-06"),
        ("BUR", "SEA", "2026-11-07"),
        ("LAX", "SEA", "2026-11-06"),
        ("LAX", "SEA", "2026-11-07"),
    ]

Add, remove or edit lines freely. Airports are IATA codes and dates are
`YYYY-MM-DD`. The list can be any length.

Below it:

| Constant | Meaning |
| --- | --- |
| `AIRLINES` | IATA airline codes to allow, e.g. `["AS"]` for Alaska. Filtering happens at the source, not afterwards. |
| `MAX_STOPS` | `0` means nonstop only. |
| `SEAT` | `"economy"`, `"premium-economy"`, `"business"` or `"first"`. |
| `ADULTS` | Number of adult passengers. |
| `CURRENCY` | `"USD"`. |
| `LANGUAGE` | `"en-US"`. |

Old rows in `history.csv` are never deleted. If you remove a route from
`ROUTES`, its card keeps appearing on the page, after the configured ones,
so past data is not silently hidden. Delete its rows from `history.csv` if
you want it gone.

To change how often it checks, edit the `cron:` lines in
`.github/workflows/track.yml`. They are in UTC, with the Pacific equivalent
in a comment beside each one.

## Known limits

- **Base fare only.** The price is the headline economy fare. Bags, seat
  selection, and any other add-on are not included, so what you actually pay
  at checkout will usually be higher.
- **Nonstop only.** Connecting itineraries are not tracked. If a route has no
  nonstop that day, the card will be empty even though flights with a
  connection exist.
- **Outbound only.** These are one-way searches. No return leg is priced, and
  a round trip booked together is often cheaper than two one-ways.
- **One airline.** Only carriers listed in `AIRLINES` are searched, so this
  is not a comparison against other airlines.
- **Times are airport local.** A departure time is local to the origin
  airport and an arrival time is local to the destination. They are shown
  exactly as the source reports them, with no conversion. The duration comes
  from the source too, so it is correct even across a time zone change.
- **It stops on its own once the trip has passed.** When every date in
  `ROUTES` is earlier than today in Pacific time, the run prints that there is
  nothing left to check and exits successfully without fetching anything or
  touching either file. The Action stays green instead of failing on every run
  forever. Edit `ROUTES` to start tracking new dates. A single date still in
  the future is enough to keep it working normally.
- **Prices move constantly.** A fare captured three times a day is a
  snapshot, not a guarantee. It can change between the check and your
  booking.
- **It reads Google Flights unofficially.** `fast-flights` is not an official
  API. It reads the Google Flights page and pulls the data out of it. When
  Google changes that page, it breaks, usually all at once and on every
  route. The run will go red rather than record nothing quietly.

  If that happens, the first thing to try is:

      pip install -U fast-flights

  and, if a newer version fixes it, commit the change. If there is no fix
  yet, there is nothing to do but wait for the library to catch up.

- **`requirements.txt` has two entries beyond `fast-flights`.**
  `typing_extensions` is there because `fast-flights` 3.1.0 imports it without
  declaring it as a dependency, so pip will not install it on its own and the
  run fails at import; remove that line once upstream fixes its packaging.
  `tzdata` is there because Python's `ZoneInfo` has no time zone database of
  its own on Windows, which is what the Pacific timestamps need when you run
  `track.py` locally. On the Linux runners `tzdata` is a harmless no-op.

## What a failed run means

A run goes red only if *every* route failed to produce a fare. If even one
route worked, its rows are saved and the run is green; the routes that failed
are named in the log, with the reason next to each. Nothing is written when
all four fail, so the last good page stays up.
