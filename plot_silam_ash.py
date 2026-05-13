#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from scipy.io import netcdf_file
import numpy as np
import matplotlib.pyplot as plt
from pathlib import Path

nc_path = Path("./output/semeru_ash_20260506_1000_test/ash_output.nc")

f = netcdf_file(nc_path, "r", mmap=False)

lon = f.variables["lon"].data.copy()
lat = f.variables["lat"].data.copy()
height = f.variables["height"].data.copy()
time = f.variables["time"].data.copy()

# Variable utama SILAM kamu
ash = f.variables["cnc_ash_m3_0"].data.copy()
# shape: time, height, lat, lon

f.close()

print("lon:", lon.min(), lon.max(), lon.shape)
print("lat:", lat.min(), lat.max(), lat.shape)
print("height:", height)
print("time:", time)
print("ash shape:", ash.shape)
print("ash min/max:", np.nanmin(ash), np.nanmax(ash))

# Ambil timestep terakhir
t_idx = -1

# Buat peta maksimum konsentrasi terhadap semua layer ketinggian
ash_max_height = np.nanmax(ash[t_idx, :, :, :], axis=0)

plt.figure(figsize=(9, 7))
plt.pcolormesh(lon, lat, ash_max_height, shading="auto")
plt.colorbar(label="Ash concentration max over height (kg/m3)")
plt.xlabel("Longitude")
plt.ylabel("Latitude")
plt.title("SILAM Semeru Ash - Max Concentration Over Height")
plt.scatter([112.917], [-8.100], marker="^", s=80, label="Semeru")
plt.legend()
plt.tight_layout()
plt.savefig("semeru_ash_max_height.png", dpi=200)
plt.show()
