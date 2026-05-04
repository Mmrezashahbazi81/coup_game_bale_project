#game_engine.py
import random
import time
from typing import List, Dict, Optional, Tuple, Any
from dataclasses import dataclass, field
from enum import Enum


# ==================== Enums ====================

class GameMode(str, Enum):
    CLASSIC = "classic"
    EXPANSION = "expansion"


class GameState(str, Enum):
    SET_MODE = "SET_MODE"
    SET_TIMER = "SET_TIMER"
    LOBBY = "LOBBY"
    PLAYING = "PLAYING"
    WAITING_TARGET = "WAITING_TARGET"
    CHALLENGE_ACT = "CHALLENGE_ACT"
    BLOCK_FOREIGN = "BLOCK_FOREIGN"
    BLOCK_PHASE = "BLOCK_PHASE"
    CHALLENGE_BLK = "CHALLENGE_BLK"
    WAITING_DROP = "WAITING_DROP"
    WAITING_EXCHANGE = "WAITING_EXCHANGE"
    WAITING_INQ_EXCHANGE = "WAITING_INQ_EXCHANGE"
    WAITING_INQ_SHOW = "WAITING_INQ_SHOW"
    WAITING_INQ_DECIDE = "WAITING_INQ_DECIDE"
    FINISHED = "FINISHED"


# ==================== Constants ====================

MIN_PLAYERS = 2
MAX_PLAYERS = 10
CARD_NUM = 5
INQUISITOR_NUM = 3
EXCHANGE_DRAW_COUNT_AMBASSADOR = 2
EXCHANGE_DRAW_COUNT_INQUISITOR = 1
COUP_COST = 7
ASSASSIN_COST = 3
FORCE_COUP_THRESHOLD = 10

ROLE_COUNTS = {
    GameMode.CLASSIC: {
        'دوک': CARD_NUM,
        'آدم‌کش': CARD_NUM,
        'فرمانده': CARD_NUM,
        'سفیر': CARD_NUM,
        'شاه‌دخت': CARD_NUM
    },
    GameMode.EXPANSION: {
        'دوک': CARD_NUM,
        'آدم‌کش': CARD_NUM,
        'فرمانده': CARD_NUM,
        'بازرس': INQUISITOR_NUM,
        'شاه‌دخت': CARD_NUM
    }
}


# ==================== Data Classes ====================

@dataclass
class Player:
    user_id: int
    name: str
    coins: int = 2
    cards: List[str] = field(default_factory=list)
    dead_cards: List[str] = field(default_factory=list)
    is_alive: bool = True
    
    def to_dict(self) -> Dict:
        return {
            'user_id': self.user_id,
            'name': self.name,
            'coins': self.coins,
            'cards': self.cards.copy(),
            'is_alive': self.is_alive,
            'card_count': len(self.cards)
        }
    
    def remove_card(self, card: str) -> bool:
        """Remove a specific card. Returns True if successful."""
        if card in self.cards:
            self.cards.remove(card)
            self.dead_cards.append(card)
            if len(self.cards) == 0:
                self.is_alive = False
            return True
        return False


@dataclass
class ChallengeResult:
    success: bool  # True = challenge successful (target was lying)
    challenger_id: int
    target_id: int
    revealed_card: Optional[str] = None
    challenger_lost: bool = False  # True = challenger loses card
    game_over: bool = False
    winner: Optional[Player] = None
    message: str = ""


# ==================== Game Engine ====================

class GameEngine:
    """
    موتور اصلی بازی کودتا
    تمام منطق بازی در این کلاس مدیریت می‌شود.
    هیچ وابستگی به FastAPI یا Telebot ندارد.
    """
    
    def __init__(self, chat_id: int, creator_id: int):
        self.chat_id = chat_id
        self.creator_id = creator_id
        self.state = GameState.SET_MODE
        self.mode: Optional[GameMode] = None
        self.timeout_sec: int = 30
        self.last_active: float = time.time()
        self.timer_task_id: Optional[str] = None
        
        # Players
        self.players: Dict[int, Player] = {}  # user_id -> Player
        self.order: List[int] = []  # list of user_ids
        self.turn_index: int = 0
        self.msg_id: Optional[int] = None
        
        # Deck
        self.deck: List[str] = []
        
        # Current action state
        self.action: Optional[str] = None
        self.actor_id: Optional[int] = None
        self.target_id: Optional[int] = None
        self.claimed_card: Optional[str] = None
        
        # Block state
        self.blocker_id: Optional[int] = None
        self.block_card: Optional[str] = None
        
        # Challenge tracking
        self.responses: List[int] = []  # list of user_ids who accepted
        
        # Drop resolution
        self.next_step: Optional[str] = None  # 'next_turn', 'execute_action', 'apply_attack'
        self.dropping_uid: Optional[int] = None
        
        # Exchange state
        self.exchange_cards: List[str] = []
        self.exchange_keep_count: int = 0
        
        # Inquisitor state
        self.inq_shown_card: Optional[str] = None
        
        # Game log
        self.game_log: List[str] = []
    
    # ==================== Setup Methods ====================
    
    def set_mode(self, mode: str) -> bool:
        """Set game mode. Returns True if valid."""
        if self.state != GameState.SET_MODE:
            return False
        if mode not in [GameMode.CLASSIC.value, GameMode.EXPANSION.value]:
            return False
        
        self.mode = GameMode(mode)
        self.deck = self._generate_deck()
        self.state = GameState.SET_TIMER
        self._touch()
        return True
    
    def set_timer(self, seconds: int) -> bool:
        """Set turn timeout. Returns True if valid."""
        if self.state != GameState.SET_TIMER:
            return False
        if seconds < 0:
            return False
        
        self.timeout_sec = seconds
        self.state = GameState.LOBBY
        self._touch()
        return True
    
    def can_join(self, user_id: int) -> Tuple[bool, str]:
        """Check if a player can join. Returns (can_join, message)."""
        if self.state != GameState.LOBBY:
            return False, "بازی در حال عضوگیری نیست!"
        if user_id in self.players:
            return False, "شما قبلاً وارد شده‌اید!"
        if len(self.players) >= MAX_PLAYERS:
            return False, f"ظرفیت تکمیل است ({MAX_PLAYERS} نفر)!"
        return True, ""
    
    def join(self, user_id: int, name: str) -> bool:
        """Add player to game. Returns True if successful."""
        can_join, _ = self.can_join(user_id)
        if not can_join:
            return False
        
        self.players[user_id] = Player(user_id=user_id, name=name)
        self.order.append(user_id)
        self._touch()
        return True
    
    def can_start(self) -> Tuple[bool, str]:
        """Check if game can start. Returns (can_start, message)."""
        if self.state != GameState.LOBBY:
            return False, "بازی در حالت عضوگیری نیست!"
        if len(self.players) < MIN_PLAYERS:
            return False, f"حداقل {MIN_PLAYERS} بازیکن نیاز است!"
        return True, ""
    
    def start_game(self) -> Tuple[bool, str]:
        """Start the game. Returns (success, message)."""
        can_start, msg = self.can_start()
        if not can_start:
            return False, msg
        
        random.shuffle(self.deck)
        random.shuffle(self.order)
        
        # Deal cards
        for uid in self.order:
            if len(self.deck) >= 2:
                self.players[uid].cards = [self.deck.pop(), self.deck.pop()]
        
        self.state = GameState.PLAYING
        self.turn_index = 0
        self.game_log.append("🎮 بازی شروع شد!")
        self._touch()
        return True, "بازی شروع شد!"
    
    # ==================== Turn Management ====================
    
    def get_current_player(self) -> Optional[Player]:
        """Get the player whose turn it is."""
        if not self.order or self.turn_index >= len(self.order):
            return None
        uid = self.order[self.turn_index]
        return self.players.get(uid)
    
    def get_current_player_id(self) -> Optional[int]:
        """Get the user_id of the current player."""
        player = self.get_current_player()
        return player.user_id if player else None
    
    def is_player_turn(self, user_id: int) -> bool:
        """Check if it's this player's turn."""
        return self.state == GameState.PLAYING and self.get_current_player_id() == user_id
    
    def must_coup(self) -> bool:
        """Check if current player must coup (10+ coins)."""
        player = self.get_current_player()
        return player is not None and player.coins >= FORCE_COUP_THRESHOLD
    
    def next_turn(self) -> Optional[Player]:
        """
        Advance to next alive player's turn.
        Returns the winner if game is over, None otherwise.
        """
        # Reset action state
        self._reset_action_state()
        self.state = GameState.PLAYING
        
        # Find next alive player
        attempts = 0
        while attempts < len(self.order):
            self.turn_index = (self.turn_index + 1) % len(self.order)
            player = self.players[self.order[self.turn_index]]
            if player.is_alive:
                break
            attempts += 1
        
        # Check winner
        return self._check_winner()
    
    def _reset_action_state(self):
        """Reset all temporary action state."""
        self.action = None
        self.actor_id = None
        self.target_id = None
        self.claimed_card = None
        self.blocker_id = None
        self.block_card = None
        self.inq_shown_card = None
        self.responses = []
        self.next_step = None
        self.dropping_uid = None
        self.exchange_cards = []
        self.exchange_keep_count = 0
    
    # ==================== Actions ====================
    
    def income(self, user_id: int) -> Tuple[bool, str]:
        """Income: +1 coin. No challenge possible."""
        if not self.is_player_turn(user_id):
            return False, "نوبت شما نیست!"
        
        self.players[user_id].coins += 1
        self.actor_id = user_id
        self.game_log.append(f"💰 {self.players[user_id].name} +۱ سکه")
        self._trim_log()
        
        winner = self.next_turn()
        if winner:
            return True, f"🏆 {winner.name} برنده شد!"
        
        self._touch()
        return True, "درآمد دریافت شد"
    
    def foreign_aid(self, user_id: int) -> Tuple[bool, str]:
        """Foreign Aid: +2 coins. Can be blocked by Duke."""
        if not self.is_player_turn(user_id):
            return False, "نوبت شما نیست!"
        
        self.action = 'کمک خارجی'
        self.actor_id = user_id
        self.state = GameState.BLOCK_FOREIGN
        self.responses = []
        self._touch()
        return True, "منتظر واکنش سایر بازیکنان..."
    
    def coup(self, user_id: int, target_id: int) -> Tuple[bool, str]:
        """Coup: Pay 7 coins, destroy target's card. Cannot be blocked."""
        if not self.is_player_turn(user_id):
            return False, "نوبت شما نیست!"
        
        player = self.players[user_id]
        if player.coins < COUP_COST:
            return False, f"سکه کافی نیست! نیاز: {COUP_COST}، دارید: {player.coins}"
        if target_id not in self.players or not self.players[target_id].is_alive:
            return False, "هدف نامعتبر است!"
        if target_id == user_id:
            return False, "نمی‌توانید خودتان را هدف قرار دهید!"
        
        player.coins -= COUP_COST
        self.actor_id = user_id
        self.target_id = target_id
        self.action = 'کودتا'
        self.next_step = 'next_turn'
        self.game_log.append(f"💀 {player.name} کودتا کرد علیه {self.players[target_id].name}!")
        self._trim_log()
        self._touch()
        
        # Target must drop a card
        return True, "drop_required"
    
    def tax(self, user_id: int) -> Tuple[bool, str]:
        """Tax (Duke): +3 coins. Can be challenged."""
        if not self.is_player_turn(user_id):
            return False, "نوبت شما نیست!"
        
        self.action = 'برداشت 3 سکه'
        self.claimed_card = 'دوک'
        self.actor_id = user_id
        self.state = GameState.CHALLENGE_ACT
        self.responses = []
        self._touch()
        return True, "ادعای دوک کردید"
    
    def assassinate(self, user_id: int, target_id: int) -> Tuple[bool, str]:
        """Assassinate: Pay 3 coins, destroy target's card. Can be challenged. Can be blocked by Contessa."""
        if not self.is_player_turn(user_id):
            return False, "نوبت شما نیست!"
        
        player = self.players[user_id]
        if player.coins < ASSASSIN_COST:
            return False, f"سکه کافی نیست! نیاز: {ASSASSIN_COST}، دارید: {player.coins}"
        if target_id not in self.players or not self.players[target_id].is_alive:
            return False, "هدف نامعتبر است!"
        if target_id == user_id:
            return False, "نمی‌توانید خودتان را هدف قرار دهید!"
        
        self.action = 'سوقصد'
        self.claimed_card = 'آدم‌کش'
        self.actor_id = user_id
        self.state = GameState.WAITING_TARGET
        self._touch()
        return True, "هدف را انتخاب کنید"
    
    def steal(self, user_id: int, target_id: int) -> Tuple[bool, str]:
        """Steal (Captain): Steal up to 2 coins. Can be challenged. Can be blocked."""
        if not self.is_player_turn(user_id):
            return False, "نوبت شما نیست!"
        if target_id not in self.players or not self.players[target_id].is_alive:
            return False, "هدف نامعتبر است!"
        if target_id == user_id:
            return False, "نمی‌توانید از خودتان بدزدید!"
        if self.players[target_id].coins <= 0:
            return False, "هدف سکه ندارد!"
        
        self.action = 'باج‌گیری'
        self.claimed_card = 'فرمانده'
        self.actor_id = user_id
        self.state = GameState.WAITING_TARGET
        self._touch()
        return True, "هدف را انتخاب کنید"
    
    def exchange(self, user_id: int) -> Tuple[bool, str]:
        """Exchange (Ambassador): Draw cards, pick which to keep. Can be challenged."""
        if not self.is_player_turn(user_id):
            return False, "نوبت شما نیست!"
        
        self.action = 'تبادل'
        self.claimed_card = 'سفیر'
        self.actor_id = user_id
        self.state = GameState.CHALLENGE_ACT
        self.responses = []
        self._touch()
        return True, "ادعای سفیر کردید"
    
    def inq_exchange(self, user_id: int) -> Tuple[bool, str]:
        """Inquisitor Exchange: Draw 1 card, pick which to keep. Can be challenged."""
        if not self.is_player_turn(user_id):
            return False, "نوبت شما نیست!"
        if self.mode != GameMode.EXPANSION:
            return False, "این قابلیت فقط در حالت الحاقی است!"
        
        self.action = 'تبادل بازرس'
        self.claimed_card = 'بازرس'
        self.actor_id = user_id
        self.state = GameState.CHALLENGE_ACT
        self.responses = []
        self._touch()
        return True, "ادعای بازرس کردید"
    
    def inq_examine(self, user_id: int, target_id: int) -> Tuple[bool, str]:
        """Inquisitor Examine: Look at target's card, decide to keep or force exchange."""
        if not self.is_player_turn(user_id):
            return False, "نوبت شما نیست!"
        if self.mode != GameMode.EXPANSION:
            return False, "این قابلیت فقط در حالت الحاقی است!"
        if target_id not in self.players or not self.players[target_id].is_alive:
            return False, "هدف نامعتبر است!"
        if target_id == user_id:
            return False, "نمی‌توانید خودتان را بازرسی کنید!"
        
        self.action = 'بازرسی'
        self.claimed_card = 'بازرس'
        self.actor_id = user_id
        self.target_id = target_id
        self.state = GameState.CHALLENGE_ACT
        self.responses = []
        self._touch()
        return True, "ادعای بازرس کردید"
    
    # ==================== Target Selection ====================
    
    def select_target(self, user_id: int, target_id: int) -> Tuple[bool, str]:
        """Select target for action requiring one. Sets appropriate state."""
        if self.state != GameState.WAITING_TARGET:
            return False, "بازی در حالت انتخاب هدف نیست!"
        if user_id != self.actor_id:
            return False, "شما مجاز به انتخاب هدف نیستید!"
        
        self.target_id = target_id
        
        if self.action == 'کودتا':
            self.next_step = 'next_turn'
            self._touch()
            return True, "drop_required"
        elif self.action in ['سوقصد', 'باج‌گیری']:
            self.state = GameState.CHALLENGE_ACT
            self.responses = []
            self._touch()
            return True, "هدف انتخاب شد"
        elif self.action == 'بازرسی':
            self.state = GameState.WAITING_INQ_SHOW
            self._touch()
            return True, "منتظر نشان دادن کارت هدف..."
        
        return False, "اکشن نامعتبر"
    
    # ==================== Challenge System ====================
    
    def can_challenge(self, user_id: int) -> Tuple[bool, str]:
        """Check if a player can challenge. Returns (can_challenge, message)."""
        if user_id not in self.players:
            return False, "شما در بازی نیستید!"
        if not self.players[user_id].is_alive:
            return False, "شما حذف شده‌اید!"
        if self.state == GameState.CHALLENGE_ACT and user_id == self.actor_id:
            return False, "نمی‌توانید خودتان را چالش کنید!"
        if self.state == GameState.CHALLENGE_BLK and user_id == self.blocker_id:
            return False, "نمی‌توانید دفاع خودتان را چالش کنید!"
        if self.state == GameState.BLOCK_FOREIGN and user_id == self.actor_id:
            return False, "نمی‌توانید خودتان را بلاک کنید!"
        return True, ""
    
    def accept(self, user_id: int) -> Tuple[bool, str]:
        """Player accepts the current action/block. Returns (success, message)."""
        valid_states = [GameState.CHALLENGE_ACT, GameState.BLOCK_FOREIGN, GameState.CHALLENGE_BLK]
        if self.state not in valid_states:
            return False, "در حال حاضر چیزی برای پذیرش نیست!"
        
        can_challenge, msg = self.can_challenge(user_id)
        if not can_challenge:
            return False, msg
        
        if user_id not in self.responses:
            self.responses.append(user_id)
        
        self._touch()
        
        # Check if all eligible players accepted
        if self._all_accepted():
            return True, "all_accepted"
        
        return True, "پذیرفته شد"
    
    def _all_accepted(self) -> bool:
        """Check if all eligible players have accepted."""
        if self.state == GameState.CHALLENGE_ACT:
            eligible = [uid for uid in self.players if uid != self.actor_id and self.players[uid].is_alive]
        elif self.state == GameState.BLOCK_FOREIGN:
            eligible = [uid for uid in self.players if uid != self.actor_id and self.players[uid].is_alive]
        elif self.state == GameState.CHALLENGE_BLK:
            eligible = [uid for uid in self.players if uid != self.blocker_id and self.players[uid].is_alive]
        else:
            return False
        
        # All eligible players must have responded
        for uid in eligible:
            if uid not in self.responses:
                return False
        return True
    
    def challenge(self, user_id: int) -> ChallengeResult:
        """
        Player challenges the current claim.
        Returns ChallengeResult with all info needed by the API layer.
        """
        result = ChallengeResult(
            success=False,
            challenger_id=user_id,
            target_id=(self.actor_id if self.state in [GameState.CHALLENGE_ACT, GameState.BLOCK_FOREIGN] else self.blocker_id) or 0,
            message=""
        )
        
        # Validate
        can_challenge, msg = self.can_challenge(user_id)
        if not can_challenge:
            result.message = msg
            return result
        
        # Determine who is being challenged and what card
        if self.state == GameState.CHALLENGE_ACT:
            target_id = self.actor_id
            claimed_card = self.claimed_card
        elif self.state == GameState.CHALLENGE_BLK:
            target_id = self.blocker_id
            claimed_card = self.block_card
        elif self.state == GameState.BLOCK_FOREIGN:
            target_id = user_id  # The blocker claimed Duke
            claimed_card = 'دوک'
            self.blocker_id = user_id
            self.block_card = 'دوک'
        else:
            result.message = "در حال حاضر چالش ممکن نیست!"
            return result
        
        assert target_id is not None
        target = self.players[target_id]
        challenger = self.players[user_id]
        
        # Check if target has the claimed card
        if claimed_card in target.cards:
            # Challenge FAILED - challenger loses a card
            result.success = False
            result.challenger_lost = True
            result.revealed_card = claimed_card
            
            self.next_step = 'next_turn' if self.state in [GameState.CHALLENGE_BLK, GameState.BLOCK_FOREIGN] else 'execute_action'
            self.dropping_uid = user_id
            self.state = GameState.WAITING_DROP
            
            # Swap the revealed card
            self._swap_card(target_id, claimed_card)
            
            result.message = f"🚨 مچ‌گیری ناموفق! {target.name} واقعاً {claimed_card} را داشت!"
            result.target_id = user_id  # Challenger becomes target for drop
        else:
            # Challenge SUCCESS - target was lying
            result.success = True
            result.revealed_card = claimed_card
            
            if self.state == GameState.CHALLENGE_ACT:
                self.next_step = 'next_turn'
            elif self.state == GameState.CHALLENGE_BLK:
                self.next_step = 'apply_attack'
            else:  # BLOCK_FOREIGN
                self.next_step = 'next_turn'
            
            self.dropping_uid = target_id
            self.state = GameState.WAITING_DROP
            
            result.message = f"🚨 مچ‌گیری موفق! {target.name} دروغ می‌گفت!"
            result.target_id = target_id  # Target loses card
        
        self._touch()
        return result
    
    def execute_action_after_challenge(self) -> Tuple[bool, str]:
        """Execute the pending action after all challenges are resolved."""
        if self.action == 'برداشت 3 سکه':
            if self.actor_id is not None:
                self.players[self.actor_id].coins += 3
            self.next_turn()
            return True, "۳ سکه برداشت شد"
        
        elif self.action == 'تبادل':
            if self.actor_id is not None:
                self._start_exchange(self.actor_id, EXCHANGE_DRAW_COUNT_AMBASSADOR, GameState.WAITING_EXCHANGE)
            return True, "exchange_started"
        
        elif self.action == 'تبادل بازرس':
            if self.actor_id is not None:
                self._start_exchange(self.actor_id, EXCHANGE_DRAW_COUNT_INQUISITOR, GameState.WAITING_INQ_EXCHANGE)
            return True, "inq_exchange_started"
        
        elif self.action == 'بازرسی':
            self.state = GameState.WAITING_INQ_SHOW
            return True, "inq_show_started"
        
        elif self.action in ['سوقصد', 'باج‌گیری']:
            self.state = GameState.BLOCK_PHASE
            return True, "block_phase"
        
        return False, "اکشنی برای اجرا نیست"
    
    # ==================== Block System ====================
    
    def block_foreign_aid(self, user_id: int) -> Tuple[bool, str]:
        """Block Foreign Aid with Duke."""
        if self.state != GameState.BLOCK_FOREIGN:
            return False, "در حال حاضر بلاک ممکن نیست!"
        if user_id == self.actor_id:
            return False, "نمی‌توانید خودتان را بلاک کنید!"
        
        self.blocker_id = user_id
        self.block_card = 'دوک'
        self.state = GameState.CHALLENGE_BLK
        self.responses = []
        self._touch()
        return True, "بلاک شد"
    
    def block_assassinate(self, user_id: int) -> Tuple[bool, str]:
        """Block Assassinate with Contessa. Only target can block."""
        if self.state != GameState.BLOCK_PHASE:
            return False, "در حال حاضر بلاک ممکن نیست!"
        if self.action != 'سوقصد':
            return False, "اکشن فعلی سوقصد نیست!"
        if user_id != self.target_id:
            return False, "فقط هدف می‌تواند دفاع کند!"
        
        self.blocker_id = user_id
        self.block_card = 'شاه‌دخت'
        self.state = GameState.CHALLENGE_BLK
        self.responses = []
        self._touch()
        return True, "دفاع با شاه‌دخت"
    
    def block_steal(self, user_id: int) -> Tuple[bool, str]:
        """Block Steal with Captain or Ambassador/Inquisitor. Only target can block."""
        if self.state != GameState.BLOCK_PHASE:
            return False, "در حال حاضر بلاک ممکن نیست!"
        if self.action != 'باج‌گیری':
            return False, "اکشن فعلی باج‌گیری نیست!"
        if user_id != self.target_id:
            return False, "فقط هدف می‌تواند دفاع کند!"
        
        self.blocker_id = user_id
        # Player can claim Captain, Ambassador, or Inquisitor
        self.block_card = 'فرمانده'  # Default, can be overridden
        self.state = GameState.CHALLENGE_BLK
        self.responses = []
        self._touch()
        return True, "دفاع در برابر باج‌گیری"
    
    def set_block_card(self, card: str) -> bool:
        """Set the specific block card (for steal defense choices)."""
        if self.state == GameState.BLOCK_PHASE and self.action == 'باج‌گیری':
            valid_cards = ['فرمانده', 'سفیر'] if self.mode == GameMode.CLASSIC else ['فرمانده', 'بازرس']
            if card in valid_cards:
                self.block_card = card
                return True
        return False
    
    def surrender(self, user_id: int) -> Tuple[bool, str]:
        """Target surrenders (doesn't block)."""
        if self.state != GameState.BLOCK_PHASE:
            return False, "در حال حاضر تسلیم ممکن نیست!"
        if user_id != self.target_id:
            return False, "فقط هدف می‌تواند تسلیم شود!"
        
        return self._apply_attack()
    
    def _apply_attack(self) -> Tuple[bool, str]:
        """Apply the attack effect."""
        if self.action == 'سوقصد':
            if self.actor_id is not None:
                self.players[self.actor_id].coins -= ASSASSIN_COST
            self.next_step = 'next_turn'
            self.dropping_uid = self.target_id
            self.state = GameState.WAITING_DROP
            return True, "drop_required"
        
        elif self.action == 'باج‌گیری':
            target = self.players[self.target_id] if self.target_id is not None else None
            if target is None:
                return False, "هدف نامعتبر!"
            steal_amount = min(2, target.coins)
            target.coins -= steal_amount
            if self.actor_id is not None:
                self.players[self.actor_id].coins += steal_amount
            self.game_log.append(f"🏴‍☠️ {steal_amount} سکه دزدیده شد")
            self._trim_log()
            self.next_turn()
            return True, f"{steal_amount} سکه دزدیده شد"
        
        return False, "اکشنی برای اجرا نیست"
    
    # ==================== Drop System ====================
    
    def get_drop_cards(self, user_id: int) -> Optional[List[str]]:
        """Get the cards available for dropping."""
        if self.dropping_uid != user_id:
            return None
        player = self.players.get(user_id)
        if not player or not player.is_alive:
            return None
        return player.cards.copy()
    
    def drop_card(self, user_id: int, card_index: int) -> Tuple[bool, str]:
        """Drop a specific card by index. Returns (success, message)."""
        if self.state != GameState.WAITING_DROP:
            return False, "بازی در حالت سوزاندن کارت نیست!"
        if self.dropping_uid != user_id:
            return False, "نوبت شما نیست!"
        
        player = self.players[user_id]
        if card_index < 0 or card_index >= len(player.cards):
            return False, "ایندکس نامعتبر!"
        
        card = player.cards.pop(card_index)
        player.dead_cards.append(card)
        
        if len(player.cards) == 0:
            player.is_alive = False
        
        self._touch()
        return self._resolve_after_drop()
    
    def random_drop(self, user_id: int) -> Tuple[bool, str]:
        """Randomly drop a card (timeout or blocked user)."""
        if self.state != GameState.WAITING_DROP:
            return False, "بازی در حالت سوزاندن کارت نیست!"
        if self.dropping_uid != user_id:
            return False, "نوبت شما نیست!"
        
        player = self.players[user_id]
        if not player.cards:
            player.is_alive = False
            return self._resolve_after_drop()
        
        card = random.choice(player.cards)
        player.cards.remove(card)
        player.dead_cards.append(card)
        
        if len(player.cards) == 0:
            player.is_alive = False
        
        self._touch()
        return self._resolve_after_drop()
    
    def _resolve_after_drop(self) -> Tuple[bool, str]:
        """Resolve the game state after a card is dropped."""
        # Check if dropped player is now dead
        dropper = self.players[self.dropping_uid] if self.dropping_uid is not None else None
        
        winner = self._check_winner()
        if winner:
            return True, f"🏆 {winner.name} برنده شد!"
        
        # Follow next_step
        if self.next_step == 'next_turn':
            self.next_turn()
            return True, "next_turn"
        elif self.next_step == 'execute_action':
            return self.execute_action_after_challenge()
        elif self.next_step == 'apply_attack':
            return self._apply_attack()
        
        return True, "done"
    
    # ==================== Exchange System ====================
    
    def _start_exchange(self, user_id: int, draw_count: int, state: GameState):
        """Internal method to start exchange."""
        self.exchange_keep_count = len(self.players[user_id].cards)
        
        drawn = [self.deck.pop() for _ in range(draw_count) if self.deck]
        self.exchange_cards = self.players[user_id].cards + drawn
        self.players[user_id].cards = []
        self.state = state
        self._touch()
    
    def get_exchange_cards(self, user_id: int) -> Optional[List[str]]:
        """Get current exchange cards for a player."""
        if self.actor_id != user_id:
            return None
        return self.exchange_cards.copy()
    
    def keep_exchange_card(self, user_id: int, card: str) -> Tuple[bool, str]:
        """Keep a card during exchange."""
        if self.actor_id != user_id:
            return False, "شما در حال تبادل نیستید!"
        if card not in self.exchange_cards:
            return False, "کارت نامعتبر!"
        
        self.players[user_id].cards.append(card)
        self.exchange_cards.remove(card)
        self._touch()
        
        # Check if exchange is complete
        if len(self.players[user_id].cards) == self.exchange_keep_count:
            return self._finalize_exchange(user_id)
        
        return True, f"کارت {card} نگه داشته شد. {self.exchange_keep_count - len(self.players[user_id].cards)} کارت دیگر لازم است"
    
    def return_exchange_card(self, user_id: int, card: str) -> Tuple[bool, str]:
        """Return a card to the deck during exchange."""
        if self.actor_id != user_id:
            return False, "شما در حال تبادل نیستید!"
        if card not in self.exchange_cards:
            return False, "کارت نامعتبر!"
        
        self.deck.append(card)
        self.exchange_cards.remove(card)
        random.shuffle(self.deck)
        self._touch()
        
        # Check if exchange is complete
        if len(self.players[user_id].cards) == self.exchange_keep_count:
            return self._finalize_exchange(user_id)
        
        return True, f"کارت {card} برگشت داده شد"
    
    def _finalize_exchange(self, user_id: int) -> Tuple[bool, str]:
        """Complete the exchange."""
        # Return remaining exchange cards to deck
        for card in self.exchange_cards:
            self.deck.append(card)
        random.shuffle(self.deck)
        
        self.exchange_cards = []
        self.game_log.append(f"🔄 {self.players[user_id].name} کارت‌ها را تعویض کرد")
        self._trim_log()
        
        self.next_turn()
        self._touch()
        return True, "تبادل کامل شد"
    
    def random_exchange(self, user_id: int) -> Tuple[bool, str]:
        """Perform random exchange (timeout fallback)."""
        if self.actor_id != user_id:
            return False, "شما در حال تبادل نیستید!"
        
        all_cards = self.exchange_cards + self.players[user_id].cards
        random.shuffle(all_cards)
        
        self.players[user_id].cards = all_cards[:self.exchange_keep_count]
        self.deck.extend(all_cards[self.exchange_keep_count:])
        random.shuffle(self.deck)
        
        self.exchange_cards = []
        self.game_log.append(f"🔄 {self.players[user_id].name} کارت‌ها را تعویض کرد (تصادفی)")
        self._trim_log()
        
        self.next_turn()
        self._touch()
        return True, "تبادل تصادفی انجام شد"
    
    # ==================== Inquisitor System ====================
    
    def get_inq_show_cards(self, user_id: int) -> Optional[List[str]]:
        """Get cards that target can show to inquisitor."""
        if self.target_id != user_id:
            return None
        player = self.players.get(user_id)
        if not player or not player.is_alive:
            return None
        return player.cards.copy()
    
    def show_card_to_inq(self, user_id: int, card_index: int) -> Tuple[bool, str]:
        """Target shows a card to the inquisitor."""
        if self.state != GameState.WAITING_INQ_SHOW:
            return False, "بازی در حالت نمایش کارت نیست!"
        if self.target_id != user_id:
            return False, "شما هدف بازرسی نیستید!"
        
        player = self.players[user_id]
        if card_index < 0 or card_index >= len(player.cards):
            return False, "ایندکس نامعتبر!"
        
        self.inq_shown_card = player.cards[card_index]
        self.state = GameState.WAITING_INQ_DECIDE
        self._touch()
        return True, "کارت نشان داده شد"
    
    def random_show_to_inq(self, user_id: int) -> Tuple[bool, str]:
        """Random card shown (timeout fallback)."""
        if self.state != GameState.WAITING_INQ_SHOW:
            return False, "بازی در حالت نمایش کارت نیست!"
        if self.target_id != user_id:
            return False, "شما هدف بازرسی نیستید!"
        
        player = self.players[user_id]
        if player.cards:
            self.inq_shown_card = random.choice(player.cards)
        
        self.state = GameState.WAITING_INQ_DECIDE
        self._touch()
        return True, "کارت تصادفی نشان داده شد"
    
    def inq_decision(self, user_id: int, force_exchange: bool) -> Tuple[bool, str]:
        """
        Inquisitor decides: keep or force exchange.
        user_id must be the actor (inquisitor).
        """
        if self.state != GameState.WAITING_INQ_DECIDE:
            return False, "بازی در حالت تصمیم‌گیری بازرس نیست!"
        if self.actor_id != user_id:
            return False, "شما بازرس نیستید!"
        
        if force_exchange and self.inq_shown_card:
            target = self.players[self.target_id] if self.target_id is not None else None
            if target is None:
                return False, "هدف نامعتبر!"
            if self.inq_shown_card in target.cards:
                target.cards.remove(self.inq_shown_card)
                self.deck.append(self.inq_shown_card)
                random.shuffle(self.deck)
                if self.deck:
                    new_card = self.deck.pop()
                    target.cards.append(new_card)
            
            self.game_log.append(f"👁 بازرس {target.name} را مجبور به تعویض کارت کرد")
        else:
            if self.target_id is not None:
                self.game_log.append(f"👁 بازرس اجازه داد {self.players[self.target_id].name} کارتش را نگه دارد")
        
        self._trim_log()
        self.inq_shown_card = None
        self.next_turn()
        self._touch()
        return True, "تصمیم بازرس ثبت شد"
    
    # ==================== Timeout Handling ====================
    
    def handle_timeout(self, expected_state: GameState, expected_turn_index: int) -> str:
        """
        Handle timeout for current state.
        Returns: 'auto_income', 'skip_turn', 'auto_accept', 'random_drop', 
                 'random_exchange', 'random_show', 'keep_card', 'no_action'
        """
        if self.state != expected_state:
            return 'no_action'
        
        state = self.state
        
        if state == GameState.PLAYING:
            return 'auto_income'
        
        elif state == GameState.WAITING_TARGET:
            return 'skip_turn'
        
        elif state in [GameState.CHALLENGE_ACT, GameState.BLOCK_FOREIGN, GameState.CHALLENGE_BLK]:
            return 'auto_accept'
        
        elif state == GameState.BLOCK_PHASE:
            return 'auto_accept'  # Attack goes through
        
        elif state == GameState.WAITING_DROP:
            return 'random_drop'
        
        elif state in [GameState.WAITING_EXCHANGE, GameState.WAITING_INQ_EXCHANGE]:
            return 'random_exchange'
        
        elif state == GameState.WAITING_INQ_SHOW:
            return 'random_show'
        
        elif state == GameState.WAITING_INQ_DECIDE:
            return 'keep_card'
        
        return 'no_action'
    
    def execute_auto_income(self, user_id: int) -> Tuple[bool, str]:
        """Execute auto income for timeout."""
        if not self.is_player_turn(user_id):
            return False, "نوبت شما نیست!"
        
        self.players[user_id].coins += 1
        self.game_log.append(f"⏳ {self.players[user_id].name} حرکت نکرد، +۱ سکه خودکار")
        self._trim_log()
        
        self.next_turn()
        self._touch()
        return True, "درآمد خودکار"
    
    def execute_auto_accept(self) -> Tuple[bool, str]:
        """Execute auto accept for challenge/block timeout."""
        if self.state == GameState.CHALLENGE_ACT:
            return self.execute_action_after_challenge()
        elif self.state == GameState.BLOCK_FOREIGN:
            if self.actor_id is not None:
                self.players[self.actor_id].coins += 2
            self.next_turn()
            return True, "کمک خارجی پذیرفته شد"
        elif self.state == GameState.CHALLENGE_BLK:
            self.next_turn()
            return True, "دفاع پذیرفته شد"
        elif self.state == GameState.BLOCK_PHASE:
            return self._apply_attack()
        return False, "اکشنی برای پذیرش نیست"
    
    # ==================== Utility Methods ====================
    
    def _generate_deck(self) -> List[str]:
        """Generate deck based on game mode."""
        deck = []
        if self.mode is None:
            return []
        for role, count in ROLE_COUNTS[self.mode].items():
            deck.extend([role] * count)
        return deck
    
    def _swap_card(self, user_id: int, revealed_card: str):
        """Swap a revealed card with a new one from the deck."""
        player = self.players[user_id]
        if revealed_card in player.cards:
            player.cards.remove(revealed_card)
            self.deck.append(revealed_card)
            random.shuffle(self.deck)
            if self.deck:
                new_card = self.deck.pop()
                player.cards.append(new_card)
    
    def _check_winner(self) -> Optional[Player]:
        """Check if there's a winner. Returns winner or None."""
        alive = [p for p in self.players.values() if p.is_alive]
        if len(alive) <= 1:
            self.state = GameState.FINISHED
            if alive:
                return alive[0]
        return None
    
    def _touch(self):
        """Update last active timestamp."""
        self.last_active = time.time()
    
    def _trim_log(self):
        """Keep only last 2 log entries."""
        if len(self.game_log) > 2:
            self.game_log = self.game_log[-2:]
    
    def is_expired(self, timeout_seconds: int = 3600) -> bool:
        """Check if game has been inactive too long."""
        return time.time() - self.last_active > timeout_seconds
    
    def get_player_info(self, user_id: int) -> Optional[Dict]:
        """Get player info (safe for sending to players)."""
        player = self.players.get(user_id)
        if not player:
            return None
        return player.to_dict()
    
    def _get_actor(self) -> Player:
        """Get actor player. Raises if None."""
        assert self.actor_id is not None, "actor_id is None!"
        return self.players[self.actor_id]

    def _get_target(self) -> Player:
        """Get target player. Raises if None."""
        assert self.target_id is not None, "target_id is None!"
        return self.players[self.target_id]

    def _get_dropper(self) -> Player:
        """Get dropping player. Raises if None."""
        assert self.dropping_uid is not None, "dropping_uid is None!"
        return self.players[self.dropping_uid]

    def _get_blocker(self) -> Player:
        """Get blocker player. Raises if None."""
        assert self.blocker_id is not None, "blocker_id is None!"
        return self.players[self.blocker_id]
    
    def _to_model(self):
        """Convert engine to GameStateModel (for saving to Redis)"""
        from app.models import GameStateModel
        return GameStateModel.from_engine(self)    
    
    def get_public_state(self) -> Dict:
        """Get public game state for dashboard."""
        return {
            'state': self.state.value,
            'mode': self.mode.value if self.mode else 'unknown',
            'timeout_sec': self.timeout_sec,
            'players': [
                {
                    'user_id': uid,
                    'name': self.players[uid].name,
                    'coins': self.players[uid].coins,
                    'card_count': len(self.players[uid].cards),
                    'is_alive': self.players[uid].is_alive,
                    'is_current': uid == self.get_current_player_id()
                }
                for uid in self.order
            ],
            'action': self.action,
            'actor_id': self.actor_id,
            'target_id': self.target_id,
            'game_log': self.game_log.copy()
        }