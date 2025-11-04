# routes/api.py
from flask import Blueprint, request, jsonify
from datetime import datetime, timedelta
import numpy as np, requests

# --- Core modules ---
from core.ash_model import simulate_ash_trajectory
from core.hysplit_fetcher import fetch_hysplit_trajectory

bp = Blueprint("api", __name__)

# -------------------------------------------------------------
# 1️⃣ Custom ash trajectory (model buatan sendiri)
# -------------------------------------------------------------
@bp.route("/api/ash_trajectory", methods=["GET"])
def api_custom_ash():
    """
    Simulasi penyebaran abu menggunakan model custom (non-NOAA).
    Parameter:
      - volcano: nama gunung (merapi, semeru, bromo, kelud, ...)
      - hours: durasi jam
      - alt: ketinggian awal puncak plume (meter)
      - particles: jumlah partikel simulasi
    """
    volcano = request.args.get("volcano", "merapi").lower()
    hours = int(request.args.get("hours", 12))
    alt = float(request.args.get("alt", 10000))
    particles = int(request.args.get("particles", 1))

    try:
        # Simulasi langsung; fungsi sudah mendukung multi-particle
        data = simulate_ash_trajectory(
            volcano=volcano,
            hours=hours,
            plume_top_m=alt,
            particles=particles
        )
        return jsonify(data)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": f"Internal error: {e}"}), 500


# -------------------------------------------------------------
# 2️⃣ Real / mock NOAA HYSPLIT trajectory
# -------------------------------------------------------------
@bp.route("/api/hysplit_trajectory", methods=["GET"])
def api_hysplit():
    """
    NOAA HYSPLIT trajectory fetcher (mock atau real).
    Mendukung multi-partikel di sekitar titik pusat gunung.
    """
    try:
        lat = float(request.args.get("lat", 0))
        lon = float(request.args.get("lon", 0))
        start_hour = int(request.args.get("hour", 0))
        height = float(request.args.get("alt", 10000))
        duration = int(request.args.get("hours", 12))
        particles = int(request.args.get("particles", 1))
        volcano = request.args.get("volcano", "merapi").lower()
    except Exception as e:
        return jsonify({"error": "Invalid parameter: " + str(e)}), 400

    # 2️⃣ fallback ke koordinat gunung jika lat/lon tidak diisi
    if (lat == 0.0 and lon == 0.0):
        if volcano in VOLCANO_DB:
            lat = VOLCANO_DB[volcano]["lat"]
            lon = VOLCANO_DB[volcano]["lon"]
        else:
            return jsonify({"error": f"Unknown volcano '{volcano}'. Provide lat/lon or known volcano."}), 400

    try:
        # Single particle
        if particles <= 1:
            result = fetch_hysplit_trajectory(lat, lon, start_hour, height, duration, 1)
            result.setdefault("meta", {})
            result["meta"].update({
                "particles": 1,
                "center": [lat, lon],
                "height": height,
                "duration_hr": duration,
                "volcano": volcano
            })
            return jsonify(result)

        # Multi-particle swarm
        trips = []
        for i in range(particles):
            dlat = np.random.uniform(-0.05, 0.05)   # ±5–6 km
            dlon = np.random.uniform(-0.05, 0.05)
            dalt = height + np.random.uniform(-500, 500)
            traj = fetch_hysplit_trajectory(lat + dlat, lon + dlon, start_hour, dalt, duration, 1)
            trips.extend(traj.get("trips", []))  # ✅ fix: gunakan "trips" bukan "trip"

        return jsonify({
            "meta": {
                "source": "hysplit_mock",
                "particles": particles,
                "center": [lat, lon],
                "height": height,
                "duration_hr": duration,
                "volcano": volcano
            },
            "trips": trips
        })

    except Exception as e:
        return jsonify({"error": "Failed to fetch HYSPLIT: " + str(e)}), 500


# -------------------------------------------------------------
# 3️⃣ Ash dispersion grid simulation (Open-Meteo wind)
# -------------------------------------------------------------
VOLCANO_DB = {
    "merapi": {"lat": -7.54, "lon": 110.446},
    "semeru": {"lat": -8.108, "lon": 112.922},
    "bromo": {"lat": -7.942, "lon": 112.953},
    "kelud": {"lat": -7.934, "lon": 112.308},
}

@bp.route("/api/ash_dispersion", methods=["GET"])
def api_dispersion():
    """
    Simulasi dispersi abu berdasarkan data angin Open-Meteo (GFS).
    """
    volcano = request.args.get("volcano", "merapi").lower()
    hours = int(request.args.get("hours", 12))
    alt_ft = int(request.args.get("alt", 20000))
    if volcano not in VOLCANO_DB:
        return jsonify({"error": "Unknown volcano"}), 400

    src = VOLCANO_DB[volcano]
    lat0, lon0 = src["lat"], src["lon"]

    url = f"https://api.open-meteo.com/v1/gfs?latitude={lat0}&longitude={lon0}&hourly=windspeed_100m,winddirection_100m"
    try:
        r = requests.get(url, timeout=10)
        r.raise_for_status()
    except Exception as e:
        return jsonify({"error": f"Failed to get wind data: {e}"}), 500

    meteo = r.json()
    u_speed = meteo["hourly"]["windspeed_100m"][0]
    u_dir = meteo["hourly"]["winddirection_100m"][0]
    rad = np.deg2rad(u_dir)
    u = np.sin(rad) * u_speed / 111
    v = np.cos(rad) * u_speed / 111

    levels = np.arange(0.5, 10.5, 0.5)
    ash_points = []
    for t in range(hours):
        lon = lon0 + u * (t + 1)
        lat = lat0 + v * (t + 1)
        spread = 0.1 + 0.02 * t
        for i in range(30):
            lon_j = lon + np.random.uniform(-spread, spread)
            lat_j = lat + np.random.uniform(-spread, spread)
            alt_j = max(0.5, np.random.choice(levels))
            ash_points.append([lon_j, lat_j, alt_j, hours - t])

    return jsonify({
        "meta": {
            "source": "open-meteo gfs",
            "volcano": volcano,
            "duration_hr": hours,
            "altitude_ft": alt_ft,
            "u_speed": round(u_speed, 2),
            "u_dir": round(u_dir, 1)
        },
        "points": ash_points,
        "levels_km": list(levels)
    })

@bp.route("/api/ash_trajectory_multi", methods=["GET"])
def api_custom_ash_multi():
    from flask import jsonify, request
    from core.ash_model import simulate_ash_trajectory

    volcano = request.args.get("volcano", "merapi").lower()
    hours = int(request.args.get("hours", 12))
    alt = float(request.args.get("alt", 10000))
    particles = int(request.args.get("particles", 10))

    alt_levels = [3000, 5000, 8000, 10000, 12000]
    alt_levels = [a for a in alt_levels if a <= alt + 2000]

    all_trips = []
    for alt_m in alt_levels:
        sim = simulate_ash_trajectory(volcano=volcano, hours=hours, plume_top_m=alt_m, particles=particles)
        for t in sim["trips"]:
            t["level"] = alt_m
        all_trips.extend(sim["trips"])

    return jsonify({
        "meta": {
            "source": "custom_model_multi",
            "volcano": volcano,
            "duration_hr": hours,
            "levels": alt_levels,
            "particles_per_level": particles,
            "total_trips": len(all_trips)
        },
        "trips": all_trips
    })

@bp.route("/api/hysplit_trajectory_multi")
def api_hysplit_trajectory_multi():
    from core.hysplit_fetcher import fetch_hysplit_trajectory
    from core.ash_model import VOLCANOES

    volcano = request.args.get("volcano", "merapi")
    hours = int(request.args.get("hours", 12))
    alt_top = float(request.args.get("alt", 10000))
    particles = int(request.args.get("particles", 5))

    levels = [3000, 5000, 8000, 10000, 12000]

    if volcano not in VOLCANOES:
        return jsonify({"error": f"Gunung '{volcano}' tidak dikenal"}), 400

    v = VOLCANOES[volcano]
    lat, lon = v["lat"], v["lon"]

    all_trips = []

    # === Loop untuk tiap level ===
    for lvl in levels:
        try:
            res = fetch_hysplit_trajectory(lat, lon, start_hour=0, height=lvl, duration=hours, particles=particles)
            for t in res.get("trips", []):
                t["level"] = lvl
                all_trips.append(t)
        except Exception as e:
            print(f"[ERR] Failed level {lvl}: {e}")

    return jsonify({
        "meta": {
            "source": "hysplit_mock_multi",
            "volcano": volcano,
            "duration_hr": hours,
            "levels": levels,
            "particles_per_level": particles,
            "total_trips": len(all_trips)
        },
        "trips": all_trips
    })
