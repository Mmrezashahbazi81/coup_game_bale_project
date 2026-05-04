"""
models.py
Pydantic Models for Coup Bot
برای ذخیره‌سازی در Redis و تبدیل به GameEngine
"""
from pydantic import BaseModel, Field
from typing import List, Dict, Optional
import random
from typing import ClassVar
from app.game_engine import GameState, GameMode


# ==================== Player Model ====================

class PlayerModel(BaseModel):
    user_id: int
    name: str
    coins: int = 2
    cards: List[str] = []
    dead_cards: List[str] = []
    is_alive: bool = True


# ==================== GameState Model ====================

class GameStateModel(BaseModel):
    chat_id: int
    creator_id: int
    state: str = "SET_MODE"  # تمام state های بازی
    mode: Optional[str] = None  # classic / expansion
    timeout_sec: int = 30
    
    # Players & Order
    players: Dict[str, PlayerModel] = {}  # user_id (str) -> PlayerModel
    order: List[int] = []
    turn_index: int = 0
    msg_id: Optional[int] = None
    
    # Deck
    deck: List[str] = []
    
    # Current action state
    action: Optional[str] = None
    actor_id: Optional[int] = None
    target_id: Optional[int] = None
    claimed_card: Optional[str] = None
    
    # Block state
    blocker_id: Optional[int] = None
    block_card: Optional[str] = None
    
    # Challenge tracking
    responses: List[int] = []  # user_ids who accepted
    
    # Drop resolution
    next_step: Optional[str] = None
    dropping_uid: Optional[int] = None
    
    # Exchange state
    exchange_cards: List[str] = []
    exchange_keep_count: int = 0
    
    # Inquisitor state
    inq_shown_card: Optional[str] = None
    
    # Timer tracking
    timer_task_id: Optional[str] = None  # Celery task ID
    
    # Game log
    game_log: List[str] = []
    
    # Last activity timestamp
    last_active: float = Field(default_factory=lambda: __import__('time').time())
    
    class Config:
        # اجازه دادن به فیلدهای اضافی (برای compatibility)
        extra = "ignore"
    
    # ==================== Factory Methods ====================
    
    @classmethod
    def create_new(cls, chat_id: int, creator_id: int) -> "GameStateModel":
        """ساخت بازی جدید"""
        return cls(
            chat_id=chat_id,
            creator_id=creator_id,
            state="SET_MODE",
            last_active=__import__('time').time()
        )
    
    # ==================== Conversion Methods ====================
    
    def to_engine(self):
        """
        تبدیل GameStateModel به GameEngine
        این متد game_engine رو import میکنه و state رو به engine تبدیل میکنه
        """
        from app.game_engine import GameEngine
        
        engine = GameEngine(chat_id=self.chat_id, creator_id=self.creator_id)
        
        # Restore state
        if isinstance(self.state, str):
            engine.state = GameState(self.state)
        else:
            engine.state = self.state

        if self.mode and isinstance(self.mode, str):
            engine.mode = GameMode(self.mode)
        elif self.mode and isinstance(self.mode, GameMode):
            engine.mode = self.mode
        else:
            engine.mode = None
        
        engine.timeout_sec = self.timeout_sec
        engine.last_active = self.last_active
        
        # Restore players (تبدیل PlayerModel به Player dataclass)
        from app.game_engine import Player as EnginePlayer
        for uid_str, player_model in self.players.items():
            uid = int(uid_str)
            engine.players[uid] = EnginePlayer(
                user_id=player_model.user_id,
                name=player_model.name,
                coins=player_model.coins,
                cards=player_model.cards.copy(),
                dead_cards=player_model.dead_cards.copy(),
                is_alive=player_model.is_alive
            )
        
        # Restore order & turn
        engine.order = self.order.copy()
        engine.turn_index = self.turn_index
        engine.msg_id = self.msg_id
        
        # Restore deck
        engine.deck = self.deck.copy()
        
        # Restore action state
        engine.action = self.action
        engine.actor_id = self.actor_id
        engine.target_id = self.target_id
        engine.claimed_card = self.claimed_card
        
        # Restore block state
        engine.blocker_id = self.blocker_id
        engine.block_card = self.block_card
        
        # Restore challenge tracking
        engine.responses = self.responses.copy()
        
        # Restore drop resolution
        engine.next_step = self.next_step
        engine.dropping_uid = self.dropping_uid
        
        # Restore exchange state
        engine.exchange_cards = self.exchange_cards.copy()
        engine.exchange_keep_count = self.exchange_keep_count
        
        # Restore inquisitor state
        engine.inq_shown_card = self.inq_shown_card
        
        # Restore game log
        engine.game_log = self.game_log.copy()
        
        return engine
    
    @classmethod
    def from_engine(cls, engine) -> "GameStateModel":
        """
        تبدیل GameEngine به GameStateModel
        """
        from app.game_engine import GameEngine
        
        # Convert players from dataclass to PlayerModel
        players_dict = {}
        for uid, player in engine.players.items():
            players_dict[str(uid)] = PlayerModel(
                user_id=player.user_id,
                name=player.name,
                coins=player.coins,
                cards=player.cards.copy(),
                dead_cards=player.dead_cards.copy(),
                is_alive=player.is_alive
            )
        
        return cls(
            chat_id=engine.chat_id,
            creator_id=engine.creator_id,
            state=engine.state.value if hasattr(engine.state, 'value') else engine.state,
            mode=engine.mode.value if engine.mode and hasattr(engine.mode, 'value') else engine.mode,
            timeout_sec=engine.timeout_sec,
            players=players_dict,
            order=engine.order.copy(),
            turn_index=engine.turn_index,
            msg_id=engine.msg_id,
            deck=engine.deck.copy(),
            action=engine.action,
            actor_id=engine.actor_id,
            target_id=engine.target_id,
            claimed_card=engine.claimed_card,
            blocker_id=engine.blocker_id,
            block_card=engine.block_card,
            responses=engine.responses.copy(),
            next_step=engine.next_step,
            dropping_uid=engine.dropping_uid,
            exchange_cards=engine.exchange_cards.copy(),
            exchange_keep_count=engine.exchange_keep_count,
            inq_shown_card=engine.inq_shown_card,
            game_log=engine.game_log.copy(),
            last_active=engine.last_active
        )
    
    # ==================== Utility Methods ====================
    
    def can_start(self) -> bool:
        """چک کن بازی می‌تونه شروع بشه یا نه"""
        return len(self.players) >= 2
    
    def get_current_player_id(self) -> Optional[int]:
        """آیدی بازیکن فعلی"""
        if not self.order or self.turn_index >= len(self.order):
            return None
        return self.order[self.turn_index]
    
    def get_current_player(self) -> Optional[PlayerModel]:
        """بازیکن فعلی"""
        uid = self.get_current_player_id()
        if uid is None:
            return None
        return self.players.get(str(uid))
    
    def get_alive_players(self) -> List[PlayerModel]:
        """لیست بازیکنان زنده"""
        return [p for p in self.players.values() if p.is_alive]
    
    def get_alive_other_players(self, exclude_id: int) -> List[PlayerModel]:
        """لیست بازیکنان زنده به جز exclude_id"""
        return [p for p in self.players.values() if p.is_alive and p.user_id != exclude_id]
    
    def get_player(self, user_id: int) -> Optional[PlayerModel]:
        """دریافت یه بازیکن با user_id"""
        return self.players.get(str(user_id))
    
    def is_player_alive(self, user_id: int) -> bool:
        """چک کن بازیکن زنده‌ست یا نه"""
        player = self.get_player(user_id)
        return player is not None and player.is_alive
    
    def must_coup(self) -> bool:
        """چک کن بازیکن فعلی مجبوره کودتا کنه (۱۰+ سکه)"""
        player = self.get_current_player()
        return player is not None and player.coins >= 10
    
    def check_winner(self) -> Optional[PlayerModel]:
        """چک کن برنده‌ای هست یا نه"""
        alive = self.get_alive_players()
        if len(alive) == 1:
            return alive[0]
        if len(alive) == 0:
            return None
        return None
    
    def is_expired(self, timeout_seconds: int = 3600) -> bool:
        """چک کن بازی منقضی شده (عدم فعالیت)"""
        import time
        return time.time() - self.last_active > timeout_seconds
    
    def touch(self):
        """آپدیت زمان آخرین فعالیت"""
        import time
        self.last_active = time.time()
    
    def add_log(self, entry: str):
        """اضافه کردن به لاگ بازی"""
        self.game_log.append(entry)
        if len(self.game_log) > 2:
            self.game_log = self.game_log[-2:]
    
    # ==================== Card Deck Methods ====================
    
    ROLE_COUNTS: ClassVar[dict] = {
        "classic": {
            "Duke": 3, "Assassin": 3, "Captain": 3,
            "Ambassador": 3, "Contessa": 3
        },
        "expansion": {
            "Duke": 3, "Assassin": 3, "Captain": 3,
            "Inquisitor": 3, "Contessa": 3
        }
    }
    
    def create_deck(self) -> List[str]:
        """ساخت دک بر اساس mode"""
        if not self.mode:
            self.mode = "classic"
        
        deck = []
        roles = self.ROLE_COUNTS.get(self.mode, self.ROLE_COUNTS["classic"])
        for role, count in roles.items():
            deck.extend([role] * count)
        random.shuffle(deck)
        return deck
    
    def deal_cards(self):
        """توزیع کارت بین بازیکنان"""
        self.deck = self.create_deck()
        for player in self.players.values():
            if len(self.deck) >= 2:
                player.cards = [self.deck.pop(), self.deck.pop()]
    
    def set_player_order(self):
        """ست کردن ترتیب تصادفی بازیکنان"""
        self.order = [int(uid) for uid in self.players.keys()]
        random.shuffle(self.order)
        self.turn_index = 0