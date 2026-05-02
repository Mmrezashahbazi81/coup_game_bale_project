# app/models.py
from pydantic import BaseModel, Field
from typing import List, Dict, Optional
import random

class Player(BaseModel):
    user_id: int
    name: str
    coins: int = 2
    cards: List[str] = []
    dead_cards: List[str] = []
    is_alive: bool = True

class GameState(BaseModel):
    chat_id: int
    creator_id: int
    mode: str = "classic"
    state: str = "LOBBY"
    players: Dict[str, Player] = {}
    player_order: List[int] = []
    current_turn_index: int = 0
    deck: List[str] = []
    
    current_action: Optional[str] = None
    actor_id: Optional[int] = None
    target_id: Optional[int] = None
    
    turn_timer: int = 60
    challenge_timer: int = 30
    timer_task_id: Optional[str] = None
    
    # NEW: فیلد game_log برای تاریخچه بازی
    game_log: List[str] = []
    
    def can_start(self) -> bool:
        return len(self.players) >= 2
    
    def create_deck(self) -> List[str]:
        cards = ["Duke"] * 5 + ["Assassin"] * 5 + ["Contessa"] * 5 + ["Captain"] * 5 + ["Ambassador"] * 5
        random.shuffle(cards)
        return cards
    
    def deal_cards(self):
        self.deck = self.create_deck()
        for player in self.players.values():
            player.cards = [self.deck.pop(), self.deck.pop()]
    
    def set_player_order(self):
        self.player_order = [int(uid) for uid in self.players.keys()]
        random.shuffle(self.player_order)
        self.current_turn_index = 0
    
    def get_current_player(self) -> Player:
        uid = str(self.player_order[self.current_turn_index])
        return self.players[uid]
    
    def next_turn(self):
        self.current_turn_index = (self.current_turn_index + 1) % len(self.player_order)
        attempts = 0
        while not self.get_current_player().is_alive and attempts < len(self.player_order):
            self.current_turn_index = (self.current_turn_index + 1) % len(self.player_order)
            attempts += 1
    
    def alive_count(self) -> int:
        return sum(1 for p in self.players.values() if p.is_alive)
    
    def check_winner(self) -> Optional[Player]:
        alive_players = [p for p in self.players.values() if p.is_alive]
        if len(alive_players) == 1:
            return alive_players[0]
        return None
    
    def add_coins(self, user_id: int, amount: int):
        self.players[str(user_id)].coins += amount
    
    def remove_coins(self, user_id: int, amount: int) -> bool:
        player = self.players[str(user_id)]
        if player.coins >= amount:
            player.coins -= amount
            return True
        return False