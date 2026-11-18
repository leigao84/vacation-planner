# Vacation Planner

A lightweight, self-hosted web app for planning trips *before* booking them:
jot down flights, hotels, cars, activities and food, build several **plans**
(hypotheticals) for the same trip, and compare what each version really costs.

Built for a home server — a single Flask + SQLite container, no build step,
no external services. Sibling project to
[kids_spending_money](https://github.com/leigao84/kids_spending_money) (the
allowance ledger), using the same pattern.

## Features

- **Trips in a left sidebar** — every trip with its dates, plan count and
  lowest plan total; the sidebar stacks on top on phones.
- **Plans per trip** — e.g. "Booked plan", "Splurge version", "Budget
  version". Duplicate a plan to start a what-if from the real thing.
- **Items** — category (flight / hotel / car / activity / food / other),
  cost × quantity, dates, link, notes, and an **idea vs booked** status.
- **Plan summary** — total, per-person and per-day costs, a category donut
  with percentages, and a booked-vs-hypothetical progress bar.
- **Calendar view** — month grids with the trip's days shaded and items on
  the days they cover, a day-by-day agenda, and a "No date yet" list.
- **Compare view** — all of a trip's plans side by side: per-category rows,
  booked vs hypothetical, per-person / per-day, and a LOWEST badge on the
  cheapest plan.
- **Company logos** — items can show the real logo of the company named in
  their title (Alaska Airlines, Hampton by Hilton, Avis, …), matched from a
  small registry in `app.py`. Logo files are **not** included in this repo
  (they are trademarks of their owners); drop your own PNGs into
  `static/logos/` and matching items light up — anything without a logo
  file falls back to a colored category tile.

## Running it

```sh
docker compose up -d --build
```

Then open http://<host>:8089. Data is a single SQLite file in `./data/`
(mounted into the container at `/data/planner.db`) — back it up by copying
that file.

To run without Docker:

```sh
pip install flask
PLANNER_DB=./data/planner.db python app.py   # serves on port 8089
```

## Adding a logo

Drop a PNG named after the brand into `static/logos/` and add an entry to
the `BRANDS` list in `app.py` (keywords matched against item titles,
specific brands before their parent companies — e.g. Fairfield before
Marriott). Logos are only used if the file exists, so a missing file is
never an error — the item simply shows its colored category tile instead.
For personal use, brand logos can be sourced from Wikimedia Commons.

## Notes

- No accounts or passwords — it is meant to run on a trusted home LAN /
  tailnet, like the other apps on the box.
- Brand logos are trademarks of their owners, included for personal,
  private use only.
