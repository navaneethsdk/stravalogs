# stravalogs

Archive your Strava activities into this repo.

- GPS activities are saved as `.gpx` (from Strava export when available).
- Non-GPS/manual activities are saved as `.json` metadata.
- If GPX export is unavailable for a GPS activity, the script saves JSON including Strava **streams**.

## Setup

### Requirements

- Python 3.12+ (works on older 3.x too)

Install deps:

```bash
pip install requests
```

### Strava API credentials

Create a Strava API application at `https://www.strava.com/settings/api` and set:

- **`STRAVA_CLIENT_ID`**
- **`STRAVA_CLIENT_SECRET`**
- **`STRAVA_REFRESH_TOKEN`**

Notes:

- To get fields like **`description`** reliably, your token typically needs the **`activity:read_all`** scope.
- The activity list endpoint often omits fields like `description`, so this project fetches **detailed activity** data per activity ID.

## Run

### Sync from Strava

This will fetch new activities and write files under `activities/`:

```bash
python scripts/sync_strava.py
```

Optional env vars:

- **Full re-sync (reset offset)**:

```bash
STRAVA_FULL_SYNC=1 python scripts/sync_strava.py
```

- **Debug logs**:

```bash
STRAVA_DEBUG=1 python scripts/sync_strava.py
```

### Convert archived JSON to GPX

Convert one JSON file:

```bash
python scripts/json_to_gpx.py activities/<activity_id>.json
```

Convert all JSON files:

```bash
python scripts/json_to_gpx.py activities/
```

Write outputs to a directory:

```bash
python scripts/json_to_gpx.py activities/ --out gpx/
```

Behavior:

- If `streams.latlng` exists in the JSON, the script writes a **GPX track**.
- If no stream exists but `start_latlng` exists, it writes a **single waypoint** GPX.
- Otherwise, it skips the file (no location data to encode).

## GitHub Actions

This repo includes a workflow at `.github/workflows/sync.yml` that runs on a schedule.

Configure repository secrets:

- `STRAVA_CLIENT_ID`
- `STRAVA_CLIENT_SECRET`
- `STRAVA_REFRESH_TOKEN`

Optional repository variable:

- `STRAVA_DEBUG` (set to `1` to enable debug logs)
