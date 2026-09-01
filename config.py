import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

DB_PATH = str(BASE_DIR / "data" / "ipo.db")

IPOJI_BASE_URL = os.getenv(
    "IPOJI_BASE_URL",
    "https://www.ipoji.com"
)

IPOJI_CURRENT_IPO_URL = os.getenv(
    "IPOJI_CURRENT_IPO_URL",
    "/ipo/current-ipo"
)

REQUEST_TIMEOUT = int(
    os.getenv("REQUEST_TIMEOUT", "20")
)
