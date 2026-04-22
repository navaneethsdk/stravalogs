#!/usr/bin/env python3
"""
json_to_gpx.py — Convert archived Strava JSON files into GPX.

Supports JSON produced by `scripts/sync_strava.py`:
- If `streams.latlng` exists, emits a GPX track (trk/trkseg/trkpt).
- If no stream latlng exists but `start_latlng` exists, emits a GPX waypoint.

Usage:
  python scripts/json_to_gpx.py activities/123.json
  python scripts/json_to_gpx.py activities/            # converts all *.json
  python scripts/json_to_gpx.py activities/ --out gpx/ # writes to directory
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from xml.etree import ElementTree as ET


GPX_NS = "http://www.topografix.com/GPX/1/1"


def _iso_to_dt(iso: str) -> datetime | None:
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return None


def _dt_to_gpx_time(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _as_stream_data(streams: dict, key: str) -> list | None:
    """
    Strava `key_by_type=true` streams look like:
      { "latlng": { "data": [...] }, "time": { "data": [...] }, ... }
    """
    if not isinstance(streams, dict):
        return None
    obj = streams.get(key)
    if not isinstance(obj, dict):
        return None
    data = obj.get("data")
    return data if isinstance(data, list) else None


def build_gpx(activity: dict) -> ET.Element:
    ET.register_namespace("", GPX_NS)

    gpx = ET.Element(
        f"{{{GPX_NS}}}gpx",
        {
            "version": "1.1",
            "creator": "stravalogs json_to_gpx.py",
        },
    )

    name = (activity.get("name") or "").strip()
    desc = (activity.get("description") or "").strip()
    start_dt = _iso_to_dt(activity.get("start_date") or "")

    metadata = ET.SubElement(gpx, f"{{{GPX_NS}}}metadata")
    if name:
        ET.SubElement(metadata, f"{{{GPX_NS}}}name").text = name
    if desc:
        ET.SubElement(metadata, f"{{{GPX_NS}}}desc").text = desc
    if start_dt:
        ET.SubElement(metadata, f"{{{GPX_NS}}}time").text = _dt_to_gpx_time(start_dt)

    streams = activity.get("streams") or {}
    latlng = _as_stream_data(streams, "latlng")
    alt = _as_stream_data(streams, "altitude")
    rel_time = _as_stream_data(streams, "time")

    if latlng:
        trk = ET.SubElement(gpx, f"{{{GPX_NS}}}trk")
        if name:
            ET.SubElement(trk, f"{{{GPX_NS}}}name").text = name
        if desc:
            ET.SubElement(trk, f"{{{GPX_NS}}}desc").text = desc

        trkseg = ET.SubElement(trk, f"{{{GPX_NS}}}trkseg")

        for i, pair in enumerate(latlng):
            if not isinstance(pair, list) or len(pair) != 2:
                continue
            lat, lon = pair
            if lat is None or lon is None:
                continue

            pt = ET.SubElement(
                trkseg,
                f"{{{GPX_NS}}}trkpt",
                {"lat": str(lat), "lon": str(lon)},
            )

            if isinstance(alt, list) and i < len(alt) and alt[i] is not None:
                ET.SubElement(pt, f"{{{GPX_NS}}}ele").text = str(alt[i])

            if start_dt and isinstance(rel_time, list) and i < len(rel_time):
                try:
                    seconds = float(rel_time[i])
                except (TypeError, ValueError):
                    seconds = None
                if seconds is not None:
                    ET.SubElement(pt, f"{{{GPX_NS}}}time").text = _dt_to_gpx_time(
                        start_dt + timedelta(seconds=seconds)
                    )

        return gpx

    start_latlng = activity.get("start_latlng")
    if isinstance(start_latlng, list) and len(start_latlng) == 2:
        lat, lon = start_latlng
        wpt = ET.SubElement(
            gpx,
            f"{{{GPX_NS}}}wpt",
            {"lat": str(lat), "lon": str(lon)},
        )
        if name:
            ET.SubElement(wpt, f"{{{GPX_NS}}}name").text = name
        if desc:
            ET.SubElement(wpt, f"{{{GPX_NS}}}desc").text = desc
        if start_dt:
            ET.SubElement(wpt, f"{{{GPX_NS}}}time").text = _dt_to_gpx_time(start_dt)
        return gpx

    raise ValueError("No GPS streams.latlng and no start_latlng; cannot build GPX.")


def write_gpx(activity_json_path: Path, out_path: Path) -> None:
    activity = json.loads(activity_json_path.read_text())
    gpx = build_gpx(activity)
    tree = ET.ElementTree(gpx)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tree.write(out_path, encoding="utf-8", xml_declaration=True)


def iter_input_files(path: Path) -> list[Path]:
    if path.is_dir():
        return sorted(p for p in path.glob("*.json") if p.is_file())
    return [path]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path, help="A JSON file or a directory of JSON files")
    parser.add_argument("--out", type=Path, default=None, help="Output directory (default: alongside JSON)")
    args = parser.parse_args()

    inputs = iter_input_files(args.path)
    if not inputs:
        print("No JSON files found.")
        return 0

    converted = 0
    skipped = 0
    for p in inputs:
        try:
            if args.out:
                out_path = args.out / f"{p.stem}.gpx"
            else:
                out_path = p.with_suffix(".gpx")
            write_gpx(p, out_path)
            print(f"✓ {p} → {out_path}")
            converted += 1
        except Exception as exc:
            print(f"↷ Skipping {p}: {exc}")
            skipped += 1

    print(f"Done. Converted={converted} Skipped={skipped}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

