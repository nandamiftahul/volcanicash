import cdsapi

c = cdsapi.Client()

c.retrieve(
    "reanalysis-era5-single-levels",
    {
        "product_type": "reanalysis",
        "variable": [
            "2m_temperature",
            "2m_dewpoint_temperature",
            "10m_u_component_of_wind",
            "10m_v_component_of_wind",
            "surface_pressure",
            "mean_sea_level_pressure",
            "large_scale_precipitation",
            "convective_precipitation",
            "total_cloud_cover",
            "land_sea_mask",
            "geopotential",
            "sea_ice_cover",
            "forecast_surface_roughness",
        ],
        "year": "2024",
        "month": "01",
        "day": "01",
        "time": ["00:00", "01:00"],
        "data_format": "grib",
        "area": [5, 100, -12, 120],
        "grid": [0.25, 0.25],
    },
    "era5_sfc_20240101_00_01.grib",
)
