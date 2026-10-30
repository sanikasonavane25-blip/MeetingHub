"""All settings come from environment variables (or a local .env file)."""
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

AI_PROVIDER = os.getenv("AI_PROVIDER", "anthropic").lower()
AI_API_KEY = os.getenv("AI_API_KEY", "").strip()
AI_MODEL = os.getenv("AI_MODEL", "claude-sonnet-5-5")
AI_TIMEOUT = float(os.getenv("AI_TIMEOUT_SECONDS", "30"))
AI_RETRIES = int(os.getenv("AI_RETRIES", "1"))

DATA_DIR = Path(os.getenv("DATA_DIR", BASE_DIR / "data"))
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{DATA_DIR.as_posix()}/app.db")
LOG_DIR = Path(os.getenv("LOG_DIR", BASE_DIR / "logs"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

STALL_DAYS = int(os.getenv("STALL_DAYS", "5"))  # no update for N days => stalled
SEED_DEMO_DATA = os.getenv("SEED_DEMO_DATA", "true").lower() == "true"
