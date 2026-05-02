# app/config.py
import os
from dotenv import load_dotenv

load_dotenv()

class Settings:
    BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")
    API_BASE_URL: str = os.getenv("API_BASE_URL", "https://tapi.bale.ai")
    API_URL: str = f"{API_BASE_URL}/bot{BOT_TOKEN}/"
    
    REDIS_HOST: str = os.getenv("REDIS_HOST", "127.0.0.1")
    REDIS_PORT: int = int(os.getenv("REDIS_PORT", "6379"))
    REDIS_DB: int = int(os.getenv("REDIS_DB", "0"))
    
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
    
    MAX_PLAYERS: int = int(os.getenv("MAX_PLAYERS", "6"))
    MIN_PLAYERS: int = int(os.getenv("MIN_PLAYERS", "2"))
    
    # NEW: تنظیمات تایمر
    DEFAULT_TURN_TIMER: int = int(os.getenv("DEFAULT_TURN_TIMER", "60"))  # ۶۰ ثانیه پیش‌فرض
    DEFAULT_CHALLENGE_TIMER: int = int(os.getenv("DEFAULT_CHALLENGE_TIMER", "30"))  # ۳۰ ثانیه پیش‌فرض

settings = Settings()