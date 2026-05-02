from pydantic import BaseModel, Field
from typing import List, Dict, Optional
import random

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

    # NEW: متد بررسی حداقل بازیکن
    def can_start(self) -> bool:
        return len(self.players) >= 3
    
    # NEW: متد ساخت دک اولیه
    def create_deck(self) -> List[str]:
        # در حالت کلاسیک: 3 تا از هر کارت (5 نوع کارت = 15 کارت)
        cards = ["Duke"] * 5 + ["Assassin"] * 5 + ["Contessa"] * 5 + ["Captain"] * 5 + ["Ambassador"] * 5
        random.shuffle(cards)
        return cards
    
    # NEW: متد توزیع کارت به بازیکنان
    def deal_cards(self):
        self.deck = self.create_deck()
        for player in self.players.values():
            # به هر بازیکن 2 کارت بده
            player.cards = [self.deck.pop(), self.deck.pop()]
    
    # NEW: متد تنظیم ترتیب بازیکنان
    def set_player_order(self):
        self.player_order = [int(uid) for uid in self.players.keys()]
        random.shuffle(self.player_order)
        self.current_turn_index = 0
        
    # NEW: گرفتن بازیکن فعلی
    def get_current_player(self) -> Player:
        uid = str(self.player_order[self.current_turn_index])
        return self.players[uid]
    
    # NEW: رفتن به نوبت بعدی
    def next_turn(self):
        self.current_turn_index = (self.current_turn_index + 1) % len(self.player_order)
        # اگه بازیکن بعدی مرده بود، برو بعدی
        attempts = 0
        while not self.get_current_player().is_alive and attempts < len(self.player_order):
            self.current_turn_index = (self.current_turn_index + 1) % len(self.player_order)
            attempts += 1
    
    # NEW: چک کردن تعداد بازیکنان زنده
    def alive_count(self) -> int:
        return sum(1 for p in self.players.values() if p.is_alive)
    
    # NEW: چک کردن برنده
    def check_winner(self) -> Optional[Player]:
        alive_players = [p for p in self.players.values() if p.is_alive]
        if len(alive_players) == 1:
            return alive_players[0]
        return None
    
    # NEW: اضافه کردن سکه به بازیکن
    def add_coins(self, user_id: int, amount: int):
        self.players[str(user_id)].coins += amount
    
    # NEW: کم کردن سکه از بازیکن
    def remove_coins(self, user_id: int, amount: int) -> bool:
        player = self.players[str(user_id)]
        if player.coins >= amount:
            player.coins -= amount
            return True
        return False        