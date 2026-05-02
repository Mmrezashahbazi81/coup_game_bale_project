# app/models.py
from pydantic import BaseModel, Field
from typing import List, Dict, Optional

class Player(BaseModel):
    user_id: int
    name: str
    coins: int = 2
    cards: List[str] = []         # کارت‌های در دست بازیکن
    dead_cards: List[str] = []    # کارت‌های سوخته
    is_alive: bool = True

class GameState(BaseModel):
    chat_id: int
    creator_id: int
    mode: str = "classic"         # classic یا expansion
    state: str = "LOBBY"          # وضعیت فعلی: LOBBY, PLAYING, WAITING_TARGET و...
    players: Dict[str, Player] = {} # دیکشنری از آیدی بازیکن به اطلاعاتش
    player_order: List[int] = []  # ترتیب نوبت بازیکنان
    current_turn_index: int = 0   # نوبت کدام بازیکن در آرایه بالا است
    deck: List[str] = []          # کارت‌های باقی‌مانده در مخزن
    
    # متغیرهای مربوط به اکشن‌های در جریان
    current_action: Optional[str] = None
    actor_id: Optional[int] = None
    target_id: Optional[int] = None
