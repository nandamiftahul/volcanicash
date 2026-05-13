import cdsapi

c = cdsapi.Client()

c.retrieve(
    "reanalysis-era5-complete",
    {
        "class": "ea",
        "date": "2024-01-01",
        "expver": "1",
        "levtype": "ml",
        "levelist": "1/to/137",
        "param": "129/130/131/132/133/135/152",
        "stream": "oper",
        "time": "00:00:00/01:00:00",
        "type": "an",
        "data_format": "grib",
        "area": "5/100/-12/120",
        "grid": "0.25/0.25",
    },
    "era5_ml_20240101_00_01.grib",
)
