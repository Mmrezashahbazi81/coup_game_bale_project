# app/config.py
import os
from dotenv import load_dotenv

# NEW: لود کردن .env
load_dotenv()

class Settings:
    # NEW: خوندن از environment variables
    BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")
    API_BASE_URL: str = os.getenv("API_BASE_URL", "https://tapi.bale.ai")
    API_URL: str = f"{API_BASE_URL}/bot{BOT_TOKEN}/"
    
    # NEW: تنظیمات Redis
    REDIS_HOST: str = os.getenv("REDIS_HOST", "127.0.0.1")
    REDIS_PORT: int = int(os.getenv("REDIS_PORT", "6379"))
    REDIS_DB: int = int(os.getenv("REDIS_DB", "0"))
    
    # NEW: تنظیمات لاگ
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
    
    # NEW: تنظیمات بازی
    MAX_PLAYERS: int = int(os.getenv("MAX_PLAYERS", "6"))
    MIN_PLAYERS: int = int(os.getenv("MIN_PLAYERS", "3"))

# NEW: ساخت instance از settings
settings = Settings()