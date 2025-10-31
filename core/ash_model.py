import numpy as np
from datetime import datetime, timedelta

# 🔹 Daftar gunung utama di Pulau Jawa
VOLCANOES = {
    "merapi":  {"lat": -7.540, "lon": 110.446, "elev": 2930},
    "semeru":  {"lat": -8.108, "lon": 112.922, "elev": 3676},
    "bromo":   {"lat": -7.942, "lon": 112.953, "elev": 2329},
    "kelud":   {"lat": -7.934, "lon": 112.308, "elev": 1731},
    "ijen":    {"lat": -8.058, "lon": 114.242, "elev": 2799},
    "slamet":  {"lat": -7.242, "lon": 109.208, "elev": 3428},
    "tangkuban": {"lat": -6.759, "lon": 107.606, "elev": 2084},
}


def get_dummy_wind_field():
    """
    Angin idealisasi di atas Pulau Jawa:
    - Lapisan bawah: angin timuran (E→W)
    - Lapisan tengah: angin baratan (W→E)
    - Lapisan atas: angin utara
    """
    z = np.array([0, 2000, 5000, 8000, 12000, 15000])
    u = np.array([-4, -2, 2, 6, 10, 8])   # arah barat (+) ke timur
    v = np.array([1, 1, 2, 1, 0, -2])     # arah selatan (+) ke utara
    return {"z": z, "u": u, "v": v}


def simulate_ash_trajectory(volcano: str = "merapi",
                            hours: int = 12,
                            plume_top_m: float = 10000.0,
                            wind_field=None):
    """
    Simulasi penyebaran abu vulkanik di atas Pulau Jawa (model sederhana).
    """

    if volcano not in VOLCANOES:
        raise ValueError(f"Gunung '{volcano}' tidak dikenal. Pilih salah satu: {list(VOLCANOES)}")

    v = VOLCANOES[volcano]
    lat, lon, alt = v["lat"], v["lon"], plume_top_m
    dt = 3600  # 1 jam

    if wind_field is None:
        wind_field = get_dummy_wind_field()

    now = datetime.utcnow()
    timestamps = []
    path = []

    for i in range(hours + 1):
        timestamps.append((now + timedelta(hours=i)).timestamp())
        path.append([lon, lat, alt])

        u = np.interp(alt, wind_field["z"], wind_field["u"])
        v_ = np.interp(alt, wind_field["z"], wind_field["v"])

        # ubah m/s → derajat
        dlat = (v_ * dt) / 111000
        dlon = (u * dt) / (111000 * np.cos(np.radians(lat)))

        lat += dlat
        lon += dlon
        alt = max(0, alt - 150)  # sink 150 m/jam

    return {
        "meta": {
            "source": "custom_model",
            "region": "Java Island",
            "volcano": volcano,
            "start": now.isoformat(),
            "duration_hr": hours,
            "top_alt_m": plume_top_m
        },
        "trip": {
            "path": path,
            "timestamps": timestamps
        }
    }


if __name__ == "__main__":
    traj = simulate_ash_trajectory("semeru", hours=12, plume_top_m=12000)
    print(f"Trajectory ({traj['meta']['volcano']}): {len(traj['trip']['path'])} points")
    print("Last position:", traj["trip"]["path"][-1])
