#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Combine multiple SILAM NetCDF3 output files along time dimension.

Tujuan:
    Menggabungkan beberapa ash_output.nc hasil run pendek SILAM menjadi satu file NetCDF,
    supaya viewer HTML lama tetap bisa dipakai tanpa modifikasi.

Contoh:
    python3 meteo/combine_silam_nc_time.py \
      --nc ./output/semeru_ash_20260506_00_06/ash_output.nc \
      --nc ./output/semeru_ash_20260506_06_12/ash_output.nc \
      --nc ./output/semeru_ash_20260506_12_18/ash_output.nc \
      --out ./output/semeru_ash_20260506_18h_combined/ash_output.nc

Kemudian:
    python3 meteo/make_silam_leaflet_player.py \
      --nc ./output/semeru_ash_20260506_18h_combined/ash_output.nc \
      --vaac-geojson ./output/semeru_ash_20260506_06_12/matched_vaac.geojson \
      --out ./output/semeru_ash_20260506_18h_combined/ash_player_with_vaac.html \
      --mode max
"""

import argparse
import re
from pathlib import Path
from datetime import datetime, timedelta, timezone

import numpy as np
from scipy.io import netcdf_file


def decode_attr(x):
    if isinstance(x, bytes):
        return x.decode("utf-8", errors="ignore")
    return str(x)


def parse_time_units(units):
    """
    Parse:
        seconds since 2026-05-06 10:00:00 UTC
    """
    units = decode_attr(units).strip()

    if "since" not in units:
        raise ValueError(f"Time units tidak punya 'since': {units}")

    unit_part, base_part = units.split("since", 1)
    unit_part = unit_part.strip().lower()
    base_part = base_part.replace("UTC", "").strip()

    if unit_part not in ["second", "seconds", "sec", "secs"]:
        raise ValueError(f"Script ini saat ini hanya support seconds since. Ditemukan: {unit_part}")

    base_part = re.sub(r"\s+", " ", base_part)

    formats = [
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d %H",
    ]

    for fmt in formats:
        try:
            return datetime.strptime(base_part, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            pass

    raise ValueError(f"Gagal parse base time dari units: {units}")


def read_nc_metadata(path):
    path = Path(path)

    f = netcdf_file(path, "r", mmap=False)

    if "time" not in f.variables:
        f.close()
        raise RuntimeError(f"Tidak ada variable time di {path}")

    time_var = f.variables["time"]
    time_units = decode_attr(getattr(time_var, "units", ""))
    base_dt = parse_time_units(time_units)

    time_values = time_var.data.copy()
    absolute_times = [base_dt + timedelta(seconds=float(t)) for t in time_values]

    dims = dict(f.dimensions)
    variables = list(f.variables.keys())
    global_attrs = dict(getattr(f, "_attributes", {}))

    f.close()

    return {
        "path": path,
        "time_units": time_units,
        "base_dt": base_dt,
        "time_values": time_values,
        "absolute_times": absolute_times,
        "dims": dims,
        "variables": variables,
        "global_attrs": global_attrs,
    }


def get_var_dims(var):
    return tuple(getattr(var, "dimensions", ()))


def get_var_attrs(var):
    return dict(getattr(var, "_attributes", {}))


def dtype_for_netcdf(dtype):
    """
    NetCDF3 aman untuk:
        f4, f8, i4, i2, i1
    Kalau int64/uint64, turunkan ke f8.
    """
    dtype = np.dtype(dtype)

    if dtype.kind == "f":
        return "f8" if dtype.itemsize > 4 else "f4"

    if dtype.kind in ["i", "u"]:
        if dtype.itemsize <= 1:
            return "i1"
        if dtype.itemsize <= 2:
            return "i2"
        if dtype.itemsize <= 4:
            return "i4"
        return "f8"

    # fallback
    return "f4"


def compare_static_grids(first_path, other_path, var_names=("lon", "lat", "height")):
    f1 = netcdf_file(first_path, "r", mmap=False)
    f2 = netcdf_file(other_path, "r", mmap=False)

    try:
        for name in var_names:
            if name not in f1.variables or name not in f2.variables:
                continue

            a = f1.variables[name].data.copy()
            b = f2.variables[name].data.copy()

            if a.shape != b.shape:
                raise RuntimeError(
                    f"Grid variable {name} beda shape: "
                    f"{first_path} {a.shape} vs {other_path} {b.shape}"
                )

            if not np.allclose(a, b, equal_nan=True):
                raise RuntimeError(
                    f"Grid variable {name} beda nilai antara "
                    f"{first_path} dan {other_path}"
                )
    finally:
        f1.close()
        f2.close()


def build_time_records(nc_paths, duplicate_policy="keep-first"):
    """
    Return list of records:
        {
            "dt": datetime,
            "path": path,
            "time_index": i
        }

    duplicate_policy:
        keep-first
        keep-last
    """
    records = []

    for path in nc_paths:
        meta = read_nc_metadata(path)

        for i, dt in enumerate(meta["absolute_times"]):
            records.append(
                {
                    "dt": dt,
                    "path": Path(path),
                    "time_index": i,
                }
            )

    records.sort(key=lambda r: r["dt"])

    unique = {}

    if duplicate_policy == "keep-first":
        for rec in records:
            key = rec["dt"]
            if key not in unique:
                unique[key] = rec

    elif duplicate_policy == "keep-last":
        for rec in records:
            key = rec["dt"]
            unique[key] = rec

    else:
        raise ValueError("duplicate_policy harus keep-first atau keep-last")

    final_records = [unique[k] for k in sorted(unique.keys())]

    return final_records


def copy_global_attrs(src_file, out_file):
    attrs = getattr(src_file, "_attributes", {})

    for key, value in attrs.items():
        try:
            setattr(out_file, key, value)
        except Exception:
            pass


def copy_var_attrs(src_var, out_var, skip_keys=None):
    if skip_keys is None:
        skip_keys = set()

    attrs = getattr(src_var, "_attributes", {})

    for key, value in attrs.items():
        if key in skip_keys:
            continue
        try:
            setattr(out_var, key, value)
        except Exception:
            pass


def collect_time_dependent_variable(var_name, records):
    """
    Ambil variable yang punya dimensi time dari beberapa file.
    """
    chunks = []
    dims = None
    axis = None

    for rec in records:
        f = netcdf_file(rec["path"], "r", mmap=False)

        try:
            var = f.variables[var_name]
            dims = get_var_dims(var)

            if "time" not in dims:
                raise RuntimeError(f"Variable {var_name} tidak punya dimensi time")

            axis = dims.index("time")
            data = var.data.copy()

            # Ambil satu timestep, tapi tetap pertahankan dimensi time = 1
            slc = np.take(data, indices=[rec["time_index"]], axis=axis)
            chunks.append(slc)

        finally:
            f.close()

    combined = np.concatenate(chunks, axis=axis)

    return combined


def combine_nc(nc_paths, out_path, duplicate_policy="keep-first"):
    nc_paths = [Path(p) for p in nc_paths]
    out_path = Path(out_path)

    if len(nc_paths) < 1:
        raise ValueError("Minimal butuh 1 file NetCDF.")

    for p in nc_paths:
        if not p.exists():
            raise FileNotFoundError(f"NetCDF tidak ditemukan: {p}")

    print("=" * 90)
    print("SILAM NetCDF Combine")
    print("=" * 90)

    print("[INPUT FILES]")
    for p in nc_paths:
        print(" -", p)

    # Validasi grid antar-file
    first_path = nc_paths[0]
    for p in nc_paths[1:]:
        compare_static_grids(first_path, p)

    records = build_time_records(nc_paths, duplicate_policy=duplicate_policy)

    if not records:
        raise RuntimeError("Tidak ada time record yang bisa digabung.")

    out_base_dt = records[0]["dt"]
    out_time_values = np.array(
        [(rec["dt"] - out_base_dt).total_seconds() for rec in records],
        dtype=np.float64,
    )

    print()
    print("[TIME RANGE]")
    print(" Start :", records[0]["dt"].isoformat().replace("+00:00", "Z"))
    print(" End   :", records[-1]["dt"].isoformat().replace("+00:00", "Z"))
    print(" Count :", len(records))
    print()

    # Buka file pertama sebagai template
    f0 = netcdf_file(first_path, "r", mmap=False)

    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)

        fout = netcdf_file(out_path, "w")

        try:
            copy_global_attrs(f0, fout)

            # Create dimensions
            for dim_name, dim_len in f0.dimensions.items():
                if dim_name == "time":
                    fout.createDimension("time", len(records))
                else:
                    fout.createDimension(dim_name, dim_len)

            # Create variables
            for var_name, src_var in f0.variables.items():
                dims = get_var_dims(src_var)
                src_dtype = src_var.data.dtype

                if var_name == "time":
                    out_var = fout.createVariable("time", "f8", dims)
                    copy_var_attrs(src_var, out_var, skip_keys={"units"})
                    out_var.units = f"seconds since {out_base_dt.strftime('%Y-%m-%d %H:%M:%S')} UTC"
                    out_var[:] = out_time_values
                    print(f"[VAR] time -> {len(out_time_values)} records")
                    continue

                out_dtype = dtype_for_netcdf(src_dtype)
                out_var = fout.createVariable(var_name, out_dtype, dims)
                copy_var_attrs(src_var, out_var)

                if "time" in dims:
                    combined_data = collect_time_dependent_variable(var_name, records)
                    out_var[:] = combined_data
                    print(f"[VAR] {var_name} time-dependent -> {combined_data.shape}")
                else:
                    out_var[:] = src_var.data.copy()
                    print(f"[VAR] {var_name} static -> {src_var.data.shape}")

            fout.history = (
                "Combined from SILAM segment outputs by combine_silam_nc_time.py"
            )

        finally:
            fout.close()

    finally:
        f0.close()

    print()
    print("[DONE] Combined NetCDF:")
    print(out_path)


def main():
    parser = argparse.ArgumentParser(
        description="Combine multiple SILAM NetCDF output files along time dimension."
    )

    parser.add_argument(
        "--nc",
        action="append",
        required=True,
        help="Input ash_output.nc. Bisa dipakai beberapa kali.",
    )

    parser.add_argument(
        "--out",
        required=True,
        help="Output combined NetCDF path.",
    )

    parser.add_argument(
        "--duplicate-policy",
        choices=["keep-first", "keep-last"],
        default="keep-first",
        help="Jika ada waktu duplikat antar-segmen, pilih keep-first atau keep-last.",
    )

    args = parser.parse_args()

    combine_nc(
        nc_paths=args.nc,
        out_path=args.out,
        duplicate_policy=args.duplicate_policy,
    )


if __name__ == "__main__":
    main()
