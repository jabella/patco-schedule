# patco-schedule

PATCO Speedline timetables as small per-station JSON files, rebuilt daily from
PATCO's official GTFS feed. Used by a PATCO
"next train" app for Tronbyt/Tidbyt displays.

PATCO has no real-time train tracking, so the schedule is all there is. The
feed carries the weekday/Saturday/Sunday patterns plus special-service days
(track work, events), and this job applies them so the app doesn't have to.

## Files

- `data/v1/index.json`: feed version, date range and station list
- `data/v1/stops/<stop_id>.json`: one station's departures:

```json
{
  "v": 1,
  "feed_version": "20",
  "first_day": "20260924",
  "last_day": "20261231",
  "stop": {"id": "4", "name": "Haddonfield"},
  "services": {"s2": {"phl": [276, 306], "lnd": [289]}},
  "days": {"20261112": ["s2"]}
}
```

Times are minutes after midnight on the service day, and can go past 1440 for
trips that run after midnight. `phl` is westbound to Philadelphia and `lnd` is
eastbound to Lindenwold. A trip's last stop isn't listed, because riders can't
board there.

## Updating

`.github/workflows/update.yml` runs daily. It downloads the feed, runs
`convert.py`, and commits only when the timetable actually changed (the
output has no timestamps). You can also run it by hand from the Actions tab.

If PATCO announces a holiday schedule that isn't in the feed yet, add it to
`overrides.json` with the service IDs listed in `data/v1/index.json`:

```json
{"20261126": ["194 Sunday"]}
```

To run it locally:

```sh
python3 convert.py PATCO_GTFS.zip data overrides.json
```

Schedule data © PATCO / DRPA.
