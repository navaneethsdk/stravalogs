#!/usr/bin/env python3
"""
strava_sync.py — Automated Strava activity archiver
----------------------------------------------------
Fetches new activities from the Strava API and saves them to an /activities
directory. GPS activities are saved as GPX files; manual/stationary activities
are saved as JSON metadata files.

Setup
-----
Set these environment variables (or store them in a .env file):
    STRAVA_CLIENT_ID      — from https://www.strava.com/settings/api
    STRAVA_CLIENT_SECRET  — from https://www.strava.com/settings/api
    STRAVA_REFRESH_TOKEN  — obtained via OAuth flow (see README below)

Usage
-----
    pip install requests
    python strava_sync.py

For GitHub Actions, store all three values as repository secrets and call this
script in your workflow YAML. The script exits with code 0 whether or not there
are new activities, so the workflow always succeeds.
"""

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

CLIENT_ID     = os.environ.get("STRAVA_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("STRAVA_CLIENT_SECRET", "")
REFRESH_TOKEN = os.environ.get("STRAVA_REFRESH_TOKEN", "")

ACTIVITIES_DIR = Path("activities")
TOKEN_CACHE    = Path(".strava_token_cache.json")

STRAVA_AUTH_URL = "https://www.strava.com/oauth/token"
STRAVA_API_BASE = "https://www.strava.com/api/v3"

DEBUG = os.environ.get("STRAVA_DEBUG", "").strip().lower() in {"1", "true", "yes", "on"}


def debug(msg: str) -> None:
    if DEBUG:
        print(f"[debug] {msg}")


# Activity types that carry GPS data and can be exported as GPX
GPS_TYPES = {
    "Run", "Ride", "Swim", "Walk", "Hike", "AlpineSki", "BackcountrySki",
    "Canoeing", "Crossfit", "EBikeRide", "Elliptical", "Golf", "Handcycle",
    "IceSkate", "InlineSkate", "Kayaking", "Kitesurf", "NordicSki",
    "RockClimbing", "RollerSki", "Rowing", "Snowboard", "Snowshoe",
    "Soccer", "StairStepper", "StandUpPaddling", "Surfing",
    "VirtualRide", "VirtualRun", "Velomobile", "Wheelchair", "Windsurf",
}

# ---------------------------------------------------------------------------
# OAuth — automatic token refresh
# ---------------------------------------------------------------------------

def load_cached_token() -> dict:
    if TOKEN_CACHE.exists():
        return json.loads(TOKEN_CACHE.read_text())
    return {}


def save_cached_token(data: dict) -> None:
    TOKEN_CACHE.write_text(json.dumps(data, indent=2))


def clear_cached_token() -> None:
    try:
        TOKEN_CACHE.unlink()
        debug(f"Deleted token cache file {TOKEN_CACHE}")
    except FileNotFoundError:
        return


def get_access_token() -> str:
    """Return a valid access token, refreshing if necessary."""
    cached = load_cached_token()

    # Reuse if still valid (with 60-second buffer)
    if cached.get("access_token") and cached.get("expires_at", 0) > time.time() + 60:
        debug(f"Using cached access token; expires_at={cached.get('expires_at')}")
        return cached["access_token"]

    if not all([CLIENT_ID, CLIENT_SECRET, REFRESH_TOKEN]):
        sys.exit(
            "ERROR: Set STRAVA_CLIENT_ID, STRAVA_CLIENT_SECRET, and "
            "STRAVA_REFRESH_TOKEN environment variables."
        )

    print("Refreshing Strava access token…")
    resp = requests.post(
        STRAVA_AUTH_URL,
        data={
            "client_id":     CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "grant_type":    "refresh_token",
            "refresh_token": REFRESH_TOKEN,
        },
        timeout=15,
    )
    resp.raise_for_status()
    token_data = resp.json()
    save_cached_token(token_data)
    debug(
        "Token refresh OK. "
        f"expires_at={token_data.get('expires_at')} "
        f"scope={token_data.get('scope')!r}"
    )
    athlete = token_data.get("athlete") or {}
    if athlete:
        debug(
            "Athlete from refresh response: "
            f"id={athlete.get('id')} username={athlete.get('username')}"
        )

    # If Strava rotated the refresh token, print it so the caller can update
    # the secret (this rarely happens but is worth surfacing).
    if token_data.get("refresh_token") != REFRESH_TOKEN:
        print(
            f"⚠️  Refresh token rotated — update STRAVA_REFRESH_TOKEN to:\n"
            f"    {token_data['refresh_token']}"
        )

    return token_data["access_token"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def strava_get(path: str, token: str, params: dict | None = None) -> dict | list:
    """GET wrapper with basic rate-limit handling."""
    url = f"{STRAVA_API_BASE}{path}"
    for attempt in range(3):
        resp = requests.get(
            url,
            headers={"Authorization": f"Bearer {token}"},
            params=params or {},
            timeout=20,
        )
        debug(f"GET {resp.request.url} → {resp.status_code}")
        if resp.status_code == 429:
            wait = int(resp.headers.get("X-RateLimit-Reset", 60))
            print(f"Rate limited — waiting {wait}s…")
            time.sleep(wait)
            continue
        if resp.status_code >= 400:
            body = ""
            try:
                body = (resp.text or "").strip()
            except Exception:
                body = ""
            if body:
                debug(f"Error body (first 800 chars): {body[:800]}")
        resp.raise_for_status()
        return resp.json()
    raise RuntimeError(f"Failed after retries: {url}")


def existing_ids() -> set[int]:
    """Return the set of activity IDs already saved to disk."""
    ids = set()
    for p in ACTIVITIES_DIR.glob("*"):
        # Files are named <id>.gpx or <id>.json
        try:
            ids.add(int(p.stem))
        except ValueError:
            pass
    return ids


def last_activity_timestamp() -> int:
    """
    Return the Unix timestamp of the most recent saved activity, or 0.
    Used as the `after` parameter so we don't re-fetch the full history.
    """
    timestamps = []
    for p in ACTIVITIES_DIR.glob("*.json"):
        try:
            data = json.loads(p.read_text())
            ts = data.get("start_date_unix", 0)
            if ts:
                timestamps.append(ts)
        except (json.JSONDecodeError, KeyError):
            pass
    return max(timestamps, default=0)


# ---------------------------------------------------------------------------
# Fetching
# ---------------------------------------------------------------------------

def fetch_activities(token: str, after: int = 0) -> list[dict]:
    """Fetch all activities since `after` (Unix timestamp), paginated."""
    activities = []
    page = 1
    while True:
        batch = strava_get(
            "/athlete/activities",
            token,
            params={"per_page": 100, "page": page, "after": after},
        )
        if not batch:
            break
        activities.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    return activities


def fetch_gpx(activity_id: int, token: str) -> bytes | None:
    """Download the GPX export for a GPS activity. Returns None if unavailable."""
    url = f"{STRAVA_API_BASE}/activities/{activity_id}/export_gpx"
    resp = requests.get(
        url,
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )
    if resp.status_code == 404:
        return None  # Activity has no GPS track (e.g. manual entry)
    resp.raise_for_status()
    return resp.content


def fetch_streams(activity_id: int, token: str) -> dict:
    """
    Fetch detailed streams (latlng, altitude, heartrate, cadence, watts).
    Used as a fallback if GPX export is unavailable.
    """
    keys = "latlng,altitude,heartrate,cadence,watts,velocity_smooth"
    try:
        return strava_get(
            f"/activities/{activity_id}/streams",
            token,
            params={"keys": keys, "key_by_type": True},
        )
    except requests.HTTPError:
        return {}


# ---------------------------------------------------------------------------
# Saving
# ---------------------------------------------------------------------------

def save_gps_activity(activity: dict, token: str) -> Path:
    """Save a GPS-bearing activity as a .gpx file (fallback: streams JSON)."""
    aid = activity["id"]
    gpx_path = ACTIVITIES_DIR / f"{aid}.gpx"

    gpx_bytes = fetch_gpx(aid, token)
    if gpx_bytes:
        gpx_path.write_bytes(gpx_bytes)
        print(f"  Saved GPX  → {gpx_path}")
        return gpx_path

    # Strava sometimes returns 404 for GPX even on GPS activities
    # (e.g. activity was manually edited or has no valid track).
    # Fall back to streams + metadata JSON.
    print(f"  GPX unavailable for {aid}, saving streams JSON instead")
    streams = fetch_streams(aid, token)
    payload = {**_metadata(activity), "streams": streams}
    json_path = ACTIVITIES_DIR / f"{aid}.json"
    json_path.write_text(json.dumps(payload, indent=2))
    print(f"  Saved JSON → {json_path}")
    return json_path


def save_manual_activity(activity: dict) -> Path:
    """Save a non-GPS activity as a metadata JSON file."""
    aid = activity["id"]
    json_path = ACTIVITIES_DIR / f"{aid}.json"
    json_path.write_text(json.dumps(_metadata(activity), indent=2))
    print(f"  Saved JSON → {json_path}")
    return json_path


def _metadata(activity: dict) -> dict:
    """Extract a clean metadata dict from a raw Strava activity object."""
    return {
        "id":                activity["id"],
        "name":              activity.get("name", ""),
        "type":              activity.get("type", ""),
        "sport_type":        activity.get("sport_type", ""),
        "start_date":        activity.get("start_date", ""),
        "start_date_local":  activity.get("start_date_local", ""),
        "start_date_unix":   _iso_to_unix(activity.get("start_date", "")),
        "description":       activity.get("description") or "",
        "distance_m":        activity.get("distance", 0),
        "moving_time_s":     activity.get("moving_time", 0),
        "elapsed_time_s":    activity.get("elapsed_time", 0),
        "total_elevation_m": activity.get("total_elevation_gain", 0),
        "average_heartrate": activity.get("average_heartrate"),
        "max_heartrate":     activity.get("max_heartrate"),
        "average_watts":     activity.get("average_watts"),
        "kilojoules":        activity.get("kilojoules"),
        "average_speed_ms":  activity.get("average_speed", 0),
        "max_speed_ms":      activity.get("max_speed", 0),
        "calories":          activity.get("calories"),
        "suffer_score":      activity.get("suffer_score"),
        "trainer":           activity.get("trainer", False),
        "commute":           activity.get("commute", False),
        "manual":            activity.get("manual", False),
        "gear_id":           activity.get("gear_id"),
    }


def _iso_to_unix(iso: str) -> int:
    """Convert an ISO 8601 timestamp string to a Unix integer."""
    if not iso:
        return 0
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return int(dt.timestamp())
    except ValueError:
        return 0


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    print("=== Strava Sync ===")
    ACTIVITIES_DIR.mkdir(exist_ok=True)

    token   = get_access_token()
    seen    = existing_ids()
    after   = last_activity_timestamp()

    print(f"Fetching activities after {datetime.fromtimestamp(after, tz=timezone.utc).isoformat() if after else 'the beginning'}…")
    try:
        activities = fetch_activities(token, after=after)
    except requests.HTTPError as exc:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        if status == 401:
            detail = ""
            try:
                detail = (exc.response.text or "").strip()
            except Exception:
                pass

            print("ERROR: Strava API returned 401 Unauthorized.")
            if detail:
                print(f"Response: {detail}")
            print(
                "This usually means STRAVA_CLIENT_ID/SECRET/REFRESH_TOKEN don't belong together, "
                "the athlete revoked access, or the app lacks the required scopes (often `activity:read_all`)."
            )
            print("Retrying once with a freshly refreshed token (clearing local token cache)…")

            clear_cached_token()
            token = get_access_token()
            activities = fetch_activities(token, after=after)
        else:
            raise
    print(f"Found {len(activities)} activit{'y' if len(activities) == 1 else 'ies'} from Strava")

    new_activities = [a for a in activities if a["id"] not in seen]
    print(f"{len(new_activities)} new (not yet archived)")

    if not new_activities:
        print("Nothing to do — repository is up to date.")
        return

    saved_count = 0
    for activity in sorted(new_activities, key=lambda a: a.get("start_date", "")):
        aid  = activity["id"]
        name = activity.get("name", "Unnamed")
        kind = activity.get("type", "Unknown")
        print(f"\n→ [{kind}] {name} (id={aid})")

        try:
            if kind in GPS_TYPES:
                save_gps_activity(activity, token)
            else:
                save_manual_activity(activity)
            saved_count += 1
        except Exception as exc:
            print(f"  ERROR saving {aid}: {exc}")

    print(f"\n✓ Archived {saved_count} new activit{'y' if saved_count == 1 else 'ies'} to {ACTIVITIES_DIR}/")


if __name__ == "__main__":
    main()