# app/database.py
import json
import redis
from app.models import GameState

# اتصال به دیتابیس موقت Redis
redis_client = redis.Redis(host='127.0.0.1', port=6379, db=0, decode_responses=True)

def save_game(chat_id: int, game_data: GameState):
    """ذخیره وضعیت بازی یک گروه در ردیس (با انقضای 24 ساعته برای جلوگیری از پر شدن رم)"""
    redis_client.setex(
        f"game:{chat_id}", 
        86400, # 24 hours
        game_data.model_dump_json()
    )

def get_game(chat_id: int) -> GameState | None:
    """دریافت وضعیت بازی یک گروه از ردیس"""
    data = redis_client.get(f"game:{chat_id}")
    if data:
        return GameState.model_validate_json(data)
    return None

def delete_game(chat_id: int):
    """پاک کردن بازی پس از اتمام"""
    redis_client.delete(f"game:{chat_id}")
