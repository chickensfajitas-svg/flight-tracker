# Flight price tracker

Checks a few Alaska Airlines nonstop round trip fares three times a day and
builds a plain web page showing what they cost. Everything runs on GitHub
Actions. There is no API key, no account, and nothing to install.

The page is here:

    https://chickensfajitas-svg.github.io/flight-tracker/dashboard.html

The repository is here:

    https://github.com/chickensfajitas-svg/flight-tracker

Two files are produced and committed back to the repository on every run:

- `history.csv` — one row per check, appended forever
- `dashboard.html` — the page, rebuilt from the whole of `history.csv`

The page is a single self-contained file. It works offline and can be opened
in any browser.

## The two prices

Every trip is looked up twice, so each one has two prices on the page.

The first is the cheapest fare of any kind. On Alaska that is usually
**Saver**, their basic economy.

The second is the cheapest **Main** cabin fare, found by asking again with
Saver excluded.

As of 2026-09-20 the gap is a flat **$80 per round trip per adult**.

What the $80 buys:

- **A seat you pick.** With Main you choose your seat when you book. With
  Saver a seat is assigned to you at check-in, and you take what you get.
- **Normal boarding.** Saver boards last, in Group F.
- **Some flexibility.** A Main ticket can be changed. A Saver ticket cannot;
  cancelling gets you 50% back as credit, and only if you cancel 14 or more
  days before the flight.

**Bags are the same either way.** Checked bag allowance and carry-on
allowance do not change between Saver and Main. The $80 is not a bag fee.
People assume it is, and it is not.

Main is not always sold. Some departures have a cheapest fare but no Main
fare at all. Those rows say **no Main fare** rather than disappearing from
the page.

## Setup

1. **You should not need to change workflow permissions.** The workflow asks
   for write access itself, in its own `permissions: contents: write` block,
   so it can push `history.csv` and `dashboard.html` back even though a new
   repository defaults the token to read-only. This is verified: a run on a
   repository with the default setting reported `Contents: write` and pushed
   normally.

   If a push ever does fail with a permissions error, which can happen when
   an organization policy forces read-only tokens, go to **Settings ->
   Actions -> General -> Workflow permissions**, select **Read and write
   permissions**, and save. That is a fallback, not a normal step.

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
prints what it found.

## Changing what is tracked

Everything you would want to change is in one block at the top of `track.py`:

    # ---- EDIT THIS ----
    AIRLINE   = "AS"     # IATA code, or "" for all airlines
    MAX_STOPS = 0        # 0 = nonstop only, None = allow connections
    ADULTS    = 1
    TRIPS = [
        ("BUR", "SEA", "2026-11-06", "2026-11-07"),
        ("LAX", "SEA", "2026-11-06", "2026-11-07"),
    ]   # (home airport, destination, depart date, return date)
    # ---- END EDIT ----

One line per trip. Each line is the home airport, the destination, the date
you leave, and the date you come back. Airports are IATA codes and dates are
`YYYY-MM-DD`. Add, remove or edit lines freely; the list can be any length.

`AIRLINE` is one IATA code, `"AS"` for Alaska. Set it to `""` to search every
airline. `MAX_STOPS` is `0` for nonstop only, or `None` to allow connections.
`ADULTS` is how many people are travelling; prices are per adult either way.

Changing airports, dates, the airline or the number of passengers happens
only here. Nothing else in the file needs touching.

Old rows in `history.csv` are never deleted. If you remove a trip from
`TRIPS`, its card keeps appearing on the page, after the configured ones, so
past data is not silently hidden. Delete its rows from `history.csv` if you
want it gone.

To change how often it checks, edit the `cron:` lines in
`.github/workflows/track.yml`. They are in UTC, with the Pacific equivalent
in a comment beside each one.

## What is in history.csv

    checked_at,origin,destination,depart_date,return_date,days_out,fare_brand,outbound_time,total_price

| Column | Meaning |
| --- | --- |
| `checked_at` | When the check ran. UTC, ISO 8601, to the second. |
| `origin` | Home airport. |
| `destination` | Where you are going. |
| `depart_date` | Date of the outbound flight. |
| `return_date` | Date of the return flight. |
| `days_out` | Days from now until `depart_date`, counted in Pacific time. |
| `fare_brand` | `any` for the cheapest fare of any kind, `main` for the cheapest Main cabin fare. |
| `outbound_time` | Departure time of the outbound flight, local to the origin airport. |
| `total_price` | The whole round trip for one adult. Not one leg, and not the party total. |

## Why there is no return time

A round trip search gives back only the **outbound** flights. The return leg
is not in the answer at all. The price attached to each outbound flight is
the total for the cheapest return that can be paired with it, but which
return that is only becomes fixed when you sit down and book.

So the tool records the outbound time, which it was told, and leaves the
return time alone. It does not guess one. When you go to book, the return
flight is still yours to choose, and picking a different one may change the
price.

## Known limits

- **Prices are the total round trip, per adult, base fare only.** Bags, seat
  selection and any other add-on are on top, so what you pay at checkout will
  usually be higher.
- **Nonstop only.** Connecting itineraries are not tracked. If a trip has no
  nonstop, its card will be empty even though flights with a connection
  exist.
- **The return time is not pinned.** Only the outbound flight is recorded.
  See *Why there is no return time* above.
- **Times are airport local.** A departure time is local to the airport it
  leaves from. Times are shown exactly as reported, with no conversion.
- **One airline.** With `AIRLINE` set to `"AS"` only Alaska is searched, so
  this is not a comparison against other airlines.
- **Prices move constantly.** A fare captured three times a day is a
  snapshot, not a guarantee. It can change between the check and your
  booking.
- **It reads Google Flights unofficially.** `fast-flights` is not an official
  API. It reads the Google Flights page and pulls the data out of it. When
  Google changes that page, it breaks, and it breaks on every route at once.

  If that happens, the first thing to try is:

      pip install -U fast-flights

  and, if a newer version fixes it, commit the change. If there is no fix
  yet, there is nothing to do but wait for the library to catch up.

- **`requirements.txt` has two entries beyond `fast-flights`.**
  `typing_extensions` is there because `fast-flights` 3.1.0 imports it without
  declaring it as a dependency, so pip will not install it on its own and the
  run fails at import; remove that line once upstream fixes its packaging.
  `tzdata` is there because Python's `ZoneInfo` has no time zone database of
  its own on Windows, which is what the Pacific dates need when you run
  `track.py` locally. On the Linux runners `tzdata` is a harmless no-op.
- **It stops on its own once the trip has passed.** After 2026-11-07 every
  tracked date is in the past. The run then prints that there is nothing to
  check and exits successfully, without fetching anything or writing either
  file. The Action goes quiet rather than red. Editing `TRIPS` with new dates
  starts it again.

## What a failed run means

A run goes red only if *every* trip failed to produce a fare. If even one
worked, its rows are saved and the run is green; the ones that failed are
named in the log, with the reason next to each. Nothing is written when they
all fail, so the last good page stays up.
