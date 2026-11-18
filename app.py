#!/usr/bin/env python3
"""Vacation Planner - trips, plans (hypotheticals), and costed items.

Single-file Flask + SQLite app in the same pattern as the Allowance Ledger.
Trips live in a left sidebar; each trip holds one or more plans; each plan
holds items (flights, hotels, cars, activities, food, other) with costs,
dates and a booked/idea status. Plans can be duplicated and compared.
"""
import os
import sqlite3
from datetime import date, timedelta

from flask import Flask, g, redirect, render_template_string, request, url_for
from markupsafe import Markup

APP_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("PLANNER_DB", "/data/planner.db")
STATIC_LOGOS = os.path.join(APP_DIR, "static", "logos")

app = Flask(__name__)

CATEGORIES = {
    "flight":   {"label": "Flights",    "color": "#2563eb"},
    "hotel":    {"label": "Hotels",     "color": "#7c3aed"},
    "car":      {"label": "Car",        "color": "#ea580c"},
    "train":    {"label": "Train",      "color": "#0f766e"},
    "bus":      {"label": "Bus",        "color": "#ca8a04"},
    "activity": {"label": "Activities", "color": "#16a34a"},
    "food":     {"label": "Food",       "color": "#db2777"},
    "other":    {"label": "Other",      "color": "#64748b"},
}

# Brand matching: (keywords, logo file). Specific brands come before their
# parent companies so "Fairfield" wins over "Marriott". A match only counts
# when the logo file actually exists in static/logos/.
BRANDS = [
    (["fairfield"], "fairfield.png"),
    (["hampton"], "hampton.png"),
    (["alaska"], "alaska.png"),
    (["southwest"], "southwest.png"),
    (["delta"], "delta.png"),
    (["united"], "united.png"),
    (["american"], "american.png"),
    (["avis"], "avis.png"),
    (["hertz"], "hertz.png"),
    (["enterprise"], "enterprise.png"),
    (["universal"], "universal.png"),
    (["hyatt"], "hyatt.png"),
    (["marriott"], "marriott.png"),
    (["hilton"], "hilton.png"),
]


def logo_for(title):
    t = (title or "").lower()
    for keywords, fname in BRANDS:
        if any(k in t for k in keywords):
            if os.path.exists(os.path.join(STATIC_LOGOS, fname)):
                return fname
    return None


# ---------------------------------------------------------------- database

def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    db = sqlite3.connect(DB_PATH)
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS trips (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            destination TEXT DEFAULT '',
            start_date TEXT DEFAULT '',
            end_date TEXT DEFAULT '',
            travelers INTEGER DEFAULT 1,
            notes TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS plans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trip_id INTEGER NOT NULL REFERENCES trips(id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            notes TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            plan_id INTEGER NOT NULL REFERENCES plans(id) ON DELETE CASCADE,
            category TEXT NOT NULL DEFAULT 'other',
            title TEXT NOT NULL,
            cost_cents INTEGER NOT NULL DEFAULT 0,
            quantity INTEGER NOT NULL DEFAULT 1,
            status TEXT NOT NULL DEFAULT 'idea',
            start_date TEXT DEFAULT '',
            end_date TEXT DEFAULT '',
            link TEXT DEFAULT '',
            notes TEXT DEFAULT ''
        );
        """
    )
    db.commit()
    db.close()


# ---------------------------------------------------------------- helpers

def fmt_money(cents):
    return "${:,.2f}".format(cents / 100.0)


def parse_money(text):
    text = (text or "").replace("$", "").replace(",", "").strip()
    if not text:
        return 0
    return int(round(float(text) * 100))


def dparse(s):
    if not s:
        return None
    try:
        return date.fromisoformat(s)
    except ValueError:
        return None


def item_total(item):
    return item["cost_cents"] * item["quantity"]


def plan_items(plan_id):
    # Dated items first, by date; within a day, flights lead (you fly before
    # you check in or pick up the car), then creation order.
    return get_db().execute(
        "SELECT * FROM items WHERE plan_id = ? ORDER BY "
        "CASE WHEN start_date = '' THEN 1 ELSE 0 END, start_date, "
        "CASE WHEN category = 'flight' THEN 0 WHEN category = 'train' THEN 1 ELSE 2 END, id",
        (plan_id,),
    ).fetchall()


def plan_total(plan_id):
    row = get_db().execute(
        "SELECT COALESCE(SUM(cost_cents * quantity), 0) AS t FROM items WHERE plan_id = ?",
        (plan_id,),
    ).fetchone()
    return row["t"]


def plan_breakdown(plan_id):
    """Return (by_category dict, booked_cents, idea_cents)."""
    by_cat = {k: 0 for k in CATEGORIES}
    booked = idea = 0
    for it in plan_items(plan_id):
        tot = item_total(it)
        by_cat[it["category"]] = by_cat.get(it["category"], 0) + tot
        if it["status"] == "booked":
            booked += tot
        else:
            idea += tot
    return by_cat, booked, idea


def trip_days(trip):
    s, e = dparse(trip["start_date"]), dparse(trip["end_date"])
    if s and e and e >= s:
        return (e - s).days + 1
    return 0


def all_trips():
    db = get_db()
    trips = db.execute("SELECT * FROM trips ORDER BY start_date, id").fetchall()
    out = []
    for t in trips:
        plans = db.execute(
            "SELECT id FROM plans WHERE trip_id = ?", (t["id"],)
        ).fetchall()
        totals = [plan_total(p["id"]) for p in plans]
        out.append(
            {
                "trip": t,
                "plan_count": len(plans),
                "lowest": min(totals) if totals else 0,
                "nights": max(trip_days(t) - 1, 0),
            }
        )
    return out


def donut_segments(by_cat, total):
    """SVG stroke-dasharray segments for a donut chart, r=42, C=2*pi*r."""
    import math

    circ = 2 * math.pi * 42
    segs = []
    offset = 0.0
    if total > 0:
        for key, cents in by_cat.items():
            if cents <= 0:
                continue
            frac = cents / total
            segs.append(
                {
                    "color": CATEGORIES[key]["color"],
                    "label": CATEGORIES[key]["label"],
                    "cents": cents,
                    "pct": round(frac * 100),
                    "dash": "{} {}".format(frac * circ - 2, circ),
                    "offset": -offset,
                }
            )
            offset += frac * circ
    return segs


def build_calendar(trip, items):
    """Month grids covering the trip (or the items' span), items on days."""
    starts = [dparse(i["start_date"]) for i in items if dparse(i["start_date"])]
    ends = [dparse(i["end_date"]) or dparse(i["start_date"]) for i in items if dparse(i["start_date"])]
    lo = dparse(trip["start_date"]) or (min(starts) if starts else None)
    hi = dparse(trip["end_date"]) or (max(ends) if ends else None)
    if lo is None or hi is None:
        return [], [i for i in items if not dparse(i["start_date"])]
    first = date(lo.year, lo.month, 1)
    last = date(hi.year, hi.month, 1)
    months = []
    cur = first
    while cur <= last:
        if cur.month == 12:
            nxt = date(cur.year + 1, 1, 1)
        else:
            nxt = date(cur.year, cur.month + 1, 1)
        days_in_month = (nxt - cur).days
        lead = (cur.weekday() + 1) % 7  # Sunday-first grid
        cells = []
        for _ in range(lead):
            cells.append(None)
        for dnum in range(1, days_in_month + 1):
            d = date(cur.year, cur.month, dnum)
            day_items = []
            for i in items:
                s = dparse(i["start_date"])
                if not s:
                    continue
                e = dparse(i["end_date"]) or s
                if s <= d <= e:
                    day_items.append(i)
            cells.append(
                {
                    "date": d,
                    "in_trip": (not dparse(trip["start_date"]) or dparse(trip["start_date"]) <= d)
                    and (not dparse(trip["end_date"]) or d <= dparse(trip["end_date"])),
                    "items": day_items,
                }
            )
        months.append({"label": cur.strftime("%B %Y"), "cells": cells})
        cur = nxt
    undated = [i for i in items if not dparse(i["start_date"])]
    return months, undated


def render(content_tpl, **ctx):
    ctx.setdefault("trips_nav", all_trips())
    ctx.setdefault("categories", CATEGORIES)
    ctx.setdefault("fmt_money", fmt_money)
    content = render_template_string(content_tpl, **ctx)
    # Flask 3 autoescapes string templates; the pre-rendered page body must
    # be marked safe or it prints as raw HTML text inside the layout.
    return render_template_string(BASE, content=Markup(content), **ctx)


# ---------------------------------------------------------------- templates

BASE = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Vacation Planner</title>
<style>
  * { box-sizing: border-box; }
  body { margin: 0; font-family: -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
         background: #f4f6fb; color: #1e293b; }
  a { color: #2563eb; text-decoration: none; }
  .shell { display: flex; min-height: 100vh; align-items: flex-start; }
  aside { width: 302px; flex: 0 0 302px; background: #0f172a; color: #e2e8f0;
          min-height: 100vh; position: sticky; top: 0; padding: 18px 14px; }
  aside h1 { font-size: 19px; margin: 2px 6px 14px; color: #fff; letter-spacing: .2px; }
  aside h1 span { color: #38bdf8; }
  .trip-card { display: flex; gap: 11px; background: #1e293b; border: 1px solid #334155;
               border-radius: 12px; padding: 10px 11px; margin: 0 2px 10px; color: #e2e8f0; }
  .trip-card:hover { border-color: #475569; }
  .trip-card.active { border-color: #38bdf8; background: #243349; }
  .date-badge { flex: 0 0 46px; background: #38bdf8; color: #082f49; border-radius: 9px;
                text-align: center; padding: 5px 0 4px; line-height: 1.05; }
  .date-badge .m { font-size: 10px; font-weight: 700; text-transform: uppercase; }
  .date-badge .d { font-size: 19px; font-weight: 800; }
  .trip-card .nm { font-weight: 700; color: #fff; font-size: 14.5px; }
  .trip-card .meta { font-size: 12px; color: #94a3b8; margin-top: 2px; line-height: 1.45; }
  .trip-card .tot { font-size: 12.5px; color: #7dd3fc; font-weight: 700; margin-top: 3px; }
  .side-btn { display: block; text-align: center; background: #38bdf8; color: #082f49; font-weight: 700;
              border-radius: 10px; padding: 9px; margin: 4px 2px 0; }
  main { flex: 1; min-width: 0; padding: 22px 26px 60px; max-width: 1060px; }
  .hero { background: linear-gradient(120deg, #0ea5e9, #6366f1 60%, #a855f7); color: #fff;
          border-radius: 16px; padding: 20px 22px; margin-bottom: 16px; }
  .hero h2 { margin: 0 0 4px; font-size: 24px; }
  .hero .sub { opacity: .92; font-size: 14px; }
  .hero .big { font-size: 15px; margin-top: 10px; opacity: .95; }
  .hero .big b { font-size: 21px; }
  .card { background: #fff; border: 1px solid #e2e8f0; border-radius: 14px; padding: 16px 18px; margin-bottom: 16px; }
  .tabs { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 16px; }
  .tab { padding: 7px 14px; border-radius: 999px; background: #e2e8f0; color: #334155; font-weight: 600; font-size: 13.5px; }
  .tab.active { background: #0f172a; color: #fff; }
  .tab.view { background: #e0f2fe; color: #075985; }
  .tab.view.active { background: #0284c7; color: #fff; }
  .stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 12px; margin-bottom: 16px; }
  .stat { background: #fff; border: 1px solid #e2e8f0; border-radius: 14px; padding: 13px 15px; }
  .stat .k { font-size: 11.5px; text-transform: uppercase; letter-spacing: .6px; color: #64748b; font-weight: 700; }
  .stat .v { font-size: 20px; font-weight: 800; margin-top: 3px; }
  .bar { height: 10px; border-radius: 999px; background: #ede9fe; overflow: hidden; display: flex; }
  .bar .booked { background: #16a34a; height: 100%; }
  .bar .idea { background: #c4b5fd; height: 100%; }
  .item { display: flex; gap: 12px; align-items: center; padding: 10px 4px; border-bottom: 1px solid #f1f5f9; }
  .item:last-child { border-bottom: none; }
  .logo-tile { flex: 0 0 46px; width: 46px; height: 46px; background: #fff; border: 1px solid #e2e8f0;
               border-radius: 10px; display: flex; align-items: center; justify-content: center; overflow: hidden; }
  .logo-tile img { max-width: 40px; max-height: 34px; object-fit: contain; }
  .logo-tile.mono { color: #fff; font-weight: 800; font-size: 13px; border: none; }
  .item .tt { font-weight: 650; font-size: 15px; }
  .item .mt { font-size: 12.5px; color: #64748b; margin-top: 1px; }
  .item .cost { margin-left: auto; text-align: right; font-weight: 750; font-size: 15px; white-space: nowrap; }
  .item .acts { font-size: 12px; margin-top: 2px; }
  .chip { display: inline-block; font-size: 11px; font-weight: 700; padding: 1.5px 8px; border-radius: 999px; vertical-align: 1px; }
  .chip.booked { background: #dcfce7; color: #166534; }
  .chip.idea { background: #ede9fe; color: #5b21b6; }
  .chip.cat { color: #fff; }
  .btn { display: inline-block; background: #0284c7; color: #fff; font-weight: 650; border: none; cursor: pointer;
         border-radius: 9px; padding: 8px 15px; font-size: 14px; }
  .btn.ghost { background: #e2e8f0; color: #334155; }
  .btn.danger { background: #fee2e2; color: #b91c1c; }
  .btn.small { padding: 5px 11px; font-size: 13px; }
  form.inline { display: inline; }
  label { display: block; font-size: 13px; font-weight: 650; color: #475569; margin: 12px 0 4px; }
  input[type=text], input[type=number], input[type=date], input[type=url], select, textarea {
    width: 100%; padding: 9px 11px; border: 1px solid #cbd5e1; border-radius: 9px; font-size: 14.5px; background: #fff; }
  textarea { min-height: 64px; }
  .grid2 { display: grid; grid-template-columns: 1fr 1fr; gap: 0 14px; }
  .donut-wrap { display: flex; gap: 22px; align-items: center; flex-wrap: wrap; }
  .legend { font-size: 13.5px; line-height: 1.9; }
  .legend .dot { display: inline-block; width: 10px; height: 10px; border-radius: 3px; margin-right: 7px; }
  .cal-month { margin-bottom: 22px; }
  .cal-grid { display: grid; grid-template-columns: repeat(7, 1fr); gap: 5px; }
  .cal-dow { font-size: 11px; font-weight: 700; color: #94a3b8; text-transform: uppercase; text-align: center; padding: 2px 0; }
  .cal-day { background: #fff; border: 1px solid #e2e8f0; border-radius: 9px; min-height: 64px; padding: 5px 6px; font-size: 12px; }
  .cal-day.out { background: transparent; border: none; }
  .cal-day.intrip { background: #f0f9ff; border-color: #bae6fd; }
  .cal-day .dn { font-weight: 700; color: #475569; }
  .cal-dot { display: block; height: 5px; border-radius: 3px; margin-top: 3px; }
  .cal-ev { display: block; font-size: 10.5px; line-height: 1.35; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  table.cmp { width: 100%; border-collapse: collapse; font-size: 14px; }
  table.cmp th, table.cmp td { padding: 8px 10px; border-bottom: 1px solid #f1f5f9; text-align: right; }
  table.cmp th:first-child, table.cmp td:first-child { text-align: left; }
  table.cmp tr.total td { font-weight: 800; border-top: 2px solid #e2e8f0; }
  .cmpbar { height: 8px; background: #e2e8f0; border-radius: 99px; overflow: hidden; margin-top: 5px; }
  .cmpbar div { height: 100%; background: #0284c7; }
  .best { background: #dcfce7; color: #166534; font-size: 11px; font-weight: 800; padding: 2px 8px; border-radius: 99px; }
  .muted { color: #64748b; font-size: 13px; }
  h3.sec { margin: 4px 0 10px; font-size: 16.5px; }
  .tl-day { display: flex; gap: 14px; }
  .tl-rail { flex: 0 0 56px; display: flex; flex-direction: column; align-items: center; }
  .tl-badge { width: 54px; background: #0f172a; color: #fff; border-radius: 12px; text-align: center;
              padding: 6px 0 5px; line-height: 1.05; z-index: 1; box-shadow: 0 1px 3px rgba(15,23,42,.25); }
  .tl-badge .m { font-size: 10.5px; font-weight: 700; letter-spacing: 1.2px; color: #7dd3fc; }
  .tl-badge .d { font-size: 21px; font-weight: 800; }
  .tl-badge.none { background: #94a3b8; }
  .tl-line { flex: 1; width: 2.5px; background: #e2e8f0; margin-top: 5px; border-radius: 2px; }
  .tl-day:last-child .tl-line { display: none; }
  .tl-body { flex: 1; min-width: 0; padding-bottom: 20px; }
  .tl-date-label { font-weight: 750; font-size: 15.5px; margin: 5px 0 9px; }
  .tl-item { background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 12px; padding: 10px 12px;
             margin-bottom: 9px; display: flex; gap: 12px; align-items: center; }
  @media (max-width: 760px) {
    .shell { flex-direction: column; }
    aside { width: 100%; flex: none; min-height: 0; position: static; }
    main { padding: 16px 14px 50px; }
    .cal-day { min-height: 46px; }
    .cal-ev { display: none; }
  }
</style>
</head>
<body>
<div class="shell">
  <aside>
    <h1>Vacation <span>Planner</span></h1>
    {% for nav in trips_nav %}
    <a class="trip-card {% if active_trip and nav.trip.id == active_trip.id %}active{% endif %}" href="/trip/{{ nav.trip.id }}">
      <div class="date-badge">
        {% if nav.trip.start_date %}
        <div class="m">{{ nav.trip.start_date[5:7] }}/{{ nav.trip.start_date[0:4] }}</div>
        <div class="d">{{ nav.trip.start_date[8:10] }}</div>
        {% else %}
        <div class="m">Trip</div><div class="d">✈</div>
        {% endif %}
      </div>
      <div>
        <div class="nm">{{ nav.trip.name }}</div>
        <div class="meta">
          {% if nav.trip.destination %}{{ nav.trip.destination }}<br>{% endif %}
          {% if nav.trip.start_date %}{{ nav.trip.start_date }} → {{ nav.trip.end_date }}<br>{% endif %}
          {{ nav.plan_count }} plan{{ "s" if nav.plan_count != 1 }}{% if nav.nights %} · {{ nav.nights }} nights{% endif %}
        </div>
        {% if nav.lowest %}<div class="tot">from {{ fmt_money(nav.lowest) }}</div>{% endif %}
      </div>
    </a>
    {% endfor %}
    <a class="side-btn" href="/trips/new">+ New trip</a>
  </aside>
  <main>{{ content }}</main>
</div>
</body>
</html>
"""

HOME = """
<div class="hero">
  <h2>Plan the trip before you book it</h2>
  <div class="sub">Add flights, hotels, cars and things to do — build a few plans per trip and compare what each one really costs.</div>
</div>
<div class="card">
  {% if trips_nav %}
  <h3 class="sec">Pick a trip on the left to get started</h3>
  <p class="muted">Each trip can hold several plans — the booked one, the splurge one, the budget one — and the planner totals them side by side.</p>
  {% else %}
  <h3 class="sec">No trips yet</h3>
  <p class="muted">Create your first trip with the “+ New trip” button on the left.</p>
  {% endif %}
</div>
"""


TRIP_PAGE = """
<div class="hero">
  <h2>{{ trip.name }}</h2>
  <div class="sub">
    {% if trip.destination %}{{ trip.destination }} · {% endif %}
    {% if trip.start_date %}{{ trip.start_date }} → {{ trip.end_date }} · {% endif %}
    {{ trip.travelers }} traveler{{ "s" if trip.travelers != 1 }}
    {% if days %} · {{ days }} days{% endif %}
  </div>
  {% if trip.notes %}<div class="big" style="font-size:14px">{{ trip.notes }}</div>{% endif %}
  <div style="margin-top:12px">
    <a class="btn small ghost" href="/trip/{{ trip.id }}/edit">Edit trip</a>
    <form class="inline" method="post" action="/trip/{{ trip.id }}/delete" onsubmit="return confirm('Delete this trip and all its plans?')">
      <button class="btn small danger" type="submit">Delete trip</button>
    </form>
  </div>
</div>

<div class="tabs">
  {% for p in plans %}
  <a class="tab {% if plan and p.id == plan.id and view != 'compare' %}active{% endif %}" href="/trip/{{ trip.id }}?plan={{ p.id }}&view={{ view if view in ['plan','calendar'] else 'plan' }}">{{ p.name }}</a>
  {% endfor %}
  <a class="tab" href="/trip/{{ trip.id }}/plans/new">+ New plan</a>
</div>
{% if plans %}
<div class="tabs">
  <a class="tab view {% if view == 'plan' %}active{% endif %}" href="/trip/{{ trip.id }}?plan={{ plan.id }}&view=plan">Items</a>
  <a class="tab view {% if view == 'calendar' %}active{% endif %}" href="/trip/{{ trip.id }}?plan={{ plan.id }}&view=calendar">Calendar</a>
  <a class="tab view {% if view == 'compare' %}active{% endif %}" href="/trip/{{ trip.id }}?view=compare">Compare plans</a>
</div>
{% endif %}

{% if not plans %}
<div class="card"><h3 class="sec">No plans yet</h3>
<p class="muted">A plan is one version of this trip — the booked one, or a hypothetical you are pricing out. Add the first one above.</p></div>
{% endif %}

{% if view == 'plan' and plan %}
<div class="stats">
  <div class="stat"><div class="k">Plan total</div><div class="v">{{ fmt_money(total) }}</div></div>
  <div class="stat"><div class="k">Per person</div><div class="v">{{ fmt_money(per_person) }}</div></div>
  <div class="stat"><div class="k">Per day</div><div class="v">{{ fmt_money(per_day) }}</div></div>
  <div class="stat"><div class="k">Booked</div><div class="v">{{ fmt_money(booked) }}</div></div>
  <div class="stat"><div class="k">Still hypothetical</div><div class="v">{{ fmt_money(idea) }}</div></div>
</div>

<div class="card">
  <div class="donut-wrap">
    <svg width="132" height="132" viewBox="0 0 100 100">
      <circle cx="50" cy="50" r="42" fill="none" stroke="#f1f5f9" stroke-width="13"/>
      {% for s in segs %}
      <circle cx="50" cy="50" r="42" fill="none" stroke="{{ s.color }}" stroke-width="13"
              stroke-dasharray="{{ s.dash }}" stroke-dashoffset="{{ s.offset }}"
              transform="rotate(-90 50 50)"/>
      {% endfor %}
      <text x="50" y="48" text-anchor="middle" font-size="12.5" font-weight="800" fill="#0f172a">{{ fmt_money(total) }}</text>
      <text x="50" y="61" text-anchor="middle" font-size="7.5" fill="#64748b">total</text>
    </svg>
    <div class="legend">
      {% for s in segs %}
      <div><span class="dot" style="background:{{ s.color }}"></span>{{ s.label }} — {{ fmt_money(s.cents) }} ({{ s.pct }}%)</div>
      {% endfor %}
      {% if not segs %}<div class="muted">No costs yet — add an item below.</div>{% endif %}
    </div>
  </div>
  {% if total %}
  <div style="margin-top:14px">
    <div class="bar"><div class="booked" style="width:{{ booked * 100 // total }}%"></div><div class="idea" style="width:{{ 100 - (booked * 100 // total) }}%"></div></div>
    <div class="muted" style="margin-top:5px">{{ booked * 100 // total }}% booked · the rest is still hypothetical</div>
  </div>
  {% endif %}
  {% if plan.notes %}<p class="muted" style="margin-bottom:0">{{ plan.notes }}</p>{% endif %}
  <div style="margin-top:12px">
    <form class="inline" method="post" action="/plan/{{ plan.id }}/duplicate"><button class="btn small ghost" type="submit">Duplicate plan</button></form>
    <a class="btn small ghost" href="/plan/{{ plan.id }}/edit">Rename / notes</a>
    <form class="inline" method="post" action="/plan/{{ plan.id }}/delete" onsubmit="return confirm('Delete this plan and its items?')"><button class="btn small danger" type="submit">Delete plan</button></form>
  </div>
</div>

<div class="card">
  <h3 class="sec">{{ plan.name }} — itinerary</h3>
  <div class="timeline">
  {% for g in timeline %}
    <div class="tl-day">
      <div class="tl-rail">
        <div class="tl-badge {% if g.label == 'No date yet' %}none{% endif %}"><div class="m">{{ g.badge_m }}</div><div class="d">{{ g.badge_d }}</div></div>
        <div class="tl-line"></div>
      </div>
      <div class="tl-body">
        <div class="tl-date-label">{{ g.label }}</div>
        {% for r in g.rows %}
        <div class="tl-item">
          {% if r.logo %}
          <div class="logo-tile"><img src="/static/logos/{{ r.logo }}" alt=""></div>
          {% else %}
          <div class="logo-tile mono" style="background:{{ categories[r.item.category].color }}">{{ mono[r.item.category] }}</div>
          {% endif %}
          <div>
            <div class="tt">{{ r.item.title }}
              <span class="chip cat" style="background:{{ categories[r.item.category].color }}">{{ categories[r.item.category].label }}</span>
              <span class="chip {{ r.item.status }}">{{ "Booked" if r.item.status == "booked" else "Idea" }}</span>
            </div>
            <div class="mt">
              {% if r.span %}{{ r.span }} · {% endif %}
              {% if r.item.quantity != 1 %}{{ r.item.quantity }} × {{ fmt_money(r.item.cost_cents) }} · {% endif %}
              {% if r.item.notes %}{{ r.item.notes }}{% endif %}
              {% if r.item.link %} · <a href="{{ r.item.link }}" target="_blank" rel="noopener">link</a>{% endif %}
            </div>
            <div class="acts"><a href="/item/{{ r.item.id }}/edit">edit</a> ·
              <form class="inline" method="post" action="/item/{{ r.item.id }}/delete" onsubmit="return confirm('Delete this item?')"><button type="submit" style="background:none;border:none;color:#b91c1c;cursor:pointer;font-size:12px;padding:0">delete</button></form>
            </div>
          </div>
          <div class="cost">{{ fmt_money(r.total) }}</div>
        </div>
        {% endfor %}
      </div>
    </div>
  {% endfor %}
  </div>
  {% if not rows %}<p class="muted">Nothing here yet.</p>{% endif %}
  <div style="margin-top:13px"><a class="btn" href="/plan/{{ plan.id }}/items/new">+ Add item</a></div>
</div>
{% endif %}

{% if view == 'calendar' and plan %}
<div class="card">
  <h3 class="sec">{{ plan.name }} — calendar</h3>
  {% for m in months %}
  <div class="cal-month">
    <div style="font-weight:750;margin-bottom:7px">{{ m.label }}</div>
    <div class="cal-grid">
      <div class="cal-dow">Su</div><div class="cal-dow">Mo</div><div class="cal-dow">Tu</div><div class="cal-dow">We</div><div class="cal-dow">Th</div><div class="cal-dow">Fr</div><div class="cal-dow">Sa</div>
      {% for c in m.cells %}
      {% if c is none %}
      <div class="cal-day out"></div>
      {% else %}
      <div class="cal-day {% if c.in_trip %}intrip{% endif %}">
        <span class="dn">{{ c.date.day }}</span>
        {% for i in c["items"] %}
        <span class="cal-dot" style="background:{{ categories[i.category].color }}"></span>
        <span class="cal-ev">{{ i.title }}</span>
        {% endfor %}
      </div>
      {% endif %}
      {% endfor %}
    </div>
  </div>
  {% endfor %}
  {% if not months %}<p class="muted">No dates on this trip or its items yet — add dates and the calendar fills in.</p>{% endif %}
</div>

<div class="card">
  <h3 class="sec">Day by day</h3>
  {% for day, day_items in agenda %}
  <div style="margin-bottom:9px">
    <div style="font-weight:700;font-size:13.5px">{{ day }}</div>
    {% for i in day_items %}
    <div class="muted" style="font-size:13.5px">· {{ i.title }} — {{ fmt_money(i.cost_cents * i.quantity) }}</div>
    {% endfor %}
  </div>
  {% endfor %}
  {% if not agenda %}<p class="muted">No dated items yet.</p>{% endif %}
  {% if undated %}
  <h3 class="sec" style="margin-top:16px">No date yet</h3>
  {% for i in undated %}
  <div class="muted" style="font-size:13.5px">· {{ i.title }} — {{ fmt_money(i.cost_cents * i.quantity) }} (<a href="/item/{{ i.id }}/edit">add a date</a>)</div>
  {% endfor %}
  {% endif %}
</div>
{% endif %}

{% if view == 'compare' %}
<div class="card">
  <h3 class="sec">Compare plans for {{ trip.name }}</h3>
  {% if compare|length < 2 %}<p class="muted">Add or duplicate a plan to compare versions of this trip side by side.</p>{% endif %}
  <table class="cmp">
    <tr><th></th>
    {% for c in compare %}
      <th>{{ c.plan.name }}<br><span style="font-size:17px">{{ fmt_money(c.total) }}</span>
      {% if c.cheapest %} <span class="best">LOWEST</span>{% endif %}
      <div class="cmpbar"><div style="width:{{ c.bar_pct }}%"></div></div></th>
    {% endfor %}
    </tr>
    {% for cat_key, cat in categories.items() %}
    {% if cat_totals[cat_key] %}
    <tr><td><span class="legend"><span class="dot" style="background:{{ cat.color }}"></span></span>{{ cat.label }}</td>
    {% for c in compare %}<td>{{ fmt_money(c.by_cat[cat_key]) if c.by_cat[cat_key] else "—" }}</td>{% endfor %}
    </tr>
    {% endif %}
    {% endfor %}
    <tr><td>Booked</td>{% for c in compare %}<td>{{ fmt_money(c.booked) }}</td>{% endfor %}</tr>
    <tr><td>Still hypothetical</td>{% for c in compare %}<td>{{ fmt_money(c.idea) }}</td>{% endfor %}</tr>
    <tr><td>Per person</td>{% for c in compare %}<td>{{ fmt_money(c.per_person) }}</td>{% endfor %}</tr>
    <tr><td>Per day</td>{% for c in compare %}<td>{{ fmt_money(c.per_day) }}</td>{% endfor %}</tr>
    <tr class="total"><td>Total</td>{% for c in compare %}<td>{{ fmt_money(c.total) }}</td>{% endfor %}</tr>
  </table>
</div>
{% endif %}
"""


TRIP_FORM = """
<div class="card" style="max-width:560px">
  <h3 class="sec">{{ heading }}</h3>
  <form method="post" action="{{ action }}">
    <label>Trip name</label>
    <input type="text" name="name" required value="{{ obj.name if obj else '' }}" placeholder="December California Trip">
    <label>Destination</label>
    <input type="text" name="destination" value="{{ obj.destination if obj else '' }}" placeholder="Los Angeles & Palm Springs, CA">
    <div class="grid2">
      <div><label>Start date</label><input type="date" name="start_date" value="{{ obj.start_date if obj else '' }}"></div>
      <div><label>End date</label><input type="date" name="end_date" value="{{ obj.end_date if obj else '' }}"></div>
    </div>
    <label>Travelers</label>
    <input type="number" name="travelers" min="1" value="{{ obj.travelers if obj else 1 }}">
    <label>Notes</label>
    <textarea name="notes" placeholder="Who is going, what is still open…">{{ obj.notes if obj else '' }}</textarea>
    <div style="margin-top:16px"><button class="btn" type="submit">Save trip</button></div>
  </form>
</div>
"""

PLAN_FORM = """
<div class="card" style="max-width:560px">
  <h3 class="sec">{{ heading }}</h3>
  <form method="post" action="{{ action }}">
    <label>Plan name</label>
    <input type="text" name="name" required value="{{ obj.name if obj else '' }}" placeholder="Booked plan">
    <label>Notes</label>
    <textarea name="notes" placeholder="What makes this version different…">{{ obj.notes if obj else '' }}</textarea>
    <div style="margin-top:16px"><button class="btn" type="submit">Save plan</button></div>
  </form>
</div>
"""

ITEM_FORM = """
<div class="card" style="max-width:560px">
  <h3 class="sec">{{ heading }}</h3>
  <form method="post" action="{{ action }}">
    <label>Title</label>
    <input type="text" name="title" required value="{{ obj.title if obj else '' }}" placeholder="Alaska Airlines AS496 / AS594">
    <div class="grid2">
      <div><label>Category</label>
        <select name="category">
          {% for key, cat in categories.items() %}
          <option value="{{ key }}" {% if obj and obj.category == key %}selected{% endif %}>{{ cat.label }}</option>
          {% endfor %}
        </select></div>
      <div><label>Status</label>
        <select name="status">
          <option value="idea" {% if obj and obj.status == 'idea' %}selected{% endif %}>Idea (hypothetical)</option>
          <option value="booked" {% if obj and obj.status == 'booked' %}selected{% endif %}>Booked</option>
        </select></div>
    </div>
    <div class="grid2">
      <div><label>Cost ($)</label><input type="text" name="cost" value="{{ '%.2f' % (obj.cost_cents / 100) if obj else '' }}" placeholder="0.00"></div>
      <div><label>Quantity</label><input type="number" name="quantity" min="1" value="{{ obj.quantity if obj else 1 }}"></div>
    </div>
    <div class="grid2">
      <div><label>Start date</label><input type="date" name="start_date" value="{{ obj.start_date if obj else '' }}"></div>
      <div><label>End date</label><input type="date" name="end_date" value="{{ obj.end_date if obj else '' }}"></div>
    </div>
    <label>Link</label>
    <input type="url" name="link" value="{{ obj.link if obj else '' }}" placeholder="https://…">
    <label>Notes</label>
    <textarea name="notes" placeholder="Confirmation numbers, seats, cancellation terms…">{{ obj.notes if obj else '' }}</textarea>
    <div style="margin-top:16px"><button class="btn" type="submit">Save item</button></div>
  </form>
</div>
"""

MONO = {"flight": "FL", "hotel": "HT", "car": "CR", "train": "TR", "bus": "BU", "activity": "AC", "food": "FD", "other": "OT"}


# ---------------------------------------------------------------- routes

@app.route("/")
def index():
    trips = all_trips()
    if trips:
        return redirect(url_for("trip_page", trip_id=trips[0]["trip"]["id"]))
    return render(HOME, active_trip=None)


@app.route("/trip/<int:trip_id>")
def trip_page(trip_id):
    db = get_db()
    trip = db.execute("SELECT * FROM trips WHERE id = ?", (trip_id,)).fetchone()
    if trip is None:
        return redirect(url_for("index"))
    plans = db.execute("SELECT * FROM plans WHERE trip_id = ? ORDER BY id", (trip_id,)).fetchall()
    view = request.args.get("view", "plan")
    plan = None
    if plans:
        pid = request.args.get("plan", type=int)
        plan = next((p for p in plans if p["id"] == pid), plans[0])
    ctx = {
        "active_trip": trip,
        "trip": trip,
        "plans": plans,
        "plan": plan,
        "view": view,
        "days": trip_days(trip),
        "mono": MONO,
    }
    if plan is not None and view == "plan":
        items = plan_items(plan["id"])
        by_cat, booked, idea = plan_breakdown(plan["id"])
        total = booked + idea
        rows = [{"item": i, "logo": logo_for(i["title"]), "total": item_total(i)} for i in items]
        groups = {}
        order = []
        for r in rows:
            key = r["item"]["start_date"] or ""
            if key not in groups:
                groups[key] = []
                order.append(key)
            s, e = dparse(r["item"]["start_date"]), dparse(r["item"]["end_date"])
            r["span"] = ""
            if s and e and e > s:
                nights = (e - s).days
                r["span"] = "→ {} · {} night{}".format(e.isoformat(), nights, "s" if nights != 1 else "")
            groups[key].append(r)
        timeline = []
        for key in order:
            d = dparse(key)
            timeline.append(
                {
                    "badge_m": d.strftime("%b").upper() if d else "—",
                    "badge_d": str(d.day) if d else "•",
                    "label": "{}, {} {}, {}".format(d.strftime("%A"), d.strftime("%B"), d.day, d.year)
                    if d
                    else "No date yet",
                    "rows": groups[key],
                }
            )
        ctx.update(
            rows=rows,
            timeline=timeline,
            by_cat=by_cat, booked=booked, idea=idea, total=total,
            per_person=int(total / trip["travelers"]) if trip["travelers"] else total,
            per_day=int(total / ctx["days"]) if ctx["days"] else total,
            segs=donut_segments(by_cat, total),
        )
    if plan is not None and view == "calendar":
        items = plan_items(plan["id"])
        months, undated = build_calendar(trip, items)
        agenda_map = {}
        for i in items:
            if i["start_date"]:
                agenda_map.setdefault(i["start_date"], []).append(i)
        ctx.update(months=months, undated=undated, agenda=sorted(agenda_map.items()))
    if view == "compare":
        compare = []
        cat_totals = {k: 0 for k in CATEGORIES}
        for p in plans:
            by_cat, booked, idea = plan_breakdown(p["id"])
            total = booked + idea
            for k in CATEGORIES:
                cat_totals[k] += by_cat[k]
            compare.append(
                {
                    "plan": p, "by_cat": by_cat, "booked": booked, "idea": idea, "total": total,
                    "per_person": int(total / trip["travelers"]) if trip["travelers"] else total,
                    "per_day": int(total / ctx["days"]) if ctx["days"] else total,
                }
            )
        max_total = max([c["total"] for c in compare], default=0)
        min_total = min([c["total"] for c in compare if c["total"] > 0], default=0)
        for c in compare:
            c["bar_pct"] = int(c["total"] * 100 / max_total) if max_total else 0
            c["cheapest"] = len(compare) > 1 and c["total"] == min_total and min_total > 0
        ctx.update(compare=compare, cat_totals=cat_totals)
    return render(TRIP_PAGE, **ctx)


def trip_from_form():
    return {
        "name": request.form.get("name", "").strip() or "Untitled trip",
        "destination": request.form.get("destination", "").strip(),
        "start_date": request.form.get("start_date", ""),
        "end_date": request.form.get("end_date", ""),
        "travelers": max(1, request.form.get("travelers", 1, type=int) or 1),
        "notes": request.form.get("notes", "").strip(),
    }


@app.route("/trips/new", methods=["GET", "POST"])
def trip_new():
    if request.method == "POST":
        f = trip_from_form()
        db = get_db()
        cur = db.execute(
            "INSERT INTO trips (name, destination, start_date, end_date, travelers, notes) VALUES (?,?,?,?,?,?)",
            (f["name"], f["destination"], f["start_date"], f["end_date"], f["travelers"], f["notes"]),
        )
        db.execute("INSERT INTO plans (trip_id, name) VALUES (?, 'Plan A')", (cur.lastrowid,))
        db.commit()
        return redirect(url_for("trip_page", trip_id=cur.lastrowid))
    return render(TRIP_FORM, heading="New trip", action="/trips/new", obj=None, active_trip=None)


@app.route("/trip/<int:trip_id>/edit", methods=["GET", "POST"])
def trip_edit(trip_id):
    db = get_db()
    trip = db.execute("SELECT * FROM trips WHERE id = ?", (trip_id,)).fetchone()
    if trip is None:
        return redirect(url_for("index"))
    if request.method == "POST":
        f = trip_from_form()
        db.execute(
            "UPDATE trips SET name=?, destination=?, start_date=?, end_date=?, travelers=?, notes=? WHERE id=?",
            (f["name"], f["destination"], f["start_date"], f["end_date"], f["travelers"], f["notes"], trip_id),
        )
        db.commit()
        return redirect(url_for("trip_page", trip_id=trip_id))
    return render(TRIP_FORM, heading="Edit trip", action="/trip/{}/edit".format(trip_id), obj=trip, active_trip=trip)


@app.route("/trip/<int:trip_id>/delete", methods=["POST"])
def trip_delete(trip_id):
    db = get_db()
    db.execute("DELETE FROM items WHERE plan_id IN (SELECT id FROM plans WHERE trip_id = ?)", (trip_id,))
    db.execute("DELETE FROM plans WHERE trip_id = ?", (trip_id,))
    db.execute("DELETE FROM trips WHERE id = ?", (trip_id,))
    db.commit()
    return redirect(url_for("index"))


@app.route("/trip/<int:trip_id>/plans/new", methods=["GET", "POST"])
def plan_new(trip_id):
    db = get_db()
    trip = db.execute("SELECT * FROM trips WHERE id = ?", (trip_id,)).fetchone()
    if trip is None:
        return redirect(url_for("index"))
    if request.method == "POST":
        name = request.form.get("name", "").strip() or "New plan"
        notes = request.form.get("notes", "").strip()
        cur = db.execute("INSERT INTO plans (trip_id, name, notes) VALUES (?,?,?)", (trip_id, name, notes))
        db.commit()
        return redirect(url_for("trip_page", trip_id=trip_id, plan=cur.lastrowid))
    return render(PLAN_FORM, heading="New plan", action="/trip/{}/plans/new".format(trip_id), obj=None, active_trip=trip)


@app.route("/plan/<int:plan_id>/edit", methods=["GET", "POST"])
def plan_edit(plan_id):
    db = get_db()
    plan = db.execute("SELECT * FROM plans WHERE id = ?", (plan_id,)).fetchone()
    if plan is None:
        return redirect(url_for("index"))
    if request.method == "POST":
        name = request.form.get("name", "").strip() or plan["name"]
        notes = request.form.get("notes", "").strip()
        db.execute("UPDATE plans SET name=?, notes=? WHERE id=?", (name, notes, plan_id))
        db.commit()
        return redirect(url_for("trip_page", trip_id=plan["trip_id"], plan=plan_id))
    return render(PLAN_FORM, heading="Edit plan", action="/plan/{}/edit".format(plan_id), obj=plan, active_trip=None)


@app.route("/plan/<int:plan_id>/delete", methods=["POST"])
def plan_delete(plan_id):
    db = get_db()
    plan = db.execute("SELECT * FROM plans WHERE id = ?", (plan_id,)).fetchone()
    if plan is None:
        return redirect(url_for("index"))
    db.execute("DELETE FROM items WHERE plan_id = ?", (plan_id,))
    db.execute("DELETE FROM plans WHERE id = ?", (plan_id,))
    db.commit()
    return redirect(url_for("trip_page", trip_id=plan["trip_id"]))


@app.route("/plan/<int:plan_id>/duplicate", methods=["POST"])
def plan_duplicate(plan_id):
    db = get_db()
    plan = db.execute("SELECT * FROM plans WHERE id = ?", (plan_id,)).fetchone()
    if plan is None:
        return redirect(url_for("index"))
    cur = db.execute(
        "INSERT INTO plans (trip_id, name, notes) VALUES (?,?,?)",
        (plan["trip_id"], plan["name"] + " (copy)", plan["notes"]),
    )
    new_id = cur.lastrowid
    for i in plan_items(plan_id):
        db.execute(
            "INSERT INTO items (plan_id, category, title, cost_cents, quantity, status, start_date, end_date, link, notes)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (new_id, i["category"], i["title"], i["cost_cents"], i["quantity"], i["status"],
             i["start_date"], i["end_date"], i["link"], i["notes"]),
        )
    db.commit()
    return redirect(url_for("trip_page", trip_id=plan["trip_id"], plan=new_id))


def item_from_form():
    cat = request.form.get("category", "other")
    if cat not in CATEGORIES:
        cat = "other"
    status = "booked" if request.form.get("status") == "booked" else "idea"
    return {
        "category": cat,
        "title": request.form.get("title", "").strip() or "Untitled item",
        "cost_cents": parse_money(request.form.get("cost", "")),
        "quantity": max(1, request.form.get("quantity", 1, type=int) or 1),
        "status": status,
        "start_date": request.form.get("start_date", ""),
        "end_date": request.form.get("end_date", ""),
        "link": request.form.get("link", "").strip(),
        "notes": request.form.get("notes", "").strip(),
    }


@app.route("/plan/<int:plan_id>/items/new", methods=["GET", "POST"])
def item_new(plan_id):
    db = get_db()
    plan = db.execute("SELECT * FROM plans WHERE id = ?", (plan_id,)).fetchone()
    if plan is None:
        return redirect(url_for("index"))
    if request.method == "POST":
        f = item_from_form()
        db.execute(
            "INSERT INTO items (plan_id, category, title, cost_cents, quantity, status, start_date, end_date, link, notes)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (plan_id, f["category"], f["title"], f["cost_cents"], f["quantity"], f["status"],
             f["start_date"], f["end_date"], f["link"], f["notes"]),
        )
        db.commit()
        return redirect(url_for("trip_page", trip_id=plan["trip_id"], plan=plan_id))
    return render(ITEM_FORM, heading="New item", action="/plan/{}/items/new".format(plan_id), obj=None, active_trip=None)


@app.route("/item/<int:item_id>/edit", methods=["GET", "POST"])
def item_edit(item_id):
    db = get_db()
    item = db.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
    if item is None:
        return redirect(url_for("index"))
    plan = db.execute("SELECT * FROM plans WHERE id = ?", (item["plan_id"],)).fetchone()
    if request.method == "POST":
        f = item_from_form()
        db.execute(
            "UPDATE items SET category=?, title=?, cost_cents=?, quantity=?, status=?, start_date=?, end_date=?, link=?, notes=? WHERE id=?",
            (f["category"], f["title"], f["cost_cents"], f["quantity"], f["status"],
             f["start_date"], f["end_date"], f["link"], f["notes"], item_id),
        )
        db.commit()
        return redirect(url_for("trip_page", trip_id=plan["trip_id"], plan=plan["id"]))
    return render(ITEM_FORM, heading="Edit item", action="/item/{}/edit".format(item_id), obj=item, active_trip=None)


@app.route("/item/<int:item_id>/delete", methods=["POST"])
def item_delete(item_id):
    db = get_db()
    item = db.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
    if item is None:
        return redirect(url_for("index"))
    plan = db.execute("SELECT * FROM plans WHERE id = ?", (item["plan_id"],)).fetchone()
    db.execute("DELETE FROM items WHERE id = ?", (item_id,))
    db.commit()
    return redirect(url_for("trip_page", trip_id=plan["trip_id"], plan=plan["id"]))


if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=8089)
