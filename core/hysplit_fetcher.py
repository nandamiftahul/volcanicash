import requests
import json
from datetime import datetime, timedelta

API_BASE = "https://apps.arl.noaa.gov/ready2"
API_KEY = "YOUR_API_KEY_HERE"

def fetch_hysplit_trajectory(lat: float, lon: float, start_hour: int,
                              plume_height_m: float, duration_hr: int = 12):
    """
    Panggil READY API untuk menjalankan model HYSPLIT forward trajectory.
    Return JSON dengan struktur { meta: {...}, trip: { path: [...], timestamps: [...] } }
    """
    endpoint = f"{API_BASE}/api/v1/trajectory"
    body = {
        "apiKey": API_KEY,
        "meteorologicalData": "GFS0p25",
        "latitude": lat,
        "longitude": lon,
        "elevation": plume_height_m,
        "startDate": datetime.utcnow().strftime("%Y-%m-%d"),
        "startHour": start_hour,
        "duration": duration_hr,
        "direction": "forward"
    }
    resp = requests.post(endpoint, json=body)
    resp.raise_for_status()
    data = resp.json()

    # lakukan parsing ke struktur yang frontend harapkan
    path = []
    timestamps = []
    # contoh mapping — tergantung format sebenarnya
    for record in data.get("trajectoryData", []):
        timestamps.append(record["time"])
        path.append([ record["longitude"], record["latitude"], record["altitude_m"] ])

    return {
        "meta": {
            "source": "noaa_hysplit",
            "start": body["startDate"] + f" {start_hour:02d}Z",
            "duration_hr": duration_hr,
            "volcano_lat": lat,
            "volcano_lon": lon,
            "plume_top_m": plume_height_m
        },
        "trip": {
            "path": path,
            "timestamps": timestamps
        }
    }
