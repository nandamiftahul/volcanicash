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
    volcano = request.args.get("volcano", "merapi").lower()
    hours = int(request.args.get("hours", 12))
    alt = float(request.args.get("alt", 10000))
    try:
        data = simulate_ash_trajectory(volcano, hours, alt)
        return jsonify(data)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


# -------------------------------------------------------------
# 2️⃣ Real / mock NOAA HYSPLIT trajectory
# -------------------------------------------------------------
@bp.route("/api/hysplit_trajectory", methods=["GET"])
def api_hysplit():
    """
    Real NOAA HYSPLIT fetcher (via core.hysplit_fetcher)
    """
    try:
        lat = float(request.args.get("lat", 0))
        lon = float(request.args.get("lon", 0))
        start_hour = int(request.args.get("hour", 0))
        height = float(request.args.get("alt", 10000))
        duration = int(request.args.get("hours", 12))
    except Exception as e:
        return jsonify({"error": "Invalid parameter: " + str(e)}), 400

    try:
        result = fetch_hysplit_trajectory(lat, lon, start_hour, height, duration)
        return jsonify(result)
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
    volcano = request.args.get("volcano", "merapi").lower()
    hours = int(request.args.get("hours", 12))
    alt_ft = int(request.args.get("alt", 20000))
    if volcano not in VOLCANO_DB:
        return jsonify({"error": "Unknown volcano"}), 400

    src = VOLCANO_DB[volcano]
    lat0, lon0 = src["lat"], src["lon"]

    # Fetch real wind from Open-Meteo
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
            "u_dir": round(u_dir, 1),
        },
        "points": ash_points,
        "levels_km": list(levels)
    })


# -------------------------------------------------------------
# 4️⃣ Flight Level Safety (SAFE / UNSAFE)
# -------------------------------------------------------------
@bp.route("/api/flight_safety", methods=["GET"])
def api_flight_safety():
    volcano = request.args.get("volcano", "merapi").lower()
    res = requests.get(f"http://127.0.0.1:5000/api/ash_dispersion?volcano={volcano}&hours=12")
    data = res.json()
    levels = data.get("levels_km", [])
    safety = {}
    for lvl in levels:
        unsafe_ratio = np.random.uniform(0, 1)
        safety[f"FL{int(lvl*100)}"] = "UNSAFE" if unsafe_ratio > 0.7 else "SAFE"
    return jsonify({"volcano": volcano, "safety": safety})


# -------------------------------------------------------------
# 5️⃣ Weather wind (profil arah/kecepatan)
# -------------------------------------------------------------
@bp.route("/api/weather_wind", methods=["GET"])
def api_weather_wind():
    lat = float(request.args.get("lat", -7.54))
    lon = float(request.args.get("lon", 110.44))
    url = f"https://api.open-meteo.com/v1/gfs?latitude={lat}&longitude={lon}&hourly=windspeed_100m,winddirection_100m"
    try:
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        meteo = r.json()
        return jsonify({
            "lat": lat, "lon": lon,
            "windspeed_100m": meteo["hourly"]["windspeed_100m"][:12],
            "winddir_100m": meteo["hourly"]["winddirection_100m"][:12],
            "timestamps": meteo["hourly"]["time"][:12]
        })
    except Exception as e:
        return jsonify({"error": f"Failed to get wind data: {e}"}), 500


# -------------------------------------------------------------
# 6️⃣ Radiosonde profile (dummy untuk validasi vertikal)
# -------------------------------------------------------------
@bp.route("/api/radiosonde_profile", methods=["GET"])
def api_radiosonde_profile():
    alt = np.arange(0, 12, 0.5)
    temp = 30 - 6.5 * alt
    rh = np.clip(90 - 5 * alt, 20, 90)
    wind = 5 + 2 * alt
    return jsonify({
        "alt_km": list(alt),
        "temperature_C": list(temp),
        "rh_percent": list(rh),
        "wind_mps": list(wind)
    })
