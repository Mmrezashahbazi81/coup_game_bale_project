# app/database.py
import json
import redis
import logging
from app.models import GameStateModel
from app.config import settings

logger = logging.getLogger(__name__)

redis_client = redis.Redis(
    host=settings.REDIS_HOST,
    port=settings.REDIS_PORT,
    db=settings.REDIS_DB,
    decode_responses=True
)

def save_game(chat_id: int, game_data):
    """ذخیره وضعیت بازی یک گروه در ردیس"""
    try:
        redis_client.setex(
            f"game:{chat_id}", 
            86400,  # 24 hours
            game_data.model_dump_json()
        )
        logger.info(f"Game saved: chat_id={chat_id}")
    except Exception as e:
        logger.error(f"Error saving game {chat_id}: {e}")

def get_game(chat_id: int):
    """دریافت وضعیت بازی یک گروه از ردیس"""
    try:
        data = redis_client.get(f"game:{chat_id}")
        if data:
            return GameStateModel.model_validate_json(data)
        return None
    except Exception as e:
        logger.error(f"Error loading game {chat_id}: {e}")
        return None

def delete_game(chat_id: int):
    """پاک کردن بازی پس از اتمام"""
    redis_client.delete(f"game:{chat_id}")
    logger.info(f"Game deleted: chat_id={chat_id}")