import requests
from datetime import datetime, timedelta
import numpy as np

def fetch_hysplit_trajectory(lat, lon, start_hour, height, duration):
    """
    Fetch or simulate NOAA HYSPLIT trajectory.
    Saat NOAA offline / gagal fetch, gunakan dummy simulasi agar tetap jalan.
    """
    try:
        # 🛰️ Contoh placeholder API NOAA (belum resmi, hanya template)
        # url = f"https://example.noaa.gov/hysplit_api?lat={lat}&lon={lon}&height={height}&hours={duration}"
        # resp = requests.get(url, timeout=10)
        # resp.raise_for_status()
        # data = resp.json()
        #
        # Untuk sekarang, kita pakai dummy sampai server NOAA ready:
        now = datetime.utcnow()
        path, timestamps = [], []
        alt = height
        for i in range(duration + 1):
            timestamps.append((now + timedelta(hours=i)).timestamp())
            path.append([lon + 0.15*i, lat - 0.05*i, max(0, alt - 200*i)])
        return {
            "meta": {
                "source": "hysplit_mock",
                "region": "Java Island",
                "volcano": "custom",
                "start": now.isoformat(),
                "duration_hr": duration,
                "top_alt_m": height
            },
            "trip": {"path": path, "timestamps": timestamps}
        }

    except requests.exceptions.RequestException as e:
        # Jika gagal koneksi NOAA, fallback ke dummy
        print(f"[WARN] HYSPLIT NOAA fetch failed: {e}")
        now = datetime.utcnow()
        path, timestamps = [], []
        alt = height
        for i in range(duration + 1):
            timestamps.append((now + timedelta(hours=i)).timestamp())
            path.append([lon + 0.15*i, lat - 0.05*i, max(0, alt - 200*i)])
        return {
            "meta": {
                "source": "hysplit_offline_fallback",
                "region": "Java Island",
                "volcano": "custom",
                "start": now.isoformat(),
                "duration_hr": duration,
                "top_alt_m": height
            },
            "trip": {"path": path, "timestamps": timestamps}
        }
