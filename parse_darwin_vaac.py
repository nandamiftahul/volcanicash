#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Darwin VAAC / BOM Volcanic Ash Advisory Parser

Contoh:
    python3 parse_darwin_vaac.py --volcano SEMERU

Export:
    python3 parse_darwin_vaac.py --volcano SEMERU --json semeru_vaac.json --csv semeru_vaac.csv --geojson semeru_vaac.geojson

Ambil semua volcano Indonesia dari Darwin VAAC:
    python3 parse_darwin_vaac.py --area INDONESIA --json indonesia_vaac.json

Catatan:
- Sumber utama: BOM Volcanic Ash page
- Parser ini membaca VAA text bulletin, bukan API JSON resmi.
"""

import argparse
import csv
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup


DEFAULT_URL = "https://www.bom.gov.au/aviation/warnings/volcanic-ash/"


FIELD_LABELS = [
    "DTG",
    "VAAC",
    "VOLCANO",
    "PSN",
    "AREA",
    "SOURCE ELEV",
    "SUMMIT ELEV",
    "ADVISORY NR",
    "INFO SOURCE",
    "AVIATION COLOUR CODE",
    "ERUPTION DETAILS",
    "OBS VA DTG",
    "EST VA DTG",
    "OBS VA CLD",
    "EST VA CLD",
    "FCST VA CLD +6 HR",
    "FCST VA CLD +12 HR",
    "FCST VA CLD +18 HR",
    "RMK",
    "NXT ADVISORY",
]


def fetch_bom_text(url: str = DEFAULT_URL, timeout: int = 45) -> str:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 compatible; DarwinVAACParser/1.0 "
            "+https://www.bom.gov.au/aviation/warnings/volcanic-ash/"
        )
    }

    resp = requests.get(url, headers=headers, timeout=timeout)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")

    # Remove scripts/styles supaya text lebih bersih.
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    text = soup.get_text("\n")

    # Normalize whitespace.
    text = text.replace("\xa0", " ")
    text = re.sub(r"\r\n?", "\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{2,}", "\n", text)

    return text.strip()


def split_vaa_blocks(text: str) -> list[str]:
    """
    Split semua blok:
        Received FVAU02 ...
        VA ADVISORY
        ...
        NXT ADVISORY: ... =
    """

    # Ambil blok yang dimulai dari Received ... lalu VA ADVISORY sampai '='.
    pattern = re.compile(
        r"(Received\s+[A-Z0-9]+.*?\nVA ADVISORY.*?NXT ADVISORY:.*?=)",
        re.IGNORECASE | re.DOTALL,
    )

    blocks = pattern.findall(text)

    # Fallback jika format Received berubah: ambil dari VA ADVISORY sampai NXT ADVISORY.
    if not blocks:
        pattern2 = re.compile(
            r"(VA ADVISORY.*?NXT ADVISORY:.*?=)",
            re.IGNORECASE | re.DOTALL,
        )
        blocks = pattern2.findall(text)

    return [clean_block(b) for b in blocks]


def clean_block(block: str) -> str:
    block = block.replace("\xa0", " ")
    block = re.sub(r"\r\n?", "\n", block)
    block = re.sub(r"[ \t]+", " ", block)
    block = re.sub(r"\n{2,}", "\n", block)
    return block.strip()


def extract_received_line(block: str) -> str | None:
    m = re.search(r"^(Received\s+.+)$", block, re.IGNORECASE | re.MULTILINE)
    return m.group(1).strip() if m else None


def get_field(block: str, label: str) -> str | None:
    """
    Ambil field multiline, contoh:
        EST VA CLD: SFC/FL160 S0809 E11257 - S0805 E11216
        - S0737 E11242 MOV NW 10KT

    Berhenti saat ketemu label field berikutnya.
    """

    escaped_labels = [re.escape(x) for x in FIELD_LABELS]
    next_label_pattern = "|".join(escaped_labels)

    pattern = re.compile(
        rf"^{re.escape(label)}:\s*(.*?)"
        rf"(?=\n(?:{next_label_pattern}):|\nReceived\s+|\Z)",
        re.IGNORECASE | re.MULTILINE | re.DOTALL,
    )

    m = pattern.search(block)
    if not m:
        return None

    value = m.group(1).strip()
    value = re.sub(r"\s*\n\s*", " ", value)
    value = re.sub(r"\s+", " ", value)
    value = value.rstrip("=")

    return value.strip() if value else None


def parse_wmo_dtg(dtg: str | None) -> str | None:
    """
    Convert:
        20260511/2250Z
    menjadi:
        2026-05-11T22:50:00Z
    """

    if not dtg:
        return None

    m = re.search(r"(\d{4})(\d{2})(\d{2})/(\d{2})(\d{2})Z", dtg)
    if not m:
        return None

    y, mo, d, h, mi = map(int, m.groups())
    return datetime(y, mo, d, h, mi, tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")


def parse_short_dtg(short_dtg: str | None, base_dtg: str | None) -> str | None:
    """
    Convert:
        11/2230Z
    dengan base:
        20260511/2250Z
    menjadi:
        2026-05-11T22:30:00Z

    Ini sederhana: tahun dan bulan diambil dari DTG utama advisory.
    """

    if not short_dtg or not base_dtg:
        return None

    base = re.search(r"(\d{4})(\d{2})(\d{2})/", base_dtg)
    short = re.search(r"(\d{2})/(\d{2})(\d{2})Z", short_dtg)

    if not base or not short:
        return None

    y = int(base.group(1))
    mo = int(base.group(2))
    d = int(short.group(1))
    h = int(short.group(2))
    mi = int(short.group(3))

    try:
        return datetime(y, mo, d, h, mi, tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")
    except ValueError:
        return None


def parse_latlon_token(token: str) -> float | None:
    """
    Convert VAAC coordinate token:
        S0806 -> -8.100000
        E11255 -> 112.916667
        N0129 -> 1.483333
        W07150 -> -71.833333

    Format:
        Lat: H DDMM
        Lon: H DDDMM
    """

    token = token.strip().upper()

    m = re.match(r"^([NS])(\d{2})(\d{2})$", token)
    if m:
        hemi, deg, minute = m.groups()
        val = int(deg) + int(minute) / 60.0
        return -val if hemi == "S" else val

    m = re.match(r"^([EW])(\d{3})(\d{2})$", token)
    if m:
        hemi, deg, minute = m.groups()
        val = int(deg) + int(minute) / 60.0
        return -val if hemi == "W" else val

    return None


def parse_position_pair(text: str | None) -> dict | None:
    """
    Convert:
        S0806 E11255
    menjadi:
        {"lat": -8.1, "lon": 112.916667}
    """

    if not text:
        return None

    m = re.search(r"\b([NS]\d{4})\s+([EW]\d{5})\b", text.upper())
    if not m:
        return None

    lat = parse_latlon_token(m.group(1))
    lon = parse_latlon_token(m.group(2))

    if lat is None or lon is None:
        return None

    return {
        "lat": round(lat, 6),
        "lon": round(lon, 6),
        "raw": f"{m.group(1)} {m.group(2)}",
    }


def extract_polygon_points(cloud_text: str | None) -> list[dict]:
    """
    Extract semua titik polygon dari:
        SFC/FL160 S0809 E11257 - S0805 E11216 - S0737 E11242 - S0804 E11259 MOV NW 10KT
    """

    if not cloud_text:
        return []

    pairs = re.findall(r"\b([NS]\d{4})\s+([EW]\d{5})\b", cloud_text.upper())

    points = []
    for lat_token, lon_token in pairs:
        lat = parse_latlon_token(lat_token)
        lon = parse_latlon_token(lon_token)

        if lat is None or lon is None:
            continue

        points.append(
            {
                "lat": round(lat, 6),
                "lon": round(lon, 6),
                "raw": f"{lat_token} {lon_token}",
            }
        )

    return points


def parse_flight_levels(text: str | None) -> dict:
    """
    Extract SFC/FL160, FL070/FL200, dst.
    """

    result = {
        "bottom": None,
        "top": None,
        "top_ft": None,
        "raw": None,
    }

    if not text:
        return result

    m = re.search(r"\b(SFC|FL\d{3})/(FL\d{3})\b", text.upper())
    if not m:
        return result

    bottom = m.group(1)
    top = m.group(2)

    result["bottom"] = bottom
    result["top"] = top
    result["raw"] = f"{bottom}/{top}"

    mtop = re.search(r"FL(\d{3})", top)
    if mtop:
        result["top_ft"] = int(mtop.group(1)) * 100

    return result


def parse_movement(text: str | None) -> dict:
    """
    Extract:
        MOV NW 10KT
        MOV W 05KT
    """

    result = {
        "direction": None,
        "speed_kt": None,
        "raw": None,
    }

    if not text:
        return result

    m = re.search(r"\bMOV\s+([A-Z]{1,3})\s+(\d{1,3})KT\b", text.upper())
    if not m:
        return result

    result["direction"] = m.group(1)
    result["speed_kt"] = int(m.group(2))
    result["raw"] = m.group(0)

    return result


def parse_source_elev(text: str | None) -> dict:
    """
    Contoh:
        3657M AMSL
        19576 FT [5967 M]
    """

    result = {
        "m": None,
        "ft": None,
        "raw": text,
    }

    if not text:
        return result

    m_m = re.search(r"(\d+(?:\.\d+)?)\s*M\b", text.upper())
    m_ft = re.search(r"(\d+(?:\.\d+)?)\s*FT\b", text.upper())

    if m_m:
        result["m"] = float(m_m.group(1))

    if m_ft:
        result["ft"] = float(m_ft.group(1))

    return result


def parse_volcano_name(volcano_field: str | None) -> str | None:
    """
    Convert:
        SEMERU 263300
    menjadi:
        SEMERU
    """

    if not volcano_field:
        return None

    name = re.sub(r"\s+\d+\s*$", "", volcano_field.strip())
    return name.strip()


def parse_advisory_block(block: str) -> dict:
    data = {
        "received": extract_received_line(block),
        "raw_text": block,
    }

    for label in FIELD_LABELS:
        key = normalize_key(label)
        data[key] = get_field(block, label)

    # Convenience fields.
    data["dtg_iso"] = parse_wmo_dtg(data.get("dtg"))
    data["volcano_name"] = parse_volcano_name(data.get("volcano"))
    data["psn_decimal"] = parse_position_pair(data.get("psn"))

    source_elev = data.get("source_elev") or data.get("summit_elev")
    data["source_elev_parsed"] = parse_source_elev(source_elev)

    # OBS/EST cloud.
    main_cloud = data.get("obs_va_cld") or data.get("est_va_cld")
    main_cloud_dtg = data.get("obs_va_dtg") or data.get("est_va_dtg")

    data["main_va_dtg_iso"] = parse_short_dtg(main_cloud_dtg, data.get("dtg"))
    data["main_va_cloud"] = main_cloud
    data["main_flight_level"] = parse_flight_levels(main_cloud)
    data["main_movement"] = parse_movement(main_cloud)
    data["main_polygon"] = extract_polygon_points(main_cloud)

    # Forecast blocks.
    forecasts = []
    for hour, key in [
        (6, "fcst_va_cld_plus6_hr"),
        (12, "fcst_va_cld_plus12_hr"),
        (18, "fcst_va_cld_plus18_hr"),
    ]:
        cloud = data.get(key)
        forecasts.append(
            {
                "hour": hour,
                "raw": cloud,
                "flight_level": parse_flight_levels(cloud),
                "movement": parse_movement(cloud),
                "polygon": extract_polygon_points(cloud),
            }
        )

    data["forecasts"] = forecasts

    return data


def normalize_key(label: str) -> str:
    key = label.lower()
    key = key.replace("+", "plus")
    key = re.sub(r"[^a-z0-9]+", "_", key)
    key = key.strip("_")
    return key


def filter_advisories(
    advisories: list[dict],
    vaac: str | None = "DARWIN",
    volcano: str | None = None,
    area: str | None = None,
) -> list[dict]:

    result = []

    for item in advisories:
        if vaac:
            item_vaac = (item.get("vaac") or "").upper()
            if item_vaac != vaac.upper():
                continue

        if volcano:
            item_volcano = (item.get("volcano") or "").upper()
            item_volcano_name = (item.get("volcano_name") or "").upper()
            if volcano.upper() not in item_volcano and volcano.upper() not in item_volcano_name:
                continue

        if area:
            item_area = (item.get("area") or "").upper()
            if area.upper() not in item_area:
                continue

        result.append(item)

    return result


def save_json(path: str | Path, data: list[dict]) -> None:
    path = Path(path)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def save_csv(path: str | Path, data: list[dict]) -> None:
    path = Path(path)

    columns = [
        "received",
        "dtg",
        "dtg_iso",
        "vaac",
        "volcano",
        "volcano_name",
        "psn",
        "psn_lat",
        "psn_lon",
        "area",
        "source_elev",
        "advisory_nr",
        "info_source",
        "eruption_details",
        "obs_va_dtg",
        "est_va_dtg",
        "main_va_dtg_iso",
        "main_fl",
        "main_fl_top_ft",
        "main_movement",
        "main_polygon_point_count",
        "next_advisory",
    ]

    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()

        for item in data:
            psn = item.get("psn_decimal") or {}
            fl = item.get("main_flight_level") or {}
            mov = item.get("main_movement") or {}
            polygon = item.get("main_polygon") or []

            writer.writerow(
                {
                    "received": item.get("received"),
                    "dtg": item.get("dtg"),
                    "dtg_iso": item.get("dtg_iso"),
                    "vaac": item.get("vaac"),
                    "volcano": item.get("volcano"),
                    "volcano_name": item.get("volcano_name"),
                    "psn": item.get("psn"),
                    "psn_lat": psn.get("lat"),
                    "psn_lon": psn.get("lon"),
                    "area": item.get("area"),
                    "source_elev": item.get("source_elev") or item.get("summit_elev"),
                    "advisory_nr": item.get("advisory_nr"),
                    "info_source": item.get("info_source"),
                    "eruption_details": item.get("eruption_details"),
                    "obs_va_dtg": item.get("obs_va_dtg"),
                    "est_va_dtg": item.get("est_va_dtg"),
                    "main_va_dtg_iso": item.get("main_va_dtg_iso"),
                    "main_fl": fl.get("raw"),
                    "main_fl_top_ft": fl.get("top_ft"),
                    "main_movement": mov.get("raw"),
                    "main_polygon_point_count": len(polygon),
                    "next_advisory": item.get("nxt_advisory"),
                }
            )


def polygon_to_geojson_ring(points: list[dict]) -> list[list[float]]:
    """
    GeoJSON coordinate format:
        [lon, lat]
    """

    coords = [[p["lon"], p["lat"]] for p in points if "lon" in p and "lat" in p]

    if len(coords) >= 3 and coords[0] != coords[-1]:
        coords.append(coords[0])

    return coords


def to_geojson(data: list[dict]) -> dict:
    features = []

    for item in data:
        volcano = item.get("volcano_name") or item.get("volcano") or "UNKNOWN"
        advisory_nr = item.get("advisory_nr")

        # Main OBS/EST polygon.
        main_points = item.get("main_polygon") or []
        main_ring = polygon_to_geojson_ring(main_points)

        if len(main_ring) >= 4:
            features.append(
                {
                    "type": "Feature",
                    "properties": {
                        "type": "main_va_cloud",
                        "vaac": item.get("vaac"),
                        "volcano": volcano,
                        "advisory_nr": advisory_nr,
                        "dtg": item.get("dtg"),
                        "dtg_iso": item.get("dtg_iso"),
                        "cloud": item.get("main_va_cloud"),
                        "flight_level": (item.get("main_flight_level") or {}).get("raw"),
                        "movement": (item.get("main_movement") or {}).get("raw"),
                    },
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [main_ring],
                    },
                }
            )

        # Forecast polygons.
        for fcst in item.get("forecasts", []):
            fcst_points = fcst.get("polygon") or []
            fcst_ring = polygon_to_geojson_ring(fcst_points)

            if len(fcst_ring) >= 4:
                features.append(
                    {
                        "type": "Feature",
                        "properties": {
                            "type": f"forecast_plus_{fcst.get('hour')}hr",
                            "vaac": item.get("vaac"),
                            "volcano": volcano,
                            "advisory_nr": advisory_nr,
                            "dtg": item.get("dtg"),
                            "dtg_iso": item.get("dtg_iso"),
                            "cloud": fcst.get("raw"),
                            "flight_level": (fcst.get("flight_level") or {}).get("raw"),
                            "movement": (fcst.get("movement") or {}).get("raw"),
                        },
                        "geometry": {
                            "type": "Polygon",
                            "coordinates": [fcst_ring],
                        },
                    }
                )

        # Volcano point.
        psn = item.get("psn_decimal")
        if psn:
            features.append(
                {
                    "type": "Feature",
                    "properties": {
                        "type": "volcano_position",
                        "vaac": item.get("vaac"),
                        "volcano": volcano,
                        "advisory_nr": advisory_nr,
                        "dtg": item.get("dtg"),
                        "dtg_iso": item.get("dtg_iso"),
                        "source_elev": item.get("source_elev") or item.get("summit_elev"),
                    },
                    "geometry": {
                        "type": "Point",
                        "coordinates": [psn["lon"], psn["lat"]],
                    },
                }
            )

    return {
        "type": "FeatureCollection",
        "features": features,
    }


def save_geojson(path: str | Path, data: list[dict]) -> None:
    path = Path(path)
    geojson = to_geojson(data)
    path.write_text(json.dumps(geojson, indent=2, ensure_ascii=False), encoding="utf-8")


def print_summary(data: list[dict]) -> None:
    if not data:
        print("[INFO] Tidak ada advisory yang cocok.")
        return

    print(f"[OK] Ditemukan {len(data)} advisory cocok.\n")

    for i, item in enumerate(data, start=1):
        psn = item.get("psn_decimal") or {}
        fl = item.get("main_flight_level") or {}
        mov = item.get("main_movement") or {}
        polygon = item.get("main_polygon") or []

        print("=" * 80)
        print(f"[{i}] {item.get('volcano')} | {item.get('vaac')} | {item.get('area')}")
        print(f"DTG          : {item.get('dtg')} | {item.get('dtg_iso')}")
        print(f"Advisory NR  : {item.get('advisory_nr')}")
        print(f"PSN          : {item.get('psn')} -> lat={psn.get('lat')} lon={psn.get('lon')}")
        print(f"Source elev  : {item.get('source_elev') or item.get('summit_elev')}")
        print(f"Info source  : {item.get('info_source')}")
        print(f"Eruption     : {item.get('eruption_details')}")
        print(f"VA DTG       : {item.get('obs_va_dtg') or item.get('est_va_dtg')} | {item.get('main_va_dtg_iso')}")
        print(f"Main FL      : {fl.get('raw')} top_ft={fl.get('top_ft')}")
        print(f"Movement     : {mov.get('raw')}")
        print(f"Polygon pts  : {len(polygon)}")
        print(f"Next adv     : {item.get('nxt_advisory')}")
        print()


def main():
    parser = argparse.ArgumentParser(
        description="Parse Darwin VAAC/BOM volcanic ash advisories."
    )

    parser.add_argument("--url", default=DEFAULT_URL, help="BOM volcanic ash page URL")
    parser.add_argument("--vaac", default="DARWIN", help="VAAC filter, default: DARWIN")
    parser.add_argument("--volcano", default=None, help="Volcano filter, contoh: SEMERU")
    parser.add_argument("--area", default=None, help="Area filter, contoh: INDONESIA")
    parser.add_argument("--json", default=None, help="Output JSON path")
    parser.add_argument("--csv", default=None, help="Output CSV path")
    parser.add_argument("--geojson", default=None, help="Output GeoJSON path")
    parser.add_argument("--raw", action="store_true", help="Print raw advisory text juga")
    parser.add_argument("--no-filter", action="store_true", help="Ambil semua advisory tanpa filter")

    args = parser.parse_args()

    try:
        text = fetch_bom_text(args.url)
    except Exception as e:
        print(f"[ERROR] Gagal fetch BOM page: {e}", file=sys.stderr)
        sys.exit(1)

    blocks = split_vaa_blocks(text)

    if not blocks:
        print("[ERROR] Tidak menemukan blok VA ADVISORY di halaman.", file=sys.stderr)
        sys.exit(2)

    advisories = [parse_advisory_block(block) for block in blocks]

    if args.no_filter:
        selected = advisories
    else:
        selected = filter_advisories(
            advisories,
            vaac=args.vaac,
            volcano=args.volcano,
            area=args.area,
        )

    print_summary(selected)

    if args.raw:
        for item in selected:
            print("\n" + "#" * 80)
            print(item.get("raw_text"))
            print("#" * 80 + "\n")

    if args.json:
        save_json(args.json, selected)
        print(f"[SAVE] JSON    : {args.json}")

    if args.csv:
        save_csv(args.csv, selected)
        print(f"[SAVE] CSV     : {args.csv}")

    if args.geojson:
        save_geojson(args.geojson, selected)
        print(f"[SAVE] GeoJSON : {args.geojson}")


if __name__ == "__main__":
    main()
