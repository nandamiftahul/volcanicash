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
                            particles: int = 10,
                            wind_field=None):
    """
    Simulasi penyebaran abu vulkanik di atas Pulau Jawa.
    Sekarang mendukung banyak partikel (trips) agar terlihat menyebar di visualisasi 3D.
    """

    if volcano not in VOLCANOES:
        raise ValueError(f"Gunung '{volcano}' tidak dikenal. Pilih salah satu: {list(VOLCANOES)}")

    v = VOLCANOES[volcano]
    base_lat, base_lon = v["lat"], v["lon"]
    dt = 3600  # 1 jam

    if wind_field is None:
        wind_field = get_dummy_wind_field()

    now = datetime.utcnow()
    timestamps = [(now + timedelta(hours=i)).timestamp() for i in range(hours + 1)]
    trips = []

    # === Loop tiap partikel abu ===
    for p in range(particles):
        lat = base_lat
        lon = base_lon
        alt = plume_top_m

        # variasi kecil tiap partikel (arah, kecepatan, sink rate)
        angle_dev = np.radians(np.random.uniform(-15, 15))   # derajat deviasi
        speed_factor = np.random.uniform(0.8, 1.2)
        sink_factor = np.random.uniform(0.8, 1.2)
        path = []

        for i in range(hours + 1):
            path.append([lon, lat, alt])

            # ambil u,v sesuai tinggi
            u = np.interp(alt, wind_field["z"], wind_field["u"]) * speed_factor
            v_ = np.interp(alt, wind_field["z"], wind_field["v"]) * speed_factor

            # rotasi arah sedikit agar tiap partikel menyebar
            u_rot = u * np.cos(angle_dev) - v_ * np.sin(angle_dev)
            v_rot = u * np.sin(angle_dev) + v_ * np.cos(angle_dev)

            # ubah ke derajat per jam
            dlat = (v_rot * dt) / 111000
            dlon = (u_rot * dt) / (111000 * np.cos(np.radians(lat)))

            lat += dlat
            lon += dlon
            alt = max(0, alt - 150 * sink_factor)

        trips.append({
            "path": path,
            "timestamps": timestamps
        })

    return {
        "meta": {
            "source": "custom_model",
            "region": "Java Island",
            "volcano": volcano,
            "start": now.isoformat(),
            "duration_hr": hours,
            "top_alt_m": plume_top_m,
            "particles": particles
        },
        "trips": trips
    }

if __name__ == "__main__":
    traj = simulate_ash_trajectory("semeru", hours=12, plume_top_m=12000)
    print(f"Trajectory ({traj['meta']['volcano']}): {len(traj['trip']['path'])} points")
    print("Last position:", traj["trip"]["path"][-1])
