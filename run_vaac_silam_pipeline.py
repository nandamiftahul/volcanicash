#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
VAAC -> SILAM -> Leaflet HTML Pipeline

Urutan:
0. Download ERA5
1. Parse VAAC Darwin sesuai waktu yang ditentukan
2. Update SILAM control dan POINT_SOURCE_5 file
3. Run SILAM
4. Generate HTML Leaflet player + VAAC polygon

Contoh:
    python3 run_vaac_silam_pipeline.py \
      --start "2026-05-06 10" \
      --end "2026-05-06 12" \
      --volcano SEMERU \
      --case-name semeru_ash_20260506_1000_auto \
      --vaa-url "https://www.volcanodiscovery.com/semeru/news/301626/vaac-advisory-2026-515.html"

Catatan:
- Untuk historical VAAC, pakai --vaa-url yang sesuai tanggal simulasi.
- Kalau tidak diberi --vaa-url, script akan pakai halaman current BOM, biasanya hanya cocok untuk case real-time/current.
"""

import argparse
import json
import re
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


# =============================================================================
# Basic helpers
# =============================================================================

def log(msg):
    print(msg, flush=True)


def run_cmd(cmd, cwd=None, check=True):
    log("\n[CMD] " + " ".join(str(x) for x in cmd))
    if cwd:
        log(f"[CWD] {cwd}")

    p = subprocess.run(cmd, cwd=cwd)

    if check and p.returncode != 0:
        raise RuntimeError(f"Command failed with return code {p.returncode}: {' '.join(cmd)}")

    return p.returncode


def parse_user_time(value):
    """
    Accept:
        2026-05-06 10
        2026-05-06 10:00
        2026-05-06T10:00:00
    Return timezone-aware UTC datetime.
    """
    value = value.strip().replace("T", " ")

    formats = [
        "%Y-%m-%d %H",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d %H:%M:%S",
    ]

    for fmt in formats:
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            pass

    raise ValueError(f"Format waktu tidak dikenali: {value}")


def dt_to_downloader_arg(dt):
    return dt.strftime("%Y-%m-%d %H")


def dt_to_control(dt):
    return dt.strftime("%Y %m %d %H %M %S")


def dt_to_source_parts(dt):
    """
    SILAM source sebelumnya memakai:
        2026 5 6 10 00 0.
    """
    return f"{dt.year} {dt.month} {dt.day} {dt.hour} {dt.minute:02d} 0."


def iso_to_dt(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None


def backup_once(path):
    path = Path(path)
    if not path.exists():
        return

    bak = path.with_suffix(path.suffix + ".bak")
    if not bak.exists():
        shutil.copy2(path, bak)
        log(f"[BACKUP] {path} -> {bak}")


# =============================================================================
# Import existing VAAC parser
# =============================================================================

def import_vaac_parser(model_root):
    """
    Import functions from meteo/parse_darwin_vaac.py.
    """
    meteo_dir = Path(model_root) / "meteo"
    sys.path.insert(0, str(meteo_dir.resolve()))

    from parse_darwin_vaac import (
        DEFAULT_URL,
        fetch_bom_text,
        split_vaa_blocks,
        parse_advisory_block,
        filter_advisories,
        save_json,
        save_geojson,
    )

    return {
        "DEFAULT_URL": DEFAULT_URL,
        "fetch_bom_text": fetch_bom_text,
        "split_vaa_blocks": split_vaa_blocks,
        "parse_advisory_block": parse_advisory_block,
        "filter_advisories": filter_advisories,
        "save_json": save_json,
        "save_geojson": save_geojson,
    }


# =============================================================================
# Step 0: Download ERA5
# =============================================================================

def step_download_era5(model_root, start_dt, end_dt, meteo_out, force=False):
    model_root = Path(model_root)
    meteo_dir = model_root / "meteo"
    script = meteo_dir / "download_era5_sem_real_v2.py"

    if not script.exists():
        raise FileNotFoundError(f"Script downloader tidak ditemukan: {script}")

    cmd = [
        sys.executable,
        str(script.name),
        "--start", dt_to_downloader_arg(start_dt),
        "--end", dt_to_downloader_arg(end_dt),
        "--out", Path(meteo_out).name,
    ]

    if force:
        cmd.append("--force")

    run_cmd(cmd, cwd=meteo_dir)

    meteo_path = meteo_dir / Path(meteo_out).name
    if not meteo_path.exists():
        raise FileNotFoundError(f"ERA5 output tidak ditemukan setelah download: {meteo_path}")

    log(f"[OK] ERA5 ready: {meteo_path}")
    return meteo_path


# =============================================================================
# Step 1: Parse VAAC and match time
# =============================================================================

def fetch_and_parse_vaac_urls(parser_api, urls):
    all_items = []

    for url in urls:
        log(f"\n[VAAC] Fetch: {url}")

        text = parser_api["fetch_bom_text"](url)
        blocks = parser_api["split_vaa_blocks"](text)

        # Fallback untuk halaman yang hanya berisi satu advisory text.
        if not blocks and "VA ADVISORY" in text:
            blocks = [text]

        if not blocks:
            log("[WARN] Tidak menemukan VA ADVISORY block di URL ini.")
            continue

        for block in blocks:
            item = parser_api["parse_advisory_block"](block)
            item["_source_url"] = url
            all_items.append(item)

    return all_items


def advisory_time(item):
    """
    Prioritas waktu:
    1. main_va_dtg_iso = waktu observed/estimated ash cloud
    2. dtg_iso = waktu advisory diterbitkan
    """
    return iso_to_dt(item.get("main_va_dtg_iso")) or iso_to_dt(item.get("dtg_iso"))


def choose_matching_advisory(items, start_dt, end_dt, tolerance_hours=0.0):
    """
    Cari advisory yang waktunya masuk range start-end.
    Kalau ada lebih dari satu, pilih yang paling dekat ke start_dt.
    """
    tol = timedelta(hours=tolerance_hours)
    match_start = start_dt - tol
    match_end = end_dt + tol

    candidates = []

    for item in items:
        t = advisory_time(item)
        if not t:
            continue

        if match_start <= t <= match_end:
            distance = abs((t - start_dt).total_seconds())
            candidates.append((distance, t, item))

    candidates.sort(key=lambda x: x[0])

    if not candidates:
        return None

    return candidates[0][2]


def step_parse_vaac(
    model_root,
    start_dt,
    end_dt,
    volcano,
    case_name,
    vaa_urls,
    tolerance_hours,
):
    model_root = Path(model_root)
    parser_api = import_vaac_parser(model_root)

    # Jika URL tidak diberikan, pakai current BOM page.
    if not vaa_urls:
        vaa_urls = [parser_api["DEFAULT_URL"]]
        log("[INFO] --vaa-url tidak diberikan. Pakai BOM current page.")

    raw_items = fetch_and_parse_vaac_urls(parser_api, vaa_urls)

    selected = parser_api["filter_advisories"](
        raw_items,
        vaac="DARWIN",
        volcano=volcano,
        area=None,
    )

    log("\n[VAAC] Candidate advisories:")
    for item in selected:
        t = advisory_time(item)
        t_str = t.isoformat().replace("+00:00", "Z") if t else "UNKNOWN"
        log(
            f"  - {item.get('volcano')} | adv={item.get('advisory_nr')} | "
            f"dtg={item.get('dtg_iso')} | main={item.get('main_va_dtg_iso')} | use={t_str}"
        )

    matched = choose_matching_advisory(
        selected,
        start_dt=start_dt,
        end_dt=end_dt,
        tolerance_hours=tolerance_hours,
    )

    if matched is None:
        raise RuntimeError(
            "Tidak ada VAAC yang masuk range simulasi. "
            "Gunakan --vaa-url yang sesuai tanggal simulasi, atau naikkan --tolerance-hours."
        )

    matched_time = advisory_time(matched)
    log("\n[VAAC] MATCHED:")
    log(f"  Volcano     : {matched.get('volcano')}")
    log(f"  Advisory NR : {matched.get('advisory_nr')}")
    log(f"  DTG         : {matched.get('dtg_iso')}")
    log(f"  Main VA DTG : {matched.get('main_va_dtg_iso')}")
    log(f"  Used time   : {matched_time.isoformat().replace('+00:00', 'Z')}")
    log(f"  PSN         : {matched.get('psn')}")
    log(f"  Cloud       : {matched.get('main_va_cloud')}")

    out_dir = model_root / "output" / case_name
    out_dir.mkdir(parents=True, exist_ok=True)

    vaac_json = out_dir / "matched_vaac.json"
    vaac_geojson = out_dir / "matched_vaac.geojson"

    parser_api["save_json"](vaac_json, [matched])
    parser_api["save_geojson"](vaac_geojson, [matched])

    log(f"[SAVE] VAAC JSON    : {vaac_json}")
    log(f"[SAVE] VAAC GeoJSON : {vaac_geojson}")

    return matched, vaac_json, vaac_geojson


# =============================================================================
# Step 2: Update control and source file
# =============================================================================

def get_source_lat_lon(advisory):
    psn = advisory.get("psn_decimal") or {}
    lat = psn.get("lat")
    lon = psn.get("lon")

    if lat is None or lon is None:
        raise RuntimeError("VAAC tidak punya PSN decimal yang valid.")

    return float(lat), float(lon)


def get_source_elev_m(advisory, default=3657.0):
    elev = advisory.get("source_elev_parsed") or {}
    value = elev.get("m")
    if value is None:
        return float(default)
    return float(value)


def get_plume_top_m(advisory, source_elev_m, default_top_m=5200.0):
    fl = advisory.get("main_flight_level") or {}
    top_ft = fl.get("top_ft")

    if top_ft is None:
        return float(default_top_m)

    top_m = float(top_ft) * 0.3048

    # Jangan sampai top lebih rendah dari summit.
    if top_m <= source_elev_m:
        top_m = source_elev_m + 1000.0

    return round(top_m, 1)


def make_point_source_text(
    advisory,
    source_name,
    release_start_dt,
    release_end_dt,
    emission_rate,
    default_elev_m,
    default_top_m,
):
    lat, lon = get_source_lat_lon(advisory)
    elev_m = get_source_elev_m(advisory, default=default_elev_m)
    top_m = get_plume_top_m(advisory, source_elev_m=elev_m, default_top_m=default_top_m)

    text = f"""POINT_SOURCE_5

source_name = {source_name}
source_sector_name = NATURAL

source_latitude = {lat:.6f}
source_longitude = {lon:.6f}

release_rate_unit = kg/sec
vertical_unit = m
vertical_distribution = SINGLE_LEVEL_DYNAMIC
stack_height = {elev_m:.1f} m

# Generated automatically from Darwin VAAC advisory
# Advisory NR : {advisory.get('advisory_nr')}
# DTG         : {advisory.get('dtg_iso')}
# Main VA DTG : {advisory.get('main_va_dtg_iso')}
# PSN         : {advisory.get('psn')}
# Main cloud  : {advisory.get('main_va_cloud')}
#
# par_str_point format:
# y m d h m sec  xy_size  bottom_m  top_m  z_velocity  temp_K  cocktail  rate_kg_s

par_str_point = {dt_to_source_parts(release_start_dt)}  1.  {elev_m:.1f} {top_m:.1f}  0. 273. VOLCANO_ASH_COCKTAIL {float(emission_rate):.3f}
par_str_point = {dt_to_source_parts(release_end_dt)}  1.  {elev_m:.1f} {top_m:.1f}  0. 273. VOLCANO_ASH_COCKTAIL 0.

hour_in_day_index = VOLCANO_ASH_COCKTAIL 1. 1. 1. 1. 1. 1. 1. 1. 1. 1. 1. 1. 1. 1. 1. 1. 1. 1. 1. 1. 1. 1. 1. 1.
day_in_week_index = VOLCANO_ASH_COCKTAIL 1. 1. 1. 1. 1. 1. 1.
month_in_year_index = VOLCANO_ASH_COCKTAIL 1. 1. 1. 1. 1. 1. 1. 1. 1. 1. 1. 1.

END_POINT_SOURCE_5
"""
    return text, lat, lon, elev_m, top_m


def replace_or_warn(text, pattern, replacement, label):
    new_text, n = re.subn(pattern, replacement, text, flags=re.MULTILINE)
    if n == 0:
        log(f"[WARN] Pattern tidak ketemu saat update {label}: {pattern}")
    else:
        log(f"[OK] Updated {label}: {n} replacement")
    return new_text


def update_control_file(
    control_file,
    case_name,
    start_dt,
    end_dt,
    meteo_file_rel,
    source_file_rel,
):
    control_file = Path(control_file)
    backup_once(control_file)

    text = control_file.read_text(encoding="utf-8")

    text = replace_or_warn(
        text,
        r"^case_name\s*=.*$",
        f"case_name = {case_name}",
        "case_name",
    )

    text = replace_or_warn(
        text,
        r"^start_time\s*=.*$",
        f"start_time = {dt_to_control(start_dt)}",
        "start_time",
    )

    text = replace_or_warn(
        text,
        r"^end_time\s*=.*$",
        f"end_time = {dt_to_control(end_dt)}",
        "end_time",
    )

    text = replace_or_warn(
        text,
        r"^dynamic_meteo_file\s*=.*$",
        f"dynamic_meteo_file = GRIB {meteo_file_rel}",
        "dynamic_meteo_file",
    )

    text = replace_or_warn(
        text,
        r"^static_meteo_file\s*=.*$",
        f"static_meteo_file = GRIB {meteo_file_rel}",
        "static_meteo_file",
    )

    text = replace_or_warn(
        text,
        r"^emission_source\s*=.*$",
        f"emission_source = EULERIAN {source_file_rel}",
        "emission_source",
    )

    control_file.write_text(text, encoding="utf-8")
    log(f"[SAVE] Control updated: {control_file}")


def step_update_silam_files(
    model_root,
    case_name,
    start_dt,
    end_dt,
    meteo_path,
    control_file,
    source_file,
    advisory,
    release_hours,
    emission_rate,
    default_elev_m,
    default_top_m,
):
    model_root = Path(model_root)
    control_file = model_root / control_file
    source_file = model_root / source_file

    matched_time = advisory_time(advisory) or start_dt

    # Release start dipakai dari waktu VAAC kalau masuk window, kalau tidak pakai start_dt.
    if start_dt <= matched_time <= end_dt:
        release_start = matched_time
    else:
        release_start = start_dt

    release_end = min(release_start + timedelta(hours=release_hours), end_dt)

    if release_end <= release_start:
        release_end = end_dt

    source_name = f"{case_name.upper()}_SRC"

    source_text, lat, lon, elev_m, top_m = make_point_source_text(
        advisory=advisory,
        source_name=source_name,
        release_start_dt=release_start,
        release_end_dt=release_end,
        emission_rate=emission_rate,
        default_elev_m=default_elev_m,
        default_top_m=default_top_m,
    )

    backup_once(source_file)
    source_file.write_text(source_text, encoding="utf-8")

    log(f"[SAVE] Source updated: {source_file}")
    log(f"[SRC] lat/lon={lat:.6f},{lon:.6f} elev={elev_m:.1f}m top={top_m:.1f}m")
    log(f"[SRC] release={release_start.isoformat()} to {release_end.isoformat()} rate={emission_rate} kg/s")

    meteo_rel = "./" + str(meteo_path.relative_to(model_root))
    source_rel = "./" + str(source_file.relative_to(model_root))

    update_control_file(
        control_file=control_file,
        case_name=case_name,
        start_dt=start_dt,
        end_dt=end_dt,
        meteo_file_rel=meteo_rel,
        source_file_rel=source_rel,
    )


# =============================================================================
# Step 3: Run SILAM
# =============================================================================

def step_run_silam(model_root, silam_bin, control_file):
    model_root = Path(model_root)
    silam_bin = model_root / silam_bin
    control_file = Path(control_file)

    if not silam_bin.exists():
        raise FileNotFoundError(f"SILAM binary tidak ditemukan: {silam_bin}")

    run_cmd(
        [str(silam_bin), str(control_file)],
        cwd=model_root,
        check=True,
    )


# =============================================================================
# Step 4: Generate HTML
# =============================================================================

def step_generate_html(model_root, case_name, vaac_geojson, mode, height, opacity):
    model_root = Path(model_root)
    viewer_script = model_root / "meteo" / "make_silam_leaflet_player.py"

    out_dir = model_root / "output" / case_name
    nc_path = out_dir / "ash_output.nc"
    html_path = out_dir / "ash_player_with_vaac.html"

    # Beberapa SILAM output template bisa menghasilkan nama ash_output.nc
    # Kalau tidak ada, cari .nc pertama di folder case.
    if not nc_path.exists():
        nc_files = sorted(out_dir.glob("*.nc"))
        if not nc_files:
            raise FileNotFoundError(f"Tidak ada NetCDF output di: {out_dir}")
        nc_path = nc_files[0]
        log(f"[INFO] ash_output.nc tidak ditemukan, pakai: {nc_path}")

    run_cmd(
        [
            sys.executable,
            str(viewer_script),
            "--nc", str(nc_path),
            "--vaac-geojson", str(vaac_geojson),
            "--out", str(html_path),
            "--mode", mode,
            "--height", str(height),
            "--opacity", str(opacity),
        ],
        cwd=model_root,
        check=True,
    )

    log(f"[DONE] HTML: {html_path}")
    return html_path


# =============================================================================
# Main
# =============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Full pipeline: ERA5 download -> VAAC parse -> update SILAM -> run -> HTML."
    )

    parser.add_argument("--model-root", default=".", help="Root folder silam-model")
    parser.add_argument("--start", required=True, help='Start UTC, contoh: "2026-05-06 10"')
    parser.add_argument("--end", required=True, help='End UTC, contoh: "2026-05-06 12"')
    parser.add_argument("--volcano", default="SEMERU", help="Nama volcano filter")
    parser.add_argument("--case-name", required=True, help="SILAM case_name baru")

    parser.add_argument(
        "--vaa-url",
        action="append",
        default=[],
        help="URL VAAC advisory. Bisa lebih dari satu. Jika kosong, pakai BOM current page.",
    )
    parser.add_argument(
        "--tolerance-hours",
        type=float,
        default=0.0,
        help="Toleransi match VAAC terhadap start-end simulasi.",
    )

    parser.add_argument(
        "--meteo-out",
        default=None,
        help="Nama output GRIB di folder meteo. Default auto dari start/end.",
    )
    parser.add_argument("--force-era5", action="store_true", help="Tambahkan --force ke downloader ERA5")

    parser.add_argument("--control-file", default="silam_semeru.control")
    parser.add_argument("--source-file", default="ini/src_semeru_point.v5")
    parser.add_argument("--silam-bin", default="bin/silam_v5_9pub")

    parser.add_argument("--release-hours", type=float, default=2.0)
    parser.add_argument("--emission-rate", type=float, default=1000.0)
    parser.add_argument("--default-elev-m", type=float, default=3657.0)
    parser.add_argument("--default-top-m", type=float, default=5200.0)

    parser.add_argument("--html-mode", choices=["max", "sum", "layer"], default="max")
    parser.add_argument("--html-height", type=float, default=5000.0)
    parser.add_argument("--html-opacity", type=float, default=0.85)

    parser.add_argument(
        "--skip-era5",
        action="store_true",
        help="Skip download ERA5, pakai --meteo-out yang sudah ada.",
    )
    parser.add_argument("--skip-run", action="store_true", help="Skip run SILAM")
    parser.add_argument("--skip-html", action="store_true", help="Skip generate HTML")

    args = parser.parse_args()

    model_root = Path(args.model_root).resolve()
    start_dt = parse_user_time(args.start)
    end_dt = parse_user_time(args.end)

    if end_dt <= start_dt:
        raise ValueError("--end harus lebih besar dari --start")

    if args.meteo_out:
        meteo_out = args.meteo_out
    else:
        meteo_out = (
            f"era5_all_{args.volcano.lower()}_"
            f"{start_dt.strftime('%Y%m%d_%H')}_{end_dt.strftime('%H')}.grib"
        )

    log("=" * 90)
    log("VAAC -> SILAM PIPELINE")
    log("=" * 90)
    log(f"Model root : {model_root}")
    log(f"Case name  : {args.case_name}")
    log(f"Start UTC  : {start_dt.isoformat().replace('+00:00', 'Z')}")
    log(f"End UTC    : {end_dt.isoformat().replace('+00:00', 'Z')}")
    log(f"Volcano    : {args.volcano}")
    log(f"Meteo out  : {meteo_out}")
    log("=" * 90)

    # 0. Download ERA5
    if args.skip_era5:
        meteo_path = model_root / "meteo" / Path(meteo_out).name
        if not meteo_path.exists():
            raise FileNotFoundError(f"--skip-era5 aktif, tapi file meteo tidak ada: {meteo_path}")
        log(f"[SKIP] ERA5 download. Use existing: {meteo_path}")
    else:
        meteo_path = step_download_era5(
            model_root=model_root,
            start_dt=start_dt,
            end_dt=end_dt,
            meteo_out=meteo_out,
            force=args.force_era5,
        )

    # 1. Parse VAAC
    advisory, vaac_json, vaac_geojson = step_parse_vaac(
        model_root=model_root,
        start_dt=start_dt,
        end_dt=end_dt,
        volcano=args.volcano,
        case_name=args.case_name,
        vaa_urls=args.vaa_url,
        tolerance_hours=args.tolerance_hours,
    )

    # 2. Update control and v5 source
    step_update_silam_files(
        model_root=model_root,
        case_name=args.case_name,
        start_dt=start_dt,
        end_dt=end_dt,
        meteo_path=meteo_path,
        control_file=args.control_file,
        source_file=args.source_file,
        advisory=advisory,
        release_hours=args.release_hours,
        emission_rate=args.emission_rate,
        default_elev_m=args.default_elev_m,
        default_top_m=args.default_top_m,
    )

    # 3. Run SILAM
    if args.skip_run:
        log("[SKIP] Run SILAM")
    else:
        step_run_silam(
            model_root=model_root,
            silam_bin=args.silam_bin,
            control_file=args.control_file,
        )

    # 4. Generate HTML
    if args.skip_html:
        log("[SKIP] Generate HTML")
    else:
        html_path = step_generate_html(
            model_root=model_root,
            case_name=args.case_name,
            vaac_geojson=vaac_geojson,
            mode=args.html_mode,
            height=args.html_height,
            opacity=args.html_opacity,
        )
        log("\nOpen:")
        log(f"firefox {html_path}")

    log("\n[DONE] Pipeline selesai.")


if __name__ == "__main__":
    main()
