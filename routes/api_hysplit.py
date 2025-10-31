from flask import Blueprint, request, jsonify
from core.hysplit_fetcher import fetch_hysplit_trajectory

bp = Blueprint("hysplit", __name__)

@bp.route("/api/hysplit_trajectory", methods=["GET"])
def api_hysplit():
    try:
        lat = float(request.args.get("lat"))
        lon = float(request.args.get("lon"))
        start_hour = int(request.args.get("hour", 0))
        height = float(request.args.get("alt", 10000))
        duration = int(request.args.get("hours", 12))
    except Exception as e:
        return jsonify({"error": "Invalid parameter: "+str(e)}), 400

    try:
        result = fetch_hysplit_trajectory(lat, lon, start_hour, height, duration)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": "Failed to fetch HYSPLIT: "+str(e)}), 500
