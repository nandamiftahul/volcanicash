#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Find VAAC advisories that match SILAM NetCDF time range.

Contoh:
    python3 meteo/get_vaac_for_nc_time.py \
      --nc ./output/semeru_ash_20260506_1000_test/ash_output.nc \
      --volcano SEMERU \
      --url "https://www.volcanodiscovery.com/semeru/news/301626/vaac-advisory-2026-515.html" \
      --json ./output/semeru_ash_20260506_1000_test/vaac_matched.json \
      --geojson ./output/semeru_ash_20260506_1000_test/vaac_matched.geojson

Bisa pakai beberapa --url sekaligus.
"""

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scipy.io import netcdf_file

# Import function dari parser kamu
from parse_darwin_vaac import (
    fetch_bom_text,
    split_vaa_blocks,
    parse_advisory_block,
    filter_advisories,
    save_json,
    save_geojson,
)


def parse_iso(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None


def read_nc_time_range(nc_path):
    nc_path = Path(nc_path)

    if not nc_path.exists():
        raise FileNotFoundError(f"NetCDF tidak ditemukan: {nc_path}")

    f = netcdf_file(nc_path, "r", mmap=False)
    time_values = f.variables["time"].data.copy()
    units = getattr(f.variables["time"], "units", "")

    if isinstance(units, bytes):
        units = units.decode("utf-8", errors="ignore")

    f.close()

    if "since" not in units:
        raise RuntimeError(f"Format time units tidak dikenali: {units}")

    base_str = units.split("since", 1)[1].replace("UTC", "").strip()
    base_dt = datetime.strptime(base_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)

    times = [base_dt + timedelta(seconds=float(t)) for t in time_values]

    return min(times), max(times), times


def advisory_time(item):
    """
    Prioritas:
    1. main_va_dtg_iso = waktu observed/estimated ash cloud
    2. dtg_iso = waktu advisory issued
    """
    return parse_iso(item.get("main_va_dtg_iso")) or parse_iso(item.get("dtg_iso"))


def fetch_and_parse_url(url):
    text = fetch_bom_text(url)
    blocks = split_vaa_blocks(text)

    # Fallback: kalau halaman hanya berisi satu VA ADVISORY tanpa format Received yang rapi
    if not blocks and "VA ADVISORY" in text:
        blocks = [text]

    advisories = [parse_advisory_block(block) for block in blocks]
    return advisories


def main():
    parser = argparse.ArgumentParser(
        description="Find VAAC advisories matching SILAM NetCDF time range."
    )

    parser.add_argument("--nc", required=True, help="Path ash_output.nc")
    parser.add_argument("--volcano", default="SEMERU", help="Volcano name filter")
    parser.add_argument("--vaac", default="DARWIN", help="VAAC filter")
    parser.add_argument("--area", default=None, help="Area filter, optional")
    parser.add_argument(
        "--url",
        action="append",
        required=True,
        help="VAAC/advisory URL. Bisa dipakai beberapa kali.",
    )
    parser.add_argument(
        "--tolerance-hours",
        type=float,
        default=0.0,
        help="Tambahan toleransi jam sebelum/sesudah NC range. Default 0.",
    )
    parser.add_argument("--json", default=None, help="Output matched JSON")
    parser.add_argument("--geojson", default=None, help="Output matched GeoJSON")

    args = parser.parse_args()

    nc_start, nc_end, nc_times = read_nc_time_range(args.nc)

    tol = timedelta(hours=args.tolerance_hours)
    match_start = nc_start - tol
    match_end = nc_end + tol

    print("=" * 80)
    print("SILAM NetCDF time range")
    print("NC start     :", nc_start.isoformat().replace("+00:00", "Z"))
    print("NC end       :", nc_end.isoformat().replace("+00:00", "Z"))
    print("Match start  :", match_start.isoformat().replace("+00:00", "Z"))
    print("Match end    :", match_end.isoformat().replace("+00:00", "Z"))
    print("=" * 80)

    all_items = []

    for url in args.url:
        print(f"[FETCH] {url}")
        try:
            items = fetch_and_parse_url(url)
            all_items.extend(items)
        except Exception as e:
            print(f"[WARN] gagal parse {url}: {e}", file=sys.stderr)

    selected = filter_advisories(
        all_items,
        vaac=args.vaac,
        volcano=args.volcano,
        area=args.area,
    )

    matched = []
    print()
    print("Candidate advisories:")
    print("-" * 80)

    for item in selected:
        t = advisory_time(item)
        t_str = t.isoformat().replace("+00:00", "Z") if t else "UNKNOWN"
        ok = bool(t and match_start <= t <= match_end)

        print(
            f"{'MATCH' if ok else 'SKIP '} | "
            f"{item.get('volcano')} | "
            f"adv={item.get('advisory_nr')} | "
            f"dtg={item.get('dtg_iso')} | "
            f"main={item.get('main_va_dtg_iso')} | "
            f"use_time={t_str}"
        )

        if ok:
            matched.append(item)

    print("-" * 80)
    print(f"[RESULT] matched advisories: {len(matched)}")

    if args.json:
        save_json(args.json, matched)
        print(f"[SAVE] JSON    : {args.json}")

    if args.geojson:
        save_geojson(args.geojson, matched)
        print(f"[SAVE] GeoJSON : {args.geojson}")


if __name__ == "__main__":
    main()
