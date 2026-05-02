# app/worker.py
from celery import Celery
import time
from app.database import get_game, save_game, delete_game
from app.models import GameState

celery_app = Celery(
    "coup_worker",
    broker="redis://127.0.0.1:6379/0",
    backend="redis://127.0.0.1:6379/0"
)

celery_app.conf.broker_connection_retry_on_startup = True

# NEW: تایمر نوبت - اگه بازیکن حرکت نکرد، خودکار درآمد بگیره
@celery_app.task(name="turn_timer")
def turn_timer(chat_id: int, user_id: int):
    """
    تایمر نوبت بازیکن
    اگه در زمان مقرر حرکتی نکرد، خودکار ۱ سکه درآمد میگیره
    """
    game = get_game(chat_id)
    if not game or game.state != "PLAYING":
        return f"Game not active or state changed"
    
    # چک کن هنوز نوبت همین بازیکنه
    current = game.get_current_player()
    if current.user_id != user_id:
        return f"Turn already changed"
    
    # NEW: auto-action - درآمد خودکار
    print(f"[AUTO] Player {user_id} didn't act, auto-income +1 coin")
    game.add_coins(user_id, 1)
    game.next_turn()
    save_game(chat_id, game)
    
    return f"Auto-income for {user_id}"

# NEW: تایمر چالش - اگه کسی چالش نکرد، اکشن اجرا بشه
@celery_app.task(name="challenge_timer")
def challenge_timer(chat_id: int, action: str, actor_id: int, target_id: int = None):
    """
    تایمر چالش
    بعد از اتمام، اکشن اجرا میشه
    """
    game = get_game(chat_id)
    if not game or game.state != "CHALLENGING":
        return f"Game not in challenge state"
    
    # چک کن هنوز همین چالش در جریانه
    if game.current_action != action or game.actor_id != actor_id:
        return f"Challenge already resolved"
    
    print(f"[CHALLENGE] No challenge for {action}, executing...")
    
    # NEW: اجرای اکشن
    if action == "foreign_aid":
        game.add_coins(actor_id, 2)
        print(f"[EXECUTE] Foreign Aid: +2 coins to {actor_id}")
    
    elif action == "duke":
        game.add_coins(actor_id, 3)
        print(f"[EXECUTE] Duke Tax: +3 coins to {actor_id}")
    
    elif action == "assassin":
        if target_id:
            game.remove_coins(actor_id, 3)
            target = game.players[str(target_id)]
            if target.cards:
                lost_card = target.cards.pop()
                target.dead_cards.append(lost_card)
                if not target.cards:
                    target.is_alive = False
                print(f"[EXECUTE] Assassin: {target_id} lost {lost_card}")
    
    elif action == "captain":
        if target_id:
            target = game.players[str(target_id)]
            steal_amount = min(2, target.coins)
            game.remove_coins(target_id, steal_amount)
            game.add_coins(actor_id, steal_amount)
            print(f"[EXECUTE] Captain: stole {steal_amount} from {target_id}")
    
    # NEW: برگشت به حالت عادی
    game.state = "PLAYING"
    game.current_action = None
    game.actor_id = None
    game.target_id = None
    game.next_turn()
    save_game(chat_id, game)
    
    return f"Action {action} executed"

# NEW: کنسل کردن تایمر
@celery_app.task(name="cancel_timer")
def cancel_timer(task_id: str):
    """کنسل کردن یه تایمر فعال"""
    from celery.result import AsyncResult
    result = AsyncResult(task_id)
    result.revoke(terminate=True)
    print(f"[TIMER] Cancelled timer: {task_id}")
    return f"Timer {task_id} cancelled"