# routes/api_ash.py
from flask import Blueprint, request, jsonify
from core.ash_model import simulate_ash_trajectory

bp = Blueprint("ash", __name__)

@bp.route("/api/ash_trajectory", methods=["GET"])
def api_custom_ash():
    """
    Simulasi abu vulkanik berdasarkan model kustom.
    Contoh: /api/ash_trajectory?volcano=semeru&hours=12&alt=12000
    """
    volcano = request.args.get("volcano", "merapi").lower()
    hours = int(request.args.get("hours", 12))
    alt = float(request.args.get("alt", 10000))
    try:
        data = simulate_ash_trajectory(volcano, hours, alt)
        return jsonify(data)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@bp.route("/api/hysplit_trajectory", methods=["GET"])
def api_hysplit_ash():
    """
    Placeholder untuk data real NOAA/HYSPLIT (akan diisi nanti).
    Sekarang pakai dummy hasil agar bisa dibandingkan di frontend.
    """
    volcano = request.args.get("volcano", "merapi").lower()
    hours = int(request.args.get("hours", 12))
    alt = float(request.args.get("alt", 10000))

    # Dummy lintasan NOAA (arah sedikit berbeda dari custom)
    import numpy as np
    from datetime import datetime, timedelta
    now = datetime.utcnow()
    path, timestamps = [], []
    lon, lat = 110.4, -7.54  # Merapi
    for i in range(hours + 1):
        timestamps.append((now + timedelta(hours=i)).timestamp())
        path.append([lon, lat, alt])
        lon += 0.15
        lat -= 0.05
        alt = max(0, alt - 200)

    return jsonify({
        "meta": {
            "source": "noaa_hysplit_mock",
            "region": "Java Island",
            "volcano": volcano,
            "start": now.isoformat(),
            "duration_hr": hours,
            "top_alt_m": alt
        },
        "trip": {"path": path, "timestamps": timestamps}
    })
