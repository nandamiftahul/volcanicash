#!/usr/bin/env python3
"""
Download ERA5 ML + SFC for SILAM and combine into one GRIB.

Flexible version:
- Supports custom --area in N/W/S/E format, e.g. 2/99/-8/110
- Supports custom --grid in lat/lon format, e.g. 0.25/0.25 or 0.1/0.1
- Supports --case-name for output naming
- Supports --out-dir for saving GRIB files outside current folder

Run from silam-model/meteo, example:
  python3 download_era5_silam_flexible.py \
    --case-name dempo \
    --start "2026-04-15 04" \
    --end "2026-04-16 05" \
    --area "2/99/-8/110" \
    --grid "0.25/0.25"

After successful run, use in SILAM control:
  dynamic_meteo_file = GRIB ./meteo/<combined_output>.grib
  static_meteo_file  = GRIB ./meteo/<combined_output>.grib
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import cdsapi


@dataclass
class DayRequest:
    date: str                  # YYYY-MM-DD
    year: str
    month: str
    day: str
    times_hh: list[str]        # ["00", "01", ...]
    tag: str                   # YYYYMMDD_HH_HH


# Defaults: Semeru-ish domain. Can be overridden by CLI.
# ERA5 area format:
#   CDS single-level area = [North, West, South, East]
#   MARS complete area    = "North/West/South/East"
DEFAULT_AREA = "2/108/-11/116"
DEFAULT_GRID = "0.25/0.25"

ML_PARAMS = "129/130/131/132/133/135/152"  # z,t,u,v,q,w,lnsp

SFC_VARIABLES = [
    "2m_temperature",
    "2m_dewpoint_temperature",
    "10m_u_component_of_wind",
    "10m_v_component_of_wind",
    "surface_pressure",
    "mean_sea_level_pressure",
    "large_scale_precipitation",
    "convective_precipitation",
    "total_cloud_cover",
    "land_sea_mask",
    "geopotential",
    "sea_ice_cover",
    "forecast_surface_roughness",
]


def parse_utc(s: str) -> datetime:
    """Accept 'YYYY-MM-DDTHH' or 'YYYY-MM-DD HH' in UTC."""
    s = s.replace("T", " ").strip()
    try:
        return datetime.strptime(s, "%Y-%m-%d %H").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise ValueError(f"Invalid UTC time '{s}'. Use format 'YYYY-MM-DD HH', example '2026-04-15 04'.") from exc


def parse_area(area: str) -> tuple[str, list[float]]:
    """
    Parse ERA5 area argument.

    Input:
        "2/99/-8/110"  -> N/W/S/E

    Returns:
        area_mars = "2/99/-8/110"
        area_list = [2.0, 99.0, -8.0, 110.0]
    """
    raw = area.strip().replace(",", "/")
    parts = [x.strip() for x in raw.split("/") if x.strip()]

    if len(parts) != 4:
        raise ValueError("Invalid --area. Use N/W/S/E, example: --area '2/99/-8/110'")

    try:
        north, west, south, east = [float(x) for x in parts]
    except ValueError as exc:
        raise ValueError("Invalid --area. All N/W/S/E values must be numeric.") from exc

    if north <= south:
        raise ValueError(f"Invalid --area: north ({north}) must be greater than south ({south}).")
    if east <= west:
        raise ValueError(f"Invalid --area: east ({east}) must be greater than west ({west}).")
    if not (-90 <= south <= 90 and -90 <= north <= 90):
        raise ValueError("Invalid --area: latitude values must be between -90 and 90.")
    if not (-180 <= west <= 180 and -180 <= east <= 180):
        raise ValueError("Invalid --area: longitude values must be between -180 and 180.")

    area_mars = f"{north:g}/{west:g}/{south:g}/{east:g}"
    area_list = [north, west, south, east]
    return area_mars, area_list


def parse_grid(grid: str) -> tuple[str, list[float]]:
    """
    Parse ERA5 grid argument.

    Input:
        "0.25/0.25" or "0.1,0.1"

    Returns:
        grid_mars = "0.25/0.25"
        grid_list = [0.25, 0.25]
    """
    raw = grid.strip().replace(",", "/")
    parts = [x.strip() for x in raw.split("/") if x.strip()]

    if len(parts) != 2:
        raise ValueError("Invalid --grid. Use LAT/LON, example: --grid '0.25/0.25'")

    try:
        dlat, dlon = [float(x) for x in parts]
    except ValueError as exc:
        raise ValueError("Invalid --grid. Both grid values must be numeric.") from exc

    if dlat <= 0 or dlon <= 0:
        raise ValueError("Invalid --grid. Grid spacing must be positive.")
    if dlat > 2 or dlon > 2:
        raise ValueError("Invalid --grid. Grid spacing looks too coarse for SILAM; use e.g. 0.25/0.25.")

    grid_mars = f"{dlat:g}/{dlon:g}"
    grid_list = [dlat, dlon]
    return grid_mars, grid_list


def build_day_requests(start: datetime, end: datetime) -> list[DayRequest]:
    """Inclusive hourly range, grouped by date."""
    if end < start:
        raise ValueError("end must be >= start")

    hours = []
    t = start
    while t <= end:
        hours.append(t)
        t += timedelta(hours=1)

    grouped: dict[str, list[datetime]] = {}
    for h in hours:
        grouped.setdefault(h.strftime("%Y-%m-%d"), []).append(h)

    out: list[DayRequest] = []
    for date, hs in sorted(grouped.items()):
        y, m, d = date.split("-")
        times_hh = [h.strftime("%H") for h in hs]
        tag = f"{y}{m}{d}_{times_hh[0]}_{times_hh[-1]}"
        out.append(DayRequest(date=date, year=y, month=m, day=d, times_hh=times_hh, tag=tag))
    return out


def fail_if_too_recent(start: datetime, force: bool) -> None:
    # ERA5T is commonly about 5 days behind real time; use 6 days as a safer practical guard.
    now = datetime.now(timezone.utc)
    safe_latest = now - timedelta(days=6)
    if start > safe_latest and not force:
        msg = f"""
[STOP] Requested ERA5 start time is too recent for normal CDS ERA5 access.

Requested start : {start:%Y-%m-%d %H:%M UTC}
Current UTC     : {now:%Y-%m-%d %H:%M UTC}
Practical safe  : <= {safe_latest:%Y-%m-%d %H:%M UTC}

This is why CDS/MARS may return:
  AccessError: Restricted access to ERA5T

Options:
  1) Wait until this case is available in ERA5/ERA5T, then rerun this script.
  2) Rerun with --force if you know your account can access the requested recent date.
  3) For same-day operational modelling, use forecast meteo instead of ERA5.
"""
        raise SystemExit(msg)


def retrieve_ml(
    client: cdsapi.Client,
    req: DayRequest,
    out_dir: Path,
    area_mars: str,
    grid_mars: str,
    overwrite: bool = False,
) -> Path:
    out = out_dir / f"era5_ml_{req.tag}.grib"
    if out.exists() and out.stat().st_size > 0 and not overwrite:
        print(f"[ML] Exists, skip: {out}")
        return out

    if out.exists() and overwrite:
        out.unlink()

    times = [f"{hh}:00:00" for hh in req.times_hh]
    print(f"[ML] Downloading {req.date} times {times[0]}..{times[-1]}")
    client.retrieve(
        "reanalysis-era5-complete",
        {
            "class": "ea",
            "date": req.date,
            "expver": "1",
            "levtype": "ml",
            "levelist": "1/to/137",
            "param": ML_PARAMS,
            "stream": "oper",
            "time": times,
            "type": "an",
            "data_format": "grib",
            "area": area_mars,
            "grid": grid_mars,
        },
        str(out),
    )
    return out


def retrieve_sfc(
    client: cdsapi.Client,
    req: DayRequest,
    out_dir: Path,
    area_list: list[float],
    grid_list: list[float],
    overwrite: bool = False,
) -> Path:
    out = out_dir / f"era5_sfc_{req.tag}.grib"
    if out.exists() and out.stat().st_size > 0 and not overwrite:
        print(f"[SFC] Exists, skip: {out}")
        return out

    if out.exists() and overwrite:
        out.unlink()

    times = [f"{hh}:00" for hh in req.times_hh]
    print(f"[SFC] Downloading {req.date} times {times[0]}..{times[-1]}")
    client.retrieve(
        "reanalysis-era5-single-levels",
        {
            "product_type": "reanalysis",
            "variable": SFC_VARIABLES,
            "year": req.year,
            "month": req.month,
            "day": req.day,
            "time": times,
            "data_format": "grib",
            "area": area_list,
            "grid": grid_list,
        },
        str(out),
    )
    return out


def combine_gribs(files: list[Path], output: Path, overwrite: bool = False) -> None:
    if output.exists() and output.stat().st_size > 0 and not overwrite:
        print(f"[COMBINE] Exists, skip: {output}")
        print(f"[DONE] Combined GRIB already available: {output}")
        print(f"[DONE] Size: {output.stat().st_size / 1024 / 1024:.1f} MB")
        return

    if output.exists() and overwrite:
        output.unlink()

    print(f"[COMBINE] Writing {output}")
    with output.open("wb") as w:
        for f in files:
            if not f.exists() or f.stat().st_size == 0:
                raise FileNotFoundError(f"Missing or empty GRIB: {f}")
            print(f"  + {f.name} ({f.stat().st_size / 1024 / 1024:.1f} MB)")
            with f.open("rb") as r:
                while True:
                    chunk = r.read(1024 * 1024)
                    if not chunk:
                        break
                    w.write(chunk)
    print(f"[DONE] Combined GRIB: {output}")
    print(f"[DONE] Size: {output.stat().st_size / 1024 / 1024:.1f} MB")


def safe_case_name(name: str) -> str:
    name = name.strip().lower().replace(" ", "_")
    keep = []
    for ch in name:
        if ch.isalnum() or ch in "_-":
            keep.append(ch)
    return "".join(keep) or "case"


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Download ERA5 model-level + single-level GRIB for SILAM, then combine into one GRIB."
    )
    ap.add_argument("--case-name", default="semeru", help="Case name for automatic output naming, e.g. dempo, semeru, ibu")
    ap.add_argument("--start", default="2026-05-11 22", help="UTC start hour, e.g. '2026-04-15 04'")
    ap.add_argument("--end", default="2026-05-12 18", help="UTC end hour inclusive, e.g. '2026-04-16 05'")
    ap.add_argument("--area", default=DEFAULT_AREA, help="ERA5 area N/W/S/E, e.g. '2/99/-8/110'")
    ap.add_argument("--grid", default=DEFAULT_GRID, help="ERA5 grid LAT/LON, e.g. '0.25/0.25' or '0.1/0.1'")
    ap.add_argument("--out", default="", help="Output combined GRIB name. If empty, generated from case/start/end.")
    ap.add_argument("--out-dir", default=".", help="Output directory for downloaded and combined GRIB files. Default: current directory")
    ap.add_argument("--force", action="store_true", help="Try download even if date is too recent for ERA5")
    ap.add_argument("--overwrite", action="store_true", help="Re-download/re-combine even if files already exist")
    args = ap.parse_args()

    try:
        start = parse_utc(args.start)
        end = parse_utc(args.end)
        area_mars, area_list = parse_area(args.area)
        grid_mars, grid_list = parse_grid(args.grid)
        fail_if_too_recent(start, args.force)
    except Exception as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        sys.exit(2)

    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    requests = build_day_requests(start, end)
    case_name = safe_case_name(args.case_name)
    out_name = args.out or f"era5_all_{case_name}_{start:%Y%m%d_%H}_{end:%Y%m%d_%H}.grib"
    out_path = out_dir / out_name

    print("[CASE] SILAM ERA5 meteo downloader")
    print(f"[CASE] Case name : {case_name}")
    print(f"[CASE] Start UTC : {start:%Y-%m-%d %H:%M}")
    print(f"[CASE] End UTC   : {end:%Y-%m-%d %H:%M}")
    print(f"[CASE] Area      : N/W/S/E = {area_mars}")
    print(f"[CASE] Grid      : {grid_mars}")
    print(f"[CASE] Out dir   : {out_dir}")
    print(f"[CASE] Output    : {out_path.name}")

    client = cdsapi.Client()

    downloaded: list[Path] = []
    for req in requests:
        downloaded.append(retrieve_ml(client, req, out_dir, area_mars, grid_mars, overwrite=args.overwrite))
        downloaded.append(retrieve_sfc(client, req, out_dir, area_list, grid_list, overwrite=args.overwrite))

    combine_gribs(downloaded, out_path, overwrite=args.overwrite)

    print("\nUse in SILAM control:")
    print(f"dynamic_meteo_file = GRIB ./meteo/{out_path.name}")
    print(f"static_meteo_file  = GRIB ./meteo/{out_path.name}")

    print("\nQuick check commands:")
    print(f"ls -lh {out_path}")
    print(f"grib_ls -p shortName,typeOfLevel,level,date,time {out_path} | head -80")


if __name__ == "__main__":
    main()
