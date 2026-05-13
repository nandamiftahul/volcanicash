#!/usr/bin/env python3
"""
Download ERA5 model-level + single-level GRIB for SILAM volcanic ash case,
then concatenate them into one GRIB file.

Case: Semeru VA Advisory
DTG: 2026-05-11 22:50 UTC
Eruption report: 2026-05-11 22:22 UTC
Estimated VA cloud: 2026-05-11 22:30 UTC
Forecast coverage: until 2026-05-12 18:00 UTC buffer

Run this from anywhere. Output files are written to the same directory as this script.
Example:
    cd ~/Documents/00_Nanda/Delvelop/Environment/silam/silam-model/meteo
    python3 download_era5_sem_real.py
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import cdsapi

# =========================
# CASE SETTINGS
# =========================
CASE_ID = "semeru_20260511_2230"
OUT_DIR = Path(__file__).resolve().parent

# Advisory/source area: Semeru, Java, moving NW.
# Download domain must be larger than SILAM output domain for interpolation buffer.
# CDS single-level uses [N, W, S, E]
AREA_LIST = [2, 108, -11, 116]
# CDS complete/model-level MARS request uses "N/W/S/E"
AREA_STR = "2/108/-11/116"
GRID_LIST = [0.25, 0.25]
GRID_STR = "0.25/0.25"

# Request split by day to avoid downloading unnecessary hours.
REQUESTS = [
    {
        "date": "2026-05-11",
        "year": "2026",
        "month": "05",
        "day": "11",
        "times_ml": ["22:00:00", "23:00:00"],
        "times_sfc": ["22:00", "23:00"],
        "tag": "20260511_22_23",
    },
    {
        "date": "2026-05-12",
        "year": "2026",
        "month": "05",
        "day": "12",
        "times_ml": [f"{h:02d}:00:00" for h in range(0, 19)],  # 00..18 UTC
        "times_sfc": [f"{h:02d}:00" for h in range(0, 19)],
        "tag": "20260512_00_18",
    },
]

ML_PARAMS = "129/130/131/132/133/135/152"  # z/t/u/v/q/w/lnsp on model levels
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

FINAL_GRIB = OUT_DIR / f"era5_all_{CASE_ID}_22_18.grib"


def retrieve_ml(c: cdsapi.Client, req: dict) -> Path:
    out = OUT_DIR / f"era5_ml_{req['tag']}.grib"
    if out.exists() and out.stat().st_size > 0:
        print(f"[SKIP] Existing ML file: {out.name}")
        return out

    print(f"[ML] Downloading {req['date']} times {req['times_ml'][0]}..{req['times_ml'][-1]}")
    c.retrieve(
        "reanalysis-era5-complete",
        {
            "class": "ea",
            "date": req["date"],
            "expver": "1",
            "levtype": "ml",
            "levelist": "1/to/137",
            "param": ML_PARAMS,
            "stream": "oper",
            "time": req["times_ml"],
            "type": "an",
            "data_format": "grib",
            "area": AREA_STR,
            "grid": GRID_STR,
        },
        str(out),
    )
    return out


def retrieve_sfc(c: cdsapi.Client, req: dict) -> Path:
    out = OUT_DIR / f"era5_sfc_{req['tag']}.grib"
    if out.exists() and out.stat().st_size > 0:
        print(f"[SKIP] Existing SFC file: {out.name}")
        return out

    print(f"[SFC] Downloading {req['date']} times {req['times_sfc'][0]}..{req['times_sfc'][-1]}")
    c.retrieve(
        "reanalysis-era5-single-levels",
        {
            "product_type": "reanalysis",
            "variable": SFC_VARIABLES,
            "year": req["year"],
            "month": req["month"],
            "day": req["day"],
            "time": req["times_sfc"],
            "data_format": "grib",
            "area": AREA_LIST,
            "grid": GRID_LIST,
        },
        str(out),
    )
    return out


def concatenate_gribs(parts: list[Path], final_file: Path) -> None:
    print("[CAT] Creating merged SILAM meteo file:", final_file.name)
    with final_file.open("wb") as fout:
        for p in parts:
            print("      +", p.name)
            with p.open("rb") as fin:
                subprocess.run(["cat"], stdin=fin, stdout=fout, check=True)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    c = cdsapi.Client()

    ml_files: list[Path] = []
    sfc_files: list[Path] = []

    for req in REQUESTS:
        ml_files.append(retrieve_ml(c, req))
        sfc_files.append(retrieve_sfc(c, req))

    # Keep all model-level fields first, then all surface fields.
    concatenate_gribs(ml_files + sfc_files, FINAL_GRIB)

    print("\n[DONE]")
    print("Final merged file:", FINAL_GRIB)
    print("Use in SILAM control:")
    print(f"dynamic_meteo_file = GRIB ./meteo/{FINAL_GRIB.name}")
    print(f"static_meteo_file  = GRIB ./meteo/{FINAL_GRIB.name}")
    print("\nCheck:")
    print(f"grib_ls -p shortName,typeOfLevel,level {FINAL_GRIB.name} | grep -E '^(t|q|u|v|w|lnsp|2t|2d|10u|10v|sp|msl|lsp|cp|tcc|lsm|ci|fsr|z) '")


if __name__ == "__main__":
    main()
