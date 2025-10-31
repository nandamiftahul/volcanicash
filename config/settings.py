import os
from dotenv import load_dotenv

# === Load .env file ===
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
ENV_PATH = os.path.join(BASE_DIR, ".env")
load_dotenv(ENV_PATH)

# === Flask core settings ===
DEBUG = os.getenv("FLASK_DEBUG", "true").lower() == "true"
SECRET_KEY = os.getenv("SECRET_KEY", "volcanic_default_secret")

# === Server port ===
PORT = int(os.getenv("PORT", 5000))

# === NOAA API ===
NOAA_API_KEY = os.getenv("API_KEY_NOAA", "")
NOAA_BASE_URL = os.getenv("NOAA_BASE_URL", "https://apps.arl.noaa.gov/ready2/api/v1/trajectory")

# === Default model params ===
DEFAULT_MODEL_DURATION = int(os.getenv("DEFAULT_MODEL_DURATION", 12))  # jam
DEFAULT_PLUME_HEIGHT_M = int(os.getenv("DEFAULT_PLUME_HEIGHT_M", 10000))

# === Volcano coordinates ===
VOLCANOES = {
    "merapi": {"lat": -7.540, "lon": 110.446},
    "semeru": {"lat": -8.108, "lon": 112.922},
    "bromo":  {"lat": -7.942, "lon": 112.953},
    "kelud":  {"lat": -7.93,  "lon": 112.308},
}
