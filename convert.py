#!/usr/bin/env python3
"""Convert PATCO's GTFS feed into small per-station JSON timetables.

Pixlet apps can't comfortably parse a 600 KB stop_times.txt on every refresh,
so this boils the feed down to one file per station:

    data/v1/stops/<stop_id>.json
    data/v1/index.json

Each station file holds, for every service pattern that serves the station,
the departure minutes (past the service day's midnight) in each direction,
plus a calendar mapping every date in the feed to the patterns running that
day. Holiday/special-event overrides from calendar_dates.txt are applied
here, so the app never has to know about them.

Usage: convert.py <gtfs.zip> <output_dir> [overrides.json]

overrides.json (optional) replaces the services on specific dates, for
holidays PATCO announces but hasn't put in the feed yet, e.g.
    {"20261126": ["194 Sunday"]}
Unknown service IDs are rejected so a typo can't silently blank a day.
"""

import csv
import datetime as dt
import hashlib
import io
import json
import sys
import zipfile
from collections import defaultdict
from pathlib import Path

SCHEMA_VERSION = 1

# GTFS direction_id -> short key used in the JSON and the app
DIRECTIONS = {"0": "phl", "1": "lnd"}  # 0 = westbound to Philadelphia, 1 = eastbound to Lindenwold


def read_csv(zf, name):
    with zf.open(name) as f:
        text = io.TextIOWrapper(f, encoding="utf-8-sig")
        return [{k.strip(): (v or "").strip() for k, v in row.items()} for row in csv.DictReader(text)]


def to_minutes(hms):
    h, m, s = (int(x) for x in hms.split(":"))
    return h * 60 + m + (1 if s >= 30 else 0)


def parse_date(s):
    return dt.date(int(s[:4]), int(s[4:6]), int(s[6:8]))


def active_dates(calendar, calendar_dates):
    """Return {date: set(service_id)} with exceptions applied."""
    days = defaultdict(set)
    weekdays = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    for row in calendar:
        d, end = parse_date(row["start_date"]), parse_date(row["end_date"])
        while d <= end:
            if row[weekdays[d.weekday()]] == "1":
                days[d].add(row["service_id"])
            d += dt.timedelta(days=1)
    for row in calendar_dates:
        d = parse_date(row["date"])
        if row["exception_type"] == "1":
            days[d].add(row["service_id"])
        elif row["exception_type"] == "2":
            days[d].discard(row["service_id"])
    return {d: s for d, s in days.items() if s}


def convert(zip_path, out_dir, overrides_path=None):
    with zipfile.ZipFile(zip_path) as zf:
        stops = read_csv(zf, "stops.txt")
        trips = {t["trip_id"]: t for t in read_csv(zf, "trips.txt")}
        stop_times = read_csv(zf, "stop_times.txt")
        calendar = read_csv(zf, "calendar.txt") if "calendar.txt" in zf.namelist() else []
        calendar_dates = read_csv(zf, "calendar_dates.txt") if "calendar_dates.txt" in zf.namelist() else []
        feed_info = read_csv(zf, "feed_info.txt")[0] if "feed_info.txt" in zf.namelist() else {}

    # Group stop_times by trip so we can find each trip's final stop
    by_trip = defaultdict(list)
    for st in stop_times:
        by_trip[st["trip_id"]].append(st)

    # deps[stop_id][service_id][direction] -> sorted minutes
    deps = defaultdict(lambda: defaultdict(lambda: defaultdict(set)))
    for trip_id, rows in by_trip.items():
        trip = trips.get(trip_id)
        if trip is None:
            continue
        direction = DIRECTIONS.get(trip.get("direction_id", ""))
        if direction is None:
            continue
        rows.sort(key=lambda r: int(r["stop_sequence"]))
        for st in rows[:-1]:  # last stop is arrival-only: you can't board there
            if st.get("pickup_type") == "1":
                continue
            t = st["departure_time"] or st["arrival_time"]
            if not t:
                continue
            deps[st["stop_id"]][trip["service_id"]][direction].add(to_minutes(t))

    days = active_dates(calendar, calendar_dates)
    if overrides_path and Path(overrides_path).exists():
        known = {r["service_id"] for r in calendar} | {r["service_id"] for r in calendar_dates}
        for date_str, svcs in json.loads(Path(overrides_path).read_text()).items():
            if date_str.startswith("_"):
                continue  # comment keys
            bad = [s for s in svcs if s not in known]
            if bad:
                raise SystemExit("overrides.json %s: unknown service %s" % (date_str, bad))
            days[parse_date(date_str)] = set(svcs)
    if not days:
        raise SystemExit("feed has no active service dates")
    first_day, last_day = min(days), max(days)

    # Short, stable-per-build codes for service IDs ("194 Weekday" -> "s0")
    service_ids = sorted({s for ss in days.values() for s in ss})
    code = {s: "s%d" % i for i, s in enumerate(service_ids)}

    # Deterministic output (no build timestamp) so the weekly job only commits
    # when PATCO actually publishes a new schedule.
    meta = {
        "v": SCHEMA_VERSION,
        "feed_version": feed_info.get("feed_version", ""),
        "feed_sha1": hashlib.sha1(Path(zip_path).read_bytes()).hexdigest()[:12],
        "first_day": first_day.strftime("%Y%m%d"),
        "last_day": last_day.strftime("%Y%m%d"),
    }

    stops_dir = Path(out_dir) / "v1" / "stops"
    stops_dir.mkdir(parents=True, exist_ok=True)
    index_stops = []
    for stop in sorted(stops, key=lambda s: int(s["stop_id"]) if s["stop_id"].isdigit() else 999):
        sid = stop["stop_id"]
        if stop.get("location_type", "0") not in ("", "0"):
            continue
        stop_deps = deps.get(sid, {})
        services = {
            code[svc]: {d: sorted(m) for d, m in dirs.items()}
            for svc, dirs in stop_deps.items()
            if svc in code
        }
        calendar_out = {
            d.strftime("%Y%m%d"): sorted(code[s] for s in ss if code[s] in services)
            for d, ss in sorted(days.items())
        }
        doc = dict(meta, stop={"id": sid, "name": stop["stop_name"]}, services=services, days=calendar_out)
        (stops_dir / ("%s.json" % sid)).write_text(json.dumps(doc, separators=(",", ":"), sort_keys=True))
        index_stops.append({"id": sid, "name": stop["stop_name"],
                            "directions": sorted({d for dirs in services.values() for d in dirs})})

    index = dict(meta, stops=index_stops, services={c: s for s, c in code.items()})
    (Path(out_dir) / "v1" / "index.json").write_text(json.dumps(index, indent=1, sort_keys=True))
    return index


if __name__ == "__main__":
    if len(sys.argv) not in (3, 4):
        raise SystemExit(__doc__)
    idx = convert(*sys.argv[1:])
    print("feed %s: %s..%s, %d stops" % (idx["feed_version"], idx["first_day"], idx["last_day"], len(idx["stops"])))
