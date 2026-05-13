#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
SILAM Ash NetCDF Leaflet Player + Darwin VAAC GeoJSON Overlay

Input:
    ./output/semeru_ash_20260506_1000_test/ash_output.nc

Optional input:
    ./output/semeru_ash_20260506_1000_test/semeru_vaac.geojson

Output:
    ./output/semeru_ash_20260506_1000_test/ash_player.html

Cara pakai basic:
    python3 make_silam_leaflet_player.py

Dengan VAAC polygon:
    python3 make_silam_leaflet_player.py \
      --nc ./output/semeru_ash_20260506_1000_test/ash_output.nc \
      --vaac-geojson ./output/semeru_ash_20260506_1000_test/semeru_vaac.geojson \
      --out ./output/semeru_ash_20260506_1000_test/ash_player_with_vaac.html \
      --mode max

Mode layer:
    python3 make_silam_leaflet_player.py \
      --mode layer \
      --height 5000 \
      --vaac-geojson ./output/semeru_ash_20260506_1000_test/semeru_vaac.geojson
"""

import argparse
import base64
import io
import json
from pathlib import Path
from datetime import datetime, timedelta, timezone

import numpy as np
from scipy.io import netcdf_file
from PIL import Image
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors


SEMERU_LAT = -8.100
SEMERU_LON = 112.917


def decode_attr(x):
    if isinstance(x, bytes):
        return x.decode("utf-8", errors="ignore")
    return str(x)


def read_silam_nc(nc_path):
    nc_path = Path(nc_path)

    if not nc_path.exists():
        raise FileNotFoundError(f"NetCDF tidak ditemukan: {nc_path}")

    f = netcdf_file(nc_path, "r", mmap=False)

    lon = f.variables["lon"].data.copy()
    lat = f.variables["lat"].data.copy()
    height = f.variables["height"].data.copy()
    time = f.variables["time"].data.copy()

    ash_var_name = None
    for name in f.variables.keys():
        if name.startswith("cnc_ash"):
            ash_var_name = name
            break

    if ash_var_name is None:
        f.close()
        raise RuntimeError("Tidak menemukan variable cnc_ash* di NetCDF.")

    ash = f.variables[ash_var_name].data.copy()

    time_units = decode_attr(getattr(f.variables["time"], "units", ""))
    ash_units = decode_attr(getattr(f.variables[ash_var_name], "units", ""))

    f.close()

    return {
        "lon": lon,
        "lat": lat,
        "height": height,
        "time": time,
        "time_units": time_units,
        "ash": ash,
        "ash_var_name": ash_var_name,
        "ash_units": ash_units,
    }


def load_vaac_geojson(path):
    """
    Load VAAC GeoJSON hasil dari parse_darwin_vaac.py.

    Kalau path kosong/tidak ada/error, return FeatureCollection kosong.
    """
    empty = {"type": "FeatureCollection", "features": []}

    if not path:
        return empty

    p = Path(path)

    if not p.exists():
        print(f"[WARN] VAAC GeoJSON tidak ditemukan: {p}")
        return empty

    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[WARN] Gagal baca VAAC GeoJSON: {e}")
        return empty

    if not isinstance(data, dict) or data.get("type") != "FeatureCollection":
        print("[WARN] VAAC GeoJSON bukan FeatureCollection.")
        return empty

    features = data.get("features", [])
    if not isinstance(features, list):
        print("[WARN] VAAC GeoJSON features bukan list.")
        return empty

    print(f"[OK] VAAC GeoJSON loaded: {p} | features={len(features)}")
    return data


def parse_time_units(time_values, units):
    """
    Contoh units:
        seconds since 2026-05-06 10:00:00 UTC
    """
    units = units.strip()

    if "since" not in units:
        return [f"Timestep {i}" for i in range(len(time_values))]

    base_str = units.split("since", 1)[1].strip()
    base_str = base_str.replace("UTC", "").strip()

    try:
        base_dt = datetime.strptime(base_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return [f"Timestep {i}" for i in range(len(time_values))]

    labels = []
    for sec in time_values:
        dt = base_dt + timedelta(seconds=float(sec))
        labels.append(dt.strftime("%Y-%m-%d %H:%M UTC"))

    return labels


def normalize_to_rgba(data, vmin=None, vmax=None, cmap_name="inferno"):
    """
    Convert 2D data menjadi PNG RGBA transparan.
    Nilai sangat kecil / nol dibuat transparan.
    """

    arr = np.array(data, dtype=float)
    arr = np.where(np.isfinite(arr), arr, 0.0)

    if vmin is None:
        positive = arr[arr > 0]
        if positive.size:
            vmin = float(np.percentile(positive, 5))
        else:
            vmin = 0.0

    if vmax is None:
        positive = arr[arr > 0]
        if positive.size:
            vmax = float(np.percentile(positive, 99))
        else:
            vmax = 1.0

    if vmax <= vmin:
        vmax = vmin + 1e-12

    # LogNorm cocok untuk konsentrasi abu karena range biasanya besar.
    safe = np.where(arr > 0, arr, np.nan)

    norm = mcolors.LogNorm(vmin=max(vmin, 1e-15), vmax=max(vmax, vmin * 10))
    cmap = plt.get_cmap(cmap_name)

    rgba = cmap(norm(safe))

    # Transparansi: nol/NaN dibuat transparent.
    alpha = np.zeros_like(arr, dtype=float)
    positive = arr > 0

    if positive.any():
        scaled = (np.log10(np.maximum(arr, vmin)) - np.log10(vmin)) / (
            np.log10(vmax) - np.log10(vmin)
        )
        scaled = np.clip(scaled, 0, 1)
        alpha = np.where(positive, 0.18 + 0.72 * scaled, 0.0)

    rgba[..., 3] = alpha

    img = (rgba * 255).astype(np.uint8)

    # Leaflet ImageOverlay expects north-up image.
    # NetCDF lat biasanya ascending dari selatan ke utara, image row pertama harus north.
    img = np.flipud(img)

    pil_img = Image.fromarray(img, mode="RGBA")
    buf = io.BytesIO()
    pil_img.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")

    return "data:image/png;base64," + b64


def make_colorbar_png(vmin, vmax, units, cmap_name="inferno"):
    """
    Buat legend colorbar sebagai base64 PNG.
    """
    fig, ax = plt.subplots(figsize=(4.0, 0.45))
    fig.subplots_adjust(bottom=0.55)

    cmap = plt.get_cmap(cmap_name)
    norm = mcolors.LogNorm(vmin=max(vmin, 1e-15), vmax=max(vmax, vmin * 10))

    cb = plt.colorbar(
        plt.cm.ScalarMappable(norm=norm, cmap=cmap),
        cax=ax,
        orientation="horizontal",
    )
    cb.set_label(f"Ash concentration ({units})")

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight", transparent=True)
    plt.close(fig)

    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return "data:image/png;base64," + b64


def select_layer_data(ash, height, mode, target_height):
    """
    Return array 3D:
        time, lat, lon
    """

    if mode == "max":
        return np.nanmax(ash, axis=1), "Max over height"

    if mode == "sum":
        return np.nansum(ash, axis=1), "Sum over height"

    if mode == "layer":
        z_idx = int(np.argmin(np.abs(height - target_height)))
        return ash[:, z_idx, :, :], f"Layer {height[z_idx]:.0f} m"

    raise ValueError("mode harus salah satu: max, sum, layer")

def parse_iso_utc_to_datetime(value):
    """
    Convert:
        2026-05-06T10:30:00Z
    menjadi datetime UTC.
    """
    if not value:
        return None

    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None


def enrich_vaac_time_status(vaac_data, nc_time_labels):
    """
    Tambahkan status apakah waktu VAAC masuk dalam range waktu NetCDF.

    nc_time_labels contoh:
        ["2026-05-06 10:00 UTC", "2026-05-06 11:00 UTC", ...]
    """

    if not vaac_data or vaac_data.get("type") != "FeatureCollection":
        return vaac_data

    nc_times = []

    for label in nc_time_labels:
        try:
            dt = datetime.strptime(label, "%Y-%m-%d %H:%M UTC").replace(tzinfo=timezone.utc)
            nc_times.append(dt)
        except Exception:
            pass

    if not nc_times:
        return vaac_data

    nc_start = min(nc_times)
    nc_end = max(nc_times)

    for feature in vaac_data.get("features", []):
        props = feature.setdefault("properties", {})

        vaac_dt = parse_iso_utc_to_datetime(props.get("dtg_iso"))

        if vaac_dt is None:
            props["time_match_nc"] = "UNKNOWN"
            props["time_match_note"] = "VAAC dtg_iso is missing or invalid."
            props["nc_start"] = nc_start.isoformat().replace("+00:00", "Z")
            props["nc_end"] = nc_end.isoformat().replace("+00:00", "Z")
            continue

        is_match = nc_start <= vaac_dt <= nc_end

        props["time_match_nc"] = "YES" if is_match else "NO"
        props["time_match_note"] = (
            "VAAC DTG is inside SILAM NetCDF time range."
            if is_match
            else "VAAC DTG is outside SILAM NetCDF time range."
        )
        props["nc_start"] = nc_start.isoformat().replace("+00:00", "Z")
        props["nc_end"] = nc_end.isoformat().replace("+00:00", "Z")

    return vaac_data

def generate_html(
    nc_path,
    out_path,
    mode="max",
    target_height=5000.0,
    opacity=0.85,
    vaac_geojson=None,
    cmap_name="inferno",
):
    data = read_silam_nc(nc_path)

    lon = data["lon"]
    lat = data["lat"]
    height = data["height"]
    time = data["time"]
    ash = data["ash"]
    ash_units = data["ash_units"] or "kg/m3"

    ash_2d_time, layer_label = select_layer_data(
        ash=ash,
        height=height,
        mode=mode,
        target_height=target_height,
    )

    positive = ash_2d_time[ash_2d_time > 0]
    if positive.size:
        vmin = float(np.percentile(positive, 5))
        vmax = float(np.percentile(positive, 99))
    else:
        vmin = 1e-15
        vmax = 1e-12

    frames = []
    time_labels = parse_time_units(time, data["time_units"])
    
    vaac_data = load_vaac_geojson(vaac_geojson)
    vaac_data = enrich_vaac_time_status(vaac_data, time_labels)
    
    for i in range(ash_2d_time.shape[0]):
        png_data_url = normalize_to_rgba(
            ash_2d_time[i],
            vmin=vmin,
            vmax=vmax,
            cmap_name=cmap_name,
        )
        frames.append(
            {
                "time_index": i,
                "label": time_labels[i],
                "image": png_data_url,
            }
        )

    colorbar = make_colorbar_png(vmin, vmax, ash_units, cmap_name=cmap_name)

    bounds = [
        [float(np.min(lat)), float(np.min(lon))],
        [float(np.max(lat)), float(np.max(lon))],
    ]

    center_lat = float((np.min(lat) + np.max(lat)) / 2)
    center_lon = float((np.min(lon) + np.max(lon)) / 2)

    payload = {
        "frames": frames,
        "bounds": bounds,
        "center": [center_lat, center_lon],
        "semeru": [SEMERU_LAT, SEMERU_LON],
        "layer_label": layer_label,
        "vmin": vmin,
        "vmax": vmax,
        "units": ash_units,
        "colorbar": colorbar,
        "opacity": opacity,
        "nc_file": str(nc_path),
        "vaac_geojson": vaac_data,
        "vaac_geojson_file": str(vaac_geojson) if vaac_geojson else None,
        "mode": mode,
        "target_height": target_height,
    }

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<title>SILAM Semeru Ash Player + VAAC</title>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>

<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>

<style>
    html, body {{
        margin: 0;
        padding: 0;
        height: 100%;
        font-family: Arial, Helvetica, sans-serif;
        background: #0B2A4A;
    }}

    #map {{
        width: 100%;
        height: 100vh;
    }}

    .top-panel {{
        position: absolute;
        top: 14px;
        left: 14px;
        z-index: 1000;
        background: rgba(255,255,255,0.94);
        padding: 12px 14px;
        border-radius: 14px;
        box-shadow: 0 8px 24px rgba(0,0,0,0.22);
        min-width: 330px;
        max-width: 450px;
    }}

    .title {{
        font-weight: 800;
        font-size: 15px;
        color: #0B2A4A;
        margin-bottom: 4px;
    }}

    .subtitle {{
        font-size: 12px;
        color: #444;
        margin-bottom: 10px;
        line-height: 1.35;
    }}

    .control-row {{
        display: flex;
        gap: 8px;
        align-items: center;
        margin-bottom: 8px;
        flex-wrap: wrap;
    }}

    button {{
        border: none;
        background: #F28C28;
        color: white;
        padding: 7px 12px;
        border-radius: 10px;
        font-weight: 700;
        cursor: pointer;
    }}

    button:hover {{
        filter: brightness(0.95);
    }}

    .small-btn {{
        background: #0B2A4A;
    }}

    input[type="range"] {{
        width: 100%;
    }}

    .time-label {{
        font-size: 13px;
        font-weight: 700;
        color: #0B2A4A;
        margin-top: 6px;
    }}

    .legend {{
        position: absolute;
        right: 14px;
        bottom: 28px;
        z-index: 1000;
        background: rgba(255,255,255,0.94);
        padding: 10px 12px;
        border-radius: 14px;
        box-shadow: 0 8px 24px rgba(0,0,0,0.22);
        width: 430px;
        max-width: calc(100vw - 40px);
    }}

    .legend img {{
        width: 100%;
        display: block;
    }}

    .legend-note {{
        font-size: 11px;
        color: #333;
        line-height: 1.35;
        margin-top: 4px;
    }}

    .vaac-key {{
        margin-top: 8px;
        font-size: 11px;
        line-height: 1.5;
        color: #222;
    }}

    .key-row {{
        display: flex;
        align-items: center;
        gap: 6px;
    }}

    .key-line {{
        display: inline-block;
        width: 26px;
        height: 0;
        border-top: 3px solid #ff2d2d;
    }}

    .key-main {{
        border-top-color: #ff2d2d;
    }}

    .key-6 {{
        border-top-color: #ff9900;
        border-top-style: dashed;
    }}

    .key-12 {{
        border-top-color: #ffd000;
        border-top-style: dashed;
    }}

    .key-18 {{
        border-top-color: #00b7ff;
        border-top-style: dashed;
    }}

    .leaflet-control-layers {{
        border-radius: 12px !important;
    }}

    .leaflet-popup-content {{
        font-size: 12px;
        line-height: 1.35;
    }}

    @media (max-width: 700px) {{
        .top-panel {{
            left: 10px;
            right: 10px;
            max-width: none;
            min-width: 0;
        }}

        .legend {{
            left: 10px;
            right: 10px;
            bottom: 20px;
            width: auto;
        }}
    }}
</style>
</head>

<body>
<div id="map"></div>

<div class="top-panel">
    <div class="title">SILAM Semeru Ash Player + VAAC</div>
    <div class="subtitle">
        Variable: <b>{data["ash_var_name"]}</b><br/>
        View: <b>{layer_label}</b><br/>
        NetCDF: {Path(nc_path).name}<br/>
        VAAC: {Path(vaac_geojson).name if vaac_geojson else "not loaded"}
    </div>

    <div class="control-row">
        <button id="playBtn">Play</button>
        <button id="prevBtn">Prev</button>
        <button id="nextBtn">Next</button>
        <button id="fitAshBtn" class="small-btn">Fit SILAM</button>
        <button id="fitVaacBtn" class="small-btn">Fit VAAC</button>
    </div>

    <input id="timeSlider" type="range" min="0" max="{len(frames)-1}" value="0" step="1"/>
    <div class="time-label" id="timeLabel"></div>
</div>

<div class="legend">
    <img src="{colorbar}" alt="colorbar"/>
    <div class="legend-note">
        SILAM scale uses logarithmic normalization. Transparent areas represent zero or very low ash concentration.
    </div>
    <div class="vaac-key">
        <b>VAAC Darwin overlay</b>
        <div class="key-row"><span class="key-line key-main"></span> Observed / Estimated VA cloud</div>
        <div class="key-row"><span class="key-line key-6"></span> Forecast +6 hour</div>
        <div class="key-row"><span class="key-line key-12"></span> Forecast +12 hour</div>
        <div class="key-row"><span class="key-line key-18"></span> Forecast +18 hour</div>
    </div>
</div>

<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>

<script>
const payload = {json.dumps(payload)};

const map = L.map('map', {{
    center: payload.center,
    zoom: 8,
    preferCanvas: true
}});

const osm = L.tileLayer('https://tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
    maxZoom: 19,
    attribution: '&copy; OpenStreetMap contributors'
}});

const esri = L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{{z}}/{{y}}/{{x}}', {{
    maxZoom: 19,
    attribution: 'Tiles &copy; Esri'
}});

const topo = L.tileLayer('https://{{s}}.tile.opentopomap.org/{{z}}/{{x}}/{{y}}.png', {{
    maxZoom: 17,
    attribution: '&copy; OpenTopoMap contributors'
}});

osm.addTo(map);

const layerControl = L.control.layers({{
    "OpenStreetMap": osm,
    "ESRI Satellite": esri,
    "OpenTopoMap": topo
}}, {{}}, {{
    collapsed: false
}}).addTo(map);

const bounds = payload.bounds;

let currentIndex = 0;
let playing = false;
let timer = null;

let ashLayer = L.imageOverlay(
    payload.frames[0].image,
    bounds,
    {{
        opacity: payload.opacity,
        interactive: false
    }}
).addTo(map);

layerControl.addOverlay(ashLayer, "SILAM ash overlay");

map.fitBounds(bounds);

const semeruIcon = L.divIcon({{
    className: "",
    html: `<div style="
        width: 18px;
        height: 18px;
        background: #F28C28;
        border: 3px solid white;
        border-radius: 50%;
        box-shadow: 0 2px 10px rgba(0,0,0,0.45);
    "></div>`,
    iconSize: [18, 18],
    iconAnchor: [9, 9]
}});

const semeruMarker = L.marker(payload.semeru, {{ icon: semeruIcon }})
    .addTo(map)
    .bindPopup("<b>Gunung Semeru</b><br/>Lat: " + payload.semeru[0] + "<br/>Lon: " + payload.semeru[1]);

layerControl.addOverlay(semeruMarker, "Semeru marker");

function vaacStyle(feature) {{
    const match = feature.properties?.time_match_nc || "UNKNOWN";
    const t = (feature.properties && feature.properties.type) ? feature.properties.type : "";

    if (match === "NO") {{
        return {{
            color: "#808080",
            weight: 2,
            opacity: 0.85,
            fillColor: "#808080",
            fillOpacity: 0.06,
            dashArray: "2,8"
        }};
    }}

    if (t === "main_va_cloud") {{
        return {{
            color: "#ff2d2d",
            weight: 3,
            opacity: 0.98,
            fillColor: "#ff2d2d",
            fillOpacity: 0.16
        }};
    }}

    if (t.includes("forecast_plus_6")) {{
        return {{
            color: "#ff9900",
            weight: 2,
            opacity: 0.95,
            fillColor: "#ff9900",
            fillOpacity: 0.10,
            dashArray: "7,6"
        }};
    }}

    if (t.includes("forecast_plus_12")) {{
        return {{
            color: "#ffd000",
            weight: 2,
            opacity: 0.95,
            fillColor: "#ffd000",
            fillOpacity: 0.08,
            dashArray: "5,6"
        }};
    }}

    if (t.includes("forecast_plus_18")) {{
        return {{
            color: "#00b7ff",
            weight: 2,
            opacity: 0.95,
            fillColor: "#00b7ff",
            fillOpacity: 0.07,
            dashArray: "3,6"
        }};
    }}

    return {{
        color: "#ffffff",
        weight: 2,
        opacity: 0.9,
        fillColor: "#ffffff",
        fillOpacity: 0.05
    }};
}}

function vaacPointToLayer(feature, latlng) {{
    return L.circleMarker(latlng, {{
        radius: 7,
        color: "#ffffff",
        weight: 2,
        fillColor: "#F28C28",
        fillOpacity: 1
    }});
}}

function safeText(value) {{
    if (value === null || value === undefined || value === "") {{
        return "-";
    }}
    return String(value);
}}

function vaacPopup(feature, layer) {{
    const p = feature.properties || {{}};

    const html =
        "<b>VAAC Darwin</b><br/>" +
        "<b>Type:</b> " + safeText(p.type) + "<br/>" +
        "<b>Volcano:</b> " + safeText(p.volcano) + "<br/>" +
        "<b>Advisory:</b> " + safeText(p.advisory_nr) + "<br/>" +
        "<b>DTG:</b> " + safeText(p.dtg) + "<br/>" +
        "<b>DTG ISO:</b> " + safeText(p.dtg_iso) + "<br/>" +
        "<b>NC Range:</b> " + safeText(p.nc_start) + " to " + safeText(p.nc_end) + "<br/>" +
        "<b>VAAC in NC Range:</b> " + safeText(p.time_match_nc) + "<br/>" +
        "<b>Range Note:</b> " + safeText(p.time_match_note) + "<br/>" +
        "<b>Flight Level:</b> " + safeText(p.flight_level) + "<br/>" +
        "<b>Movement:</b> " + safeText(p.movement) + "<br/>" +
        "<hr style='margin:6px 0;'/>" +
        "<div style='max-width:340px; white-space:normal;'>" +
            safeText(p.cloud || p.source_elev) +
        "</div>";

    layer.bindPopup(html);
}}

let vaacLayer = null;

if (
    payload.vaac_geojson &&
    payload.vaac_geojson.features &&
    payload.vaac_geojson.features.length > 0
) {{
    vaacLayer = L.geoJSON(payload.vaac_geojson, {{
        style: vaacStyle,
        pointToLayer: vaacPointToLayer,
        onEachFeature: vaacPopup
    }}).addTo(map);

    layerControl.addOverlay(vaacLayer, "VAAC Darwin polygons");
}} else {{
    console.warn("No VAAC GeoJSON features loaded.");
}}

const timeSlider = document.getElementById("timeSlider");
const timeLabel = document.getElementById("timeLabel");
const playBtn = document.getElementById("playBtn");
const prevBtn = document.getElementById("prevBtn");
const nextBtn = document.getElementById("nextBtn");
const fitAshBtn = document.getElementById("fitAshBtn");
const fitVaacBtn = document.getElementById("fitVaacBtn");

function setFrame(idx) {{
    currentIndex = Math.max(0, Math.min(payload.frames.length - 1, idx));

    const frame = payload.frames[currentIndex];

    ashLayer.setUrl(frame.image);
    timeSlider.value = currentIndex;
    timeLabel.innerHTML =
        "Time: " + frame.label +
        " &nbsp; | &nbsp; Frame " + (currentIndex + 1) + "/" + payload.frames.length;
}}

function play() {{
    if (playing) return;

    playing = true;
    playBtn.innerText = "Pause";

    timer = setInterval(() => {{
        let next = currentIndex + 1;
        if (next >= payload.frames.length) {{
            next = 0;
        }}
        setFrame(next);
    }}, 900);
}}

function pause() {{
    playing = false;
    playBtn.innerText = "Play";

    if (timer) {{
        clearInterval(timer);
        timer = null;
    }}
}}

playBtn.addEventListener("click", () => {{
    if (playing) pause();
    else play();
}});

prevBtn.addEventListener("click", () => {{
    pause();
    setFrame(currentIndex - 1);
}});

nextBtn.addEventListener("click", () => {{
    pause();
    setFrame(currentIndex + 1);
}});

timeSlider.addEventListener("input", () => {{
    pause();
    setFrame(parseInt(timeSlider.value));
}});

fitAshBtn.addEventListener("click", () => {{
    map.fitBounds(bounds, {{ padding: [30, 30] }});
}});

fitVaacBtn.addEventListener("click", () => {{
    if (vaacLayer) {{
        try {{
            map.fitBounds(vaacLayer.getBounds(), {{ padding: [30, 30] }});
        }} catch (e) {{
            console.warn("VAAC bounds not available", e);
        }}
    }}
}});

setFrame(0);
</script>
</body>
</html>
"""

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")

    print("[OK] HTML player dibuat:")
    print(out_path)
    print()
    print("Buka dengan browser:")
    print(f"firefox {out_path}")


def main():
    parser = argparse.ArgumentParser(
        description="Create Leaflet player for SILAM ash NetCDF output with optional VAAC GeoJSON overlay."
    )

    parser.add_argument(
        "--nc",
        default="./output/semeru_ash_20260506_1000_test/ash_output.nc",
        help="Path ke ash_output.nc",
    )
    parser.add_argument(
        "--out",
        default="./output/semeru_ash_20260506_1000_test/ash_player.html",
        help="Path output HTML",
    )
    parser.add_argument(
        "--mode",
        choices=["max", "sum", "layer"],
        default="max",
        help="Mode visualisasi: max, sum, atau layer",
    )
    parser.add_argument(
        "--height",
        type=float,
        default=5000.0,
        help="Target height untuk mode layer, dalam meter",
    )
    parser.add_argument(
        "--opacity",
        type=float,
        default=0.85,
        help="Opacity overlay ash 0..1",
    )
    parser.add_argument(
        "--vaac-geojson",
        default=None,
        help="Path ke GeoJSON hasil parse_darwin_vaac.py",
    )
    parser.add_argument(
        "--cmap",
        default="inferno",
        help="Matplotlib colormap untuk SILAM ash overlay, default: inferno",
    )

    args = parser.parse_args()

    generate_html(
        nc_path=args.nc,
        out_path=args.out,
        mode=args.mode,
        target_height=args.height,
        opacity=args.opacity,
        vaac_geojson=args.vaac_geojson,
        cmap_name=args.cmap,
    )


if __name__ == "__main__":
    main()
