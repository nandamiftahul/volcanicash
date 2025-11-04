import requests
from datetime import datetime, timedelta
import numpy as np

def fetch_hysplit_trajectory(lat, lon, start_hour, height, duration, particles=10):
    """
    Fetch atau simulasi multi-particle NOAA HYSPLIT trajectory (dummy realistis).
    Jika NOAA fetch gagal, gunakan dummy multi-partikel agar tetap visual.
    """
    now = datetime.utcnow()

    try:
        # 🛰️ Placeholder API (belum aktif)
        # url = f"https://example.noaa.gov/hysplit_api?lat={lat}&lon={lon}&height={height}&hours={duration}"
        # resp = requests.get(url, timeout=10)
        # resp.raise_for_status()
        # data = resp.json()
        # return data
        raise requests.exceptions.RequestException("NOAA placeholder not implemented")

    except requests.exceptions.RequestException as e:
        print(f"[WARN] HYSPLIT NOAA fetch failed: {e}")

        # === fallback dummy multi-particle ===
        timestamps = [(now + timedelta(hours=i)).timestamp() for i in range(duration + 1)]
        trips = []

        for p in range(particles):
            path = []
            lon_p, lat_p = lon, lat
            alt_p = height

            # variasi arah & kecepatan horizontal antar partikel
            angle = np.deg2rad(100 + np.random.uniform(-30, 30))  # arah utama timur dengan variasi
            speed = 35 + np.random.uniform(-10, 15)               # km/jam ± variasi
            sink = np.random.uniform(150, 250)                    # m/jam variasi

            u = np.cos(angle) * speed / 111                       # lon offset per jam
            v = np.sin(angle) * speed / 111                       # lat offset per jam

            for i in range(duration + 1):
                path.append([lon_p, lat_p, max(0, alt_p)])
                lon_p += u
                lat_p += v
                alt_p -= sink

            trips.append({"path": path, "timestamps": timestamps})

        return {
            "meta": {
                "source": "hysplit_mock",
                "region": "Java Island",
                "volcano": "custom",
                "start": now.isoformat(),
                "duration_hr": duration,
                "top_alt_m": height,
                "particles": particles
            },
            "trips": trips
        }