"""
Celery Worker for Coup Bot
مسئول تایمرها و پردازش‌های async
جایگزین threading.Timer در کد فلسک
"""

from celery import Celery
import time
import random
from app.database import get_game, save_game, delete_game
from app.game_engine import GameEngine, GameState

# ==================== Celery App ====================

celery_app = Celery(
    "coup_worker",
    broker="redis://127.0.0.1:6379/0",
    backend="redis://127.0.0.1:6379/0"
)

celery_app.conf.broker_connection_retry_on_startup = True

# ==================== Turn Timer ====================

@celery_app.task(name="turn_timer", bind=True, max_retries=3, default_retry_delay=5)
def turn_timer(self, chat_id: int, user_id: int):
    """
    تایمر نوبت - اگر بازیکن در زمان مقرر حرکت نکرد:
    ۱. Auto-Income (+۱ سکه)
    ۲. نوبت بعدی
    """
    try:
        game_model = get_game(chat_id)
        if not game_model:
            return f"[TIMER] Game {chat_id} not found"
        
        engine = game_model.to_engine()
        
        # چک کن بازی هنوز تو همون state و نوبته
        if engine.state != GameState.PLAYING:
            return f"[TIMER] Game not in PLAYING state: {engine.state}"
        
        current = engine.get_current_player()
        if not current or current.user_id != user_id:
            return f"[TIMER] Turn already changed (current: {current.user_id if current else 'none'}, expected: {user_id})"
        
        # Auto Income
        engine.players[user_id].coins += 1
        engine.game_log.append(f"⏰ {engine.players[user_id].name} حرکت نکرد، +۱ سکه خودکار")
        engine._trim_log()
        
        # نوبت بعدی
        engine.next_turn()
        
        # ذخیره
        save_game(chat_id, engine._to_model())
        
        # لاگ
        import logging
        logger = logging.getLogger(__name__)
        logger.info(f"[AUTO-INCOME] Player {user_id} in chat {chat_id} - +1 coin")
        
        return f"[AUTO-INCOME] Chat {chat_id}, Player {user_id}, next turn: {engine.get_current_player_id()}"
    
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"[TIMER ERROR] turn_timer failed: {e}", exc_info=True)
        
        # Retry with exponential backoff
        raise self.retry(exc=e)


# ==================== Challenge Timer ====================

@celery_app.task(name="challenge_timer", bind=True, max_retries=3, default_retry_delay=5)
def challenge_timer(self, chat_id: int, action: str, actor_id: int, target_id: int = None):
    """
    تایمر چالش - اگر در زمان مقرر هیچکس چالش نکرد:
    ۱. اکشن اجرا میشه (auto-accept)
    ۲. نوبت بعدی
    """
    try:
        game_model = get_game(chat_id)
        if not game_model:
            return f"[CHALLENGE TIMER] Game {chat_id} not found"
        
        engine = game_model.to_engine()
        
        # چک کن بازی هنوز تو state چالشه
        valid_states = [GameState.CHALLENGE_ACT, GameState.BLOCK_FOREIGN, 
                       GameState.CHALLENGE_BLK, GameState.BLOCK_PHASE]
        
        if engine.state not in valid_states:
            return f"[CHALLENGE TIMER] State already changed: {engine.state}"
        
        # چک کن همون اکشن هنوز جاری باشه
        if engine.action != action or engine.actor_id != actor_id:
            return f"[CHALLENGE TIMER] Action already changed"
        
        import logging
        logger = logging.getLogger(__name__)
        logger.info(f"[CHALLENGE TIMEOUT] Chat {chat_id}, Action: {action}, Auto-accepting...")
        
        # Auto-accept based on state
        if engine.state == GameState.CHALLENGE_ACT:
            result, msg = engine.execute_action_after_challenge()
            engine.game_log.append(f"⏰ زمان چالش تمام شد - اکشن {action} اجرا شد")
            
        elif engine.state == GameState.BLOCK_FOREIGN:
            engine.players[actor_id].coins += 2
            engine.game_log.append(f"🌐 {engine.players[actor_id].name} +۲ سکه کمک خارجی (زمان تمام شد)")
            engine.next_turn()
            
        elif engine.state == GameState.CHALLENGE_BLK:
            engine.game_log.append(f"⏰ زمان چالش دفاع تمام شد - دفاع پذیرفته شد")
            engine.next_turn()
            
        elif engine.state == GameState.BLOCK_PHASE:
            # Apply attack (no block)
            if engine.action == 'سوقصد':
                engine.players[actor_id].coins -= 3
                engine.next_step = 'next_turn'
                engine.dropping_uid = target_id
                engine.state = GameState.WAITING_DROP
                engine.game_log.append(f"⏰ {engine.players[target_id].name} دفاع نکرد - سوقصد اجرا شد")
            elif engine.action == 'باج‌گیری':
                target = engine.players[target_id]
                steal_amount = min(2, target.coins)
                target.coins -= steal_amount
                engine.players[actor_id].coins += steal_amount
                engine.game_log.append(f"🏴‍☠️ {steal_amount} سکه دزدیده شد (زمان دفاع تمام شد)")
                engine.next_turn()
        
        engine._trim_log()
        
        # ذخیره
        save_game(chat_id, engine._to_model())
        
        return f"[CHALLENGE TIMEOUT] Chat {chat_id}, Action: {action}, Auto-accepted"
    
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"[CHALLENGE TIMER ERROR] Failed: {e}", exc_info=True)
        
        raise self.retry(exc=e)


# ==================== Cancel Timer ====================

@celery_app.task(name="cancel_timer")
def cancel_timer(task_id: str):
    """
    کنسل کردن یه تایمر در حال اجرا
    وقتی بازیکن قبل از timeout حرکت می‌کنه
    """
    try:
        from celery.result import AsyncResult
        result = AsyncResult(task_id)
        result.revoke(terminate=True)
        
        import logging
        logger = logging.getLogger(__name__)
        logger.info(f"[TIMER CANCELLED] Task: {task_id}")
        
        return f"[TIMER CANCELLED] {task_id}"
    
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"[CANCEL TIMER ERROR] Failed to cancel {task_id}: {e}")
        return f"[ERROR] {e}"


# ==================== Garbage Collector ====================

@celery_app.task(name="garbage_collector")
def garbage_collector():
    """
    پاکسازی بازی‌های قدیمی (هر ساعت اجرا میشه)
    جایگزین garbage_collector توی کد فلسک
    """
    import redis
    import logging
    
    logger = logging.getLogger(__name__)
    
    try:
        r = redis.Redis(host='127.0.0.1', port=6379, db=0, decode_responses=True)
        
        # پیدا کردن همه game key ها
        keys = r.keys("game:*")
        current_time = time.time()
        deleted_count = 0
        
        for key in keys:
            try:
                data = r.get(key)
                if not data:
                    continue
                
                import json
                game_dict = json.loads(data)
                last_active = game_dict.get('last_active', 0)
                
                # اگر بیش از ۱ ساعت غیرفعال بوده
                if current_time - last_active > 3600:
                    chat_id = key.replace("game:", "")
                    r.delete(key)
                    deleted_count += 1
                    logger.info(f"[GC] Deleted inactive game: chat {chat_id}")
            
            except Exception as e:
                logger.error(f"[GC ERROR] Failed to process key {key}: {e}")
                continue
        
        logger.info(f"[GC] Cleanup complete. Deleted {deleted_count} inactive games")
        return f"[GC] Deleted {deleted_count} games"
    
    except Exception as e:
        logger.error(f"[GC ERROR] Garbage collector failed: {e}")
        return f"[GC ERROR] {e}"


# ==================== Beat Schedule ====================

from celery.schedules import crontab

celery_app.conf.beat_schedule = {
    'garbage-collector-hourly': {
        'task': 'garbage_collector',
        'schedule': crontab(minute=0),  # هر ساعت
    },
}

celery_app.conf.timezone = 'Asia/Tehran'