#!/usr/bin/env python3
from __future__ import annotations

import argparse
import math
import shutil
import subprocess
from collections import defaultdict
from pathlib import Path

import numpy as np
from eccodes import (
    codes_grib_new_from_file,
    codes_get,
    codes_get_double_array,
    codes_set,
    codes_set_values,
    codes_write,
    codes_release,
)

# ECMWF paramIds converted from pressure-level to hybrid-level
HYBRID_3D_PARAMS = {
    129: "z",     # geopotential
    130: "t",     # temperature
    131: "u",     # u wind
    132: "v",     # v wind
    133: "q",     # specific humidity
    135: "w",     # vertical velocity / omega
}

# Realtime surface params to remap and append from pressure GRIB.
# This includes basic surface meteo used by SILAM.
SURFACE_COPY_PARAMS = {
    129, 134, 151, 165, 166, 167, 168, 142, 143, 164,
    172, 31, 244, 228, 34, 235, 186, 187, 188,
    39, 40, 41, 42, 139, 170, 183, 236,
}

# Static fields that SILAM often needs for physiography.
# lsm -> land_fract, z -> relief/orography, fsr -> surface roughness
STATIC_SHORTNAMES = ["lsm", "z", "fsr"]


def run(cmd: list[str]) -> None:
    print("[CMD]", " ".join(str(c) for c in cmd))
    subprocess.run(cmd, check=True)


def require_tool(name: str) -> None:
    if shutil.which(name) is None:
        raise RuntimeError(f"Tool '{name}' tidak ditemukan di PATH. Install dulu tool tersebut.")


def bbox_from_center_radius(lat: float, lon: float, radius_km: float) -> tuple[float, float, float, float]:
    """Return west, east, south, north for CDO sellonlatbox."""
    dlat = radius_km / 111.32
    coslat = max(math.cos(math.radians(lat)), 0.1)
    dlon = radius_km / (111.32 * coslat)
    west = lon - dlon
    east = lon + dlon
    south = lat - dlat
    north = lat + dlat
    return west, east, south, north


def cdo_crop(input_file: Path, output_file: Path, west: float, east: float, south: float, north: float) -> None:
    require_tool("cdo")
    output_file.parent.mkdir(parents=True, exist_ok=True)
    sellonlatbox = f"sellonlatbox,{west:.6f},{east:.6f},{south:.6f},{north:.6f}"
    run(["cdo", "-O", sellonlatbox, str(input_file), str(output_file)])


def cdo_remap_to_target(input_file: Path, target_grid_file: Path, output_file: Path, method: str = "remapbil") -> None:
    require_tool("cdo")
    output_file.parent.mkdir(parents=True, exist_ok=True)
    run(["cdo", "-O", f"{method},{target_grid_file}", str(input_file), str(output_file)])


def append_file(out_path: Path, extra_path: Path, label: str = "APPEND") -> None:
    if not extra_path.exists() or extra_path.stat().st_size == 0:
        print(f"[{label}][WARN] skip empty/missing file: {extra_path}")
        return
    with out_path.open("ab") as w, extra_path.open("rb") as r:
        w.write(r.read())
    print(f"[{label}] appended {extra_path.name} -> {out_path.name}")


def safe_get(gid, key, default=None):
    try:
        return codes_get(gid, key)
    except Exception:
        return default


def read_messages(path: Path):
    messages = []
    with path.open("rb") as f:
        while True:
            gid = codes_grib_new_from_file(f)
            if gid is None:
                break
            try:
                param_id = int(safe_get(gid, "paramId", -999))
                short_name = str(safe_get(gid, "shortName", ""))
                level_type = str(safe_get(gid, "typeOfLevel", ""))
                level = int(float(safe_get(gid, "level", 0)))
                values = np.asarray(codes_get_double_array(gid, "values"), dtype=np.float64)
                messages.append({
                    "gid": gid,
                    "paramId": param_id,
                    "shortName": short_name,
                    "typeOfLevel": level_type,
                    "level": level,
                    "values": values,
                    "dataDate": safe_get(gid, "dataDate", None),
                    "dataTime": safe_get(gid, "dataTime", None),
                    "stepRange": safe_get(gid, "stepRange", None),
                    "validityDate": safe_get(gid, "validityDate", None),
                    "validityTime": safe_get(gid, "validityTime", None),
                })
            except Exception as e:
                print(f"[WARN] skip unreadable message in {path}: {e}")
                codes_release(gid)
    return messages


def release_messages(messages) -> None:
    for m in messages:
        try:
            codes_release(m["gid"])
        except Exception:
            pass


def build_pressure_cube(messages):
    fields = defaultdict(dict)
    surface = {}
    for m in messages:
        pid = m["paramId"]
        lt = m["typeOfLevel"]
        lvl = m["level"]
        if lt == "isobaricInhPa":
            fields[pid][lvl] = m["values"]
        elif lt == "surface":
            surface[pid] = m["values"]
    return fields, surface


def get_template_hybrid_messages(template_messages):
    allowed = set(HYBRID_3D_PARAMS.keys()) | {152}
    out = []
    for m in template_messages:
        if m["typeOfLevel"] == "hybrid" and m["paramId"] in allowed:
            out.append(m)
    return out


def get_pv_from_template_message(template_messages):
    """
    Extract ECMWF hybrid coefficients from a real hybrid GRIB template.
    ecCodes key 'pv' normally contains A half-levels then B half-levels.
    """
    for m in template_messages:
        if m["typeOfLevel"] == "hybrid":
            pv = np.asarray(codes_get_double_array(m["gid"], "pv"), dtype=np.float64)
            if len(pv) >= 4 and len(pv) % 2 == 0:
                n = len(pv) // 2
                return pv[:n], pv[n:]
    raise RuntimeError("Tidak menemukan hybrid coefficient pv di template hybrid GRIB.")


def infer_full_level_pressure_pa(a_half, b_half, sp_values, hybrid_level):
    """Approximate full-level pressure from neighbouring half levels."""
    k = int(hybrid_level) - 1
    if k < 0 or k + 1 >= len(a_half):
        raise ValueError(f"Hybrid level {hybrid_level} out of range")
    p1 = a_half[k] + b_half[k] * sp_values
    p2 = a_half[k + 1] + b_half[k + 1] * sp_values
    return 0.5 * (p1 + p2)


def logp_interp_to_target(levels_hpa, values_by_level, target_p_pa):
    """
    Log-pressure interpolation from pressure-level fields to target hybrid pressure.
    Values outside available pressure range use nearest available source level.
    """
    levels_pa = np.asarray([lvl * 100.0 for lvl in levels_hpa], dtype=np.float64)
    order = np.argsort(levels_pa)
    xp = np.log(levels_pa[order])
    stacked = np.vstack([values_by_level[levels_hpa[i]] for i in order])

    clipped = np.clip(target_p_pa, np.min(levels_pa), np.max(levels_pa))
    target_x = np.log(clipped)

    nlev, npts = stacked.shape
    out = np.empty(npts, dtype=np.float64)
    for j in range(npts):
        out[j] = np.interp(target_x[j], xp, stacked[:, j])
    return out


def extract_target_hybrid_grid(template_hybrid_crop: Path, target_grid: Path) -> None:
    """Create a one-message target grid file from hybrid template, used by CDO remap."""
    require_tool("grib_copy")
    target_grid.parent.mkdir(parents=True, exist_ok=True)
    if target_grid.exists():
        target_grid.unlink()

    # Prefer temperature at hybrid level 1. If not available, any hybrid message is fine.
    try:
        run([
            "grib_copy",
            "-w", "shortName=t,typeOfLevel=hybrid,level=1",
            str(template_hybrid_crop),
            str(target_grid),
        ])
    except subprocess.CalledProcessError:
        run([
            "grib_copy",
            "-w", "typeOfLevel=hybrid",
            str(template_hybrid_crop),
            str(target_grid),
        ])

    if not target_grid.exists() or target_grid.stat().st_size == 0:
        raise RuntimeError("Gagal membuat target hybrid grid file.")


def convert_pressure_to_hybrid(pressure_path: Path, template_path: Path, out_path: Path) -> None:
    """
    Write only hybrid 3D + lnsp hybrid fields.
    Surface fields are appended later after remapping to hybrid target grid.
    """
    print(f"[READ] pressure GRIB: {pressure_path}")
    pressure_msgs = read_messages(pressure_path)
    pressure_fields, pressure_surface = build_pressure_cube(pressure_msgs)

    print(f"[READ] template hybrid GRIB: {template_path}")
    template_msgs = read_messages(template_path)

    a_half, b_half = get_pv_from_template_message(template_msgs)
    print(f"[OK] hybrid coefficients half-levels: {len(a_half)}")

    if 134 not in pressure_surface:
        release_messages(pressure_msgs)
        release_messages(template_msgs)
        raise RuntimeError("Surface pressure paramId=134 tidak ditemukan di pressure GRIB.")

    sp = pressure_surface[134]
    lnsp = np.log(np.maximum(sp, 1.0))
    hybrid_msgs = get_template_hybrid_messages(template_msgs)
    if not hybrid_msgs:
        release_messages(pressure_msgs)
        release_messages(template_msgs)
        raise RuntimeError("Tidak ada message hybrid target di template.")

    print(f"[WRITE] {out_path}")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    count_written = 0
    count_interpolated = 0
    count_lnsp = 0

    with out_path.open("wb") as w:
        for m in hybrid_msgs:
            pid = m["paramId"]
            level = m["level"]
            gid = m["gid"]

            if pid == 152:
                new_values = lnsp
                count_lnsp += 1
            elif pid in HYBRID_3D_PARAMS:
                if pid not in pressure_fields:
                    print(f"[WARN] paramId {pid} tidak ada di pressure-level. Skip level {level}.")
                    continue
                levels_hpa = sorted(pressure_fields[pid].keys())
                target_p_pa = infer_full_level_pressure_pa(a_half, b_half, sp, level)
                new_values = logp_interp_to_target(levels_hpa, pressure_fields[pid], target_p_pa)
                count_interpolated += 1
            else:
                continue

            first = pressure_msgs[0]
            try:
                if first["dataDate"] is not None:
                    codes_set(gid, "dataDate", int(first["dataDate"]))
                if first["dataTime"] is not None:
                    codes_set(gid, "dataTime", int(first["dataTime"]))
                if first["stepRange"] is not None:
                    codes_set(gid, "stepRange", str(first["stepRange"]))
            except Exception as e:
                print(f"[WARN] gagal set date/time/step: {e}")

            codes_set_values(gid, new_values)
            codes_write(gid, w)
            count_written += 1

    print("[SURFACE] direct surface copy skipped; will remap surface/static fields after hybrid write")
    print(f"[DONE] wrote hybrid messages        : {count_written}")
    print(f"[DONE] interpolated hybrid messages: {count_interpolated}")
    print(f"[DONE] lnsp hybrid messages        : {count_lnsp}")
    print(f"[DONE] output                      : {out_path}")

    release_messages(pressure_msgs)
    release_messages(template_msgs)


def append_realtime_surface_remapped(pressure_crop: Path, target_grid: Path, out_path: Path, crop_dir: Path) -> None:
    """
    Extract all surface fields from realtime pressure GRIB, remap them to hybrid target grid,
    then append to output. This avoids mixed 0.1/0.25 grids inside final GRIB.
    """
    require_tool("grib_copy")
    surf_dir = crop_dir / "surface_remap"
    surf_dir.mkdir(parents=True, exist_ok=True)
    raw = surf_dir / "realtime_surface_raw.grib"
    remap = surf_dir / "realtime_surface_remap.grib"

    for f in (raw, remap):
        try:
            f.unlink()
        except FileNotFoundError:
            pass

    run(["grib_copy", "-w", "typeOfLevel=surface", str(pressure_crop), str(raw)])

    if not raw.exists() or raw.stat().st_size == 0:
        print("[SURFACE][WARN] no realtime surface fields found")
        return

    cdo_remap_to_target(raw, target_grid, remap, method="remapbil")
    append_file(out_path, remap, "SURFACE")
    print("[SURFACE] appended realtime surface fields remapped to hybrid grid")


def append_static_from_original_template(template_original: Path, target_grid: Path, out_path: Path,
                                         crop_dir: Path, west: float, east: float, south: float, north: float) -> None:
    """
    Extract lsm/z/fsr from original ERA5 template, crop to requested bbox,
    remap to target hybrid grid, then append to output.
    """
    require_tool("grib_copy")
    static_dir = crop_dir / "static_from_original"
    static_dir.mkdir(parents=True, exist_ok=True)

    appended = 0
    for short_name in STATIC_SHORTNAMES:
        raw = static_dir / f"{short_name}_raw.grib"
        cropped = static_dir / f"{short_name}_crop.grib"
        remapped = static_dir / f"{short_name}_remap.grib"

        for f in (raw, cropped, remapped):
            try:
                f.unlink()
            except FileNotFoundError:
                pass

        try:
            run([
                "grib_copy", "-w", f"shortName={short_name},typeOfLevel=surface",
                str(template_original), str(raw)
            ])
            if not raw.exists() or raw.stat().st_size == 0:
                print(f"[STATIC][WARN] not found in original template: {short_name}")
                continue

            cdo_crop(raw, cropped, west, east, south, north)
            cdo_remap_to_target(cropped, target_grid, remapped, method="remapbil")
            append_file(out_path, remapped, "STATIC")
            print(f"[STATIC] appended {short_name} from ORIGINAL template remapped to hybrid grid")
            appended += 1
        except Exception as e:
            print(f"[STATIC][WARN] failed {short_name}: {e}")

    print(f"[STATIC] appended static fields count: {appended}")


def append_lnsp_surface_from_output_sp(out_path: Path, crop_dir: Path) -> None:
    """
    Append lnsp surface = log(sp surface) using the already-remapped sp surface
    contained in final output GRIB. SILAM may need this as log_ground_pressure.
    """
    lnsp_out = crop_dir / "lnsp_surface_from_sp.grib"
    try:
        lnsp_out.unlink()
    except FileNotFoundError:
        pass

    sp_gid = None
    with out_path.open("rb") as f:
        while True:
            gid = codes_grib_new_from_file(f)
            if gid is None:
                break
            try:
                param_id = int(safe_get(gid, "paramId", -999))
                level_type = str(safe_get(gid, "typeOfLevel", ""))
                level = int(float(safe_get(gid, "level", 0)))
                if param_id == 134 and level_type == "surface" and level == 0:
                    sp_gid = gid
                    break
            except Exception:
                codes_release(gid)

    if sp_gid is None:
        print("[LNSP][WARN] sp surface paramId=134 not found; cannot append lnsp surface")
        return

    sp_values = np.asarray(codes_get_double_array(sp_gid, "values"), dtype=np.float64)
    lnsp_values = np.log(np.maximum(sp_values, 1.0))

    codes_set(sp_gid, "paramId", 152)
    try:
        codes_set(sp_gid, "shortName", "lnsp")
    except Exception:
        pass
    codes_set_values(sp_gid, lnsp_values)

    with lnsp_out.open("wb") as w:
        codes_write(sp_gid, w)

    codes_release(sp_gid)
    append_file(out_path, lnsp_out, "LNSP")
    print("[LNSP] appended lnsp surface from remapped sp surface")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pressure", required=True, help="Input ECMWF pressure-level GRIB")
    ap.add_argument("--template", required=True, help="ERA5/ECMWF hybrid template GRIB")
    ap.add_argument("--out", required=True, help="Output hybrid GRIB")
    ap.add_argument("--center-lat", type=float, default=-8.108, help="Center latitude")
    ap.add_argument("--center-lon", type=float, default=112.922, help="Center longitude")
    ap.add_argument("--radius-km", type=float, default=180.0, help="Crop radius in km")
    ap.add_argument("--no-crop", action="store_true", help="Disable crop and convert full files")
    ap.add_argument("--keep-crop", action="store_true", help="Keep cropped temporary files")
    ap.add_argument("--crop-dir", default="meteo/crop_work", help="Directory for cropped/remapped files")
    args = ap.parse_args()

    pressure = Path(args.pressure)
    template = Path(args.template)
    out = Path(args.out)
    crop_dir = Path(args.crop_dir)
    crop_dir.mkdir(parents=True, exist_ok=True)

    if args.no_crop:
        # No-crop mode still remaps surface/static to target hybrid grid extracted from template.
        target_grid = crop_dir / "target_hybrid_grid.grib"
        extract_target_hybrid_grid(template, target_grid)
        convert_pressure_to_hybrid(pressure, template, out)
        append_realtime_surface_remapped(pressure, target_grid, out, crop_dir)
        # For no-crop, use very wide bbox just to pass through cdo_crop if needed.
        append_static_from_original_template(template, target_grid, out, crop_dir, -180.0, 180.0, -90.0, 90.0)
        append_lnsp_surface_from_output_sp(out, crop_dir)
        return

    west, east, south, north = bbox_from_center_radius(args.center_lat, args.center_lon, args.radius_km)
    print("[CROP] center lat/lon:", args.center_lat, args.center_lon)
    print("[CROP] radius km     :", args.radius_km)
    print("[CROP] bbox W/E/S/N  :", f"{west:.4f}", f"{east:.4f}", f"{south:.4f}", f"{north:.4f}")

    crop_pressure = crop_dir / "pressure_crop.grib"
    crop_template = crop_dir / "template_hybrid_crop.grib"
    target_grid = crop_dir / "target_hybrid_grid.grib"

    cdo_crop(pressure, crop_pressure, west, east, south, north)
    cdo_crop(template, crop_template, west, east, south, north)
    extract_target_hybrid_grid(crop_template, target_grid)

    convert_pressure_to_hybrid(crop_pressure, crop_template, out)
    append_realtime_surface_remapped(crop_pressure, target_grid, out, crop_dir)
    append_static_from_original_template(template, target_grid, out, crop_dir, west, east, south, north)
    append_lnsp_surface_from_output_sp(out, crop_dir)

    if not args.keep_crop:
        for f in (crop_pressure, crop_template, target_grid):
            try:
                f.unlink()
            except Exception:
                pass


if __name__ == "__main__":
    main()
