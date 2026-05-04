"""
main.py
FastAPI Webhook Handler for Coup Bot
این فایل فقط مسئول:
۱. دریافت Webhook از بله
۲. تبدیل به game_engine call
۳. ارسال پاسخ به بله
۴. مدیریت تایمر با Celery

هیچ منطق بازی توی این فایل نیست!
"""

from fastapi import FastAPI, Request
import httpx
import logging
import random
from typing import Optional, Dict, List, Tuple
from app.worker import turn_timer, challenge_timer, cancel_timer


from app.database import get_game, save_game, delete_game
from app.config import settings
from app.game_engine import (
    GameEngine, Player, GameState, GameMode,
    MIN_PLAYERS, MAX_PLAYERS
)
from app.models import GameStateModel

# ==================== Setup ====================

logger = logging.getLogger(__name__)
logging.basicConfig(level=getattr(logging, settings.LOG_LEVEL))

app = FastAPI()

# ==================== Bot API Helpers ====================

async def call_bot_api(method: str, payload: dict) -> Optional[httpx.Response]:
    """صدا زدن API بله با httpx"""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                f"{settings.API_BASE_URL}/bot{settings.BOT_TOKEN}/{method}",
                json=payload
            )
            if response.status_code != 200:
                logger.error(f"API Error [{method}]: {response.status_code} - {response.text}")
            return response
    except Exception as e:
        logger.error(f"API Call failed [{method}]: {e}")
        return None


async def send_message(chat_id: int, text: str, reply_markup: Optional[dict] = None):
    """ارسال پیام به گروه یا PV"""
    payload = {"chat_id": chat_id, "text": text}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    return await call_bot_api("sendMessage", payload)


async def edit_message_text(chat_id: int, message_id: int, text: str, reply_markup: Optional[dict] = None):
    """ویرایش پیام موجود"""
    payload = {"chat_id": chat_id, "message_id": message_id, "text": text}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    return await call_bot_api("editMessageText", payload)


async def delete_message(chat_id: int, message_id: int):
    """حذف پیام"""
    payload = {"chat_id": chat_id, "message_id": message_id}
    return await call_bot_api("deleteMessage", payload)


async def answer_callback(callback_id: str, text: str = "", show_alert: bool = False):
    """پاسخ به callback query (برای دکمه‌های شیشه‌ای)"""
    payload = {
        "callback_query_id": callback_id,
        "text": text,
        "show_alert": show_alert
    }
    return await call_bot_api("answerCallbackQuery", payload)

# ==================== Timer Helpers ====================

async def start_turn_timer(chat_id: int, user_id: int, duration: int) -> Optional[str]:
    """شروع تایمر نوبت با Celery"""
    if duration <= 0:
        return None
    try:
        task = turn_timer.apply_async(args=[chat_id, user_id], countdown=duration)  # type: ignore
        logger.info(f"Turn timer started: task_id={task.id}, chat={chat_id}, user={user_id}, duration={duration}s")
        return task.id
    except Exception as e:
        logger.error(f"Failed to start turn timer: {e}")
        return None


async def start_challenge_timer(chat_id: int, action: str, actor_id: int, target_id: Optional[int], duration: int) -> Optional[str]:
    """شروع تایمر چالش با Celery"""
    if duration <= 0:
        return None
    try:
        task = challenge_timer.apply_async( # type: ignore
            args=[chat_id, action, actor_id, target_id],
            countdown=duration
        )
        logger.info(f"Challenge timer started: task_id={task.id}")
        return task.id
    except Exception as e:
        logger.error(f"Failed to start challenge timer: {e}")
        return None


async def cancel_timer_task(task_id: str):
    """کنسل کردن تایمر Celery"""
    if not task_id:
        return
    try:
        cancel_timer.delay(task_id) # type: ignore
        logger.info(f"Timer cancelled: {task_id}")
    except Exception as e:
        logger.error(f"Failed to cancel timer: {e}")

# ==================== Helper Functions ====================

async def notify_player_cards(engine: GameEngine, user_id: int):
    """ارسال کارت‌های بازیکن به PV"""
    player = engine.players.get(user_id)
    if not player or not player.cards:
        return
    
    cards_text = "\n".join([f"🔹 {c}" for c in player.cards])
    await send_message(
        user_id,
        f"🃏 **کارت‌های شما:**\n\n{cards_text}\n\n💰 سکه: {player.coins}"
    )


def build_main_menu_keyboard(engine: GameEngine, user_id: int) -> dict:
    """ساخت کیبورد اصلی بر اساس وضعیت بازیکن"""
    player = engine.get_current_player()
    if not player:
        return {"inline_keyboard": []}
    
    keyboard = []
    
    # اگر مجبور به کودتا باشه
    if engine.must_coup():
        alive_others = [p for p in engine.players.values() if p.is_alive and p.user_id != player.user_id]
        target_buttons = [
            [{"text": f"💀 کودتا علیه {p.name}", "callback_data": f"coup_target_{p.user_id}"}]
            for p in alive_others
        ]
        return {"inline_keyboard": target_buttons}
    
    # اکشن‌های عمومی
    keyboard.append([{"text": "💰 درآمد (+۱ سکه)", "callback_data": "action_income"}])
    keyboard.append([{"text": "🌐 کمک خارجی (+۲ سکه)", "callback_data": "action_foreign_aid"}])
    
    # اکشن‌های شخصیت‌ها
    keyboard.append([{"text": "👑 دوک - مالیات (+۳ سکه)", "callback_data": "char_duke"}])
    keyboard.append([{"text": "🗡️ آدم‌کش - ترور (۳ سکه)", "callback_data": "char_assassin"}])
    keyboard.append([{"text": "🏴‍☠️ فرمانده - باج‌گیری (۲ سکه)", "callback_data": "char_captain"}])
    keyboard.append([{"text": "🔄 سفیر - تبادل کارت", "callback_data": "char_ambassador"}])
    
    # کودتا (اگه ۷+ سکه داره)
    if player.coins >= 7:
        keyboard.append([{"text": "💀 کودتا (۷ سکه)", "callback_data": "action_coup"}])
    
    # رد نوبت
    keyboard.append([{"text": "⏭️ رد نوبت", "callback_data": "skip_turn"}])
    
    return {"inline_keyboard": keyboard}


def build_block_foreign_keyboard(engine: GameEngine) -> dict:
    """ساخت کیبورد بلاک کمک خارجی"""
    return {
        "inline_keyboard": [
            [{"text": "🛡️ بلاک با دوک", "callback_data": "block_foreign"}],
            [{"text": "✅ قبول", "callback_data": "accept_action"}]
        ]
    }


def build_challenge_keyboard(engine: GameEngine) -> dict:
    """ساخت کیبورد چالش"""
    return {
        "inline_keyboard": [
            [{"text": "⚠️ مچ‌گیری (چالش)", "callback_data": "challenge"}],
            [{"text": "✅ قبول", "callback_data": "accept_action"}]
        ]
    }


def build_block_phase_keyboard(engine: GameEngine) -> dict:
    """ساخت کیبورد دفاع برای هدف"""
    keyboard = []
    
    if engine.action == 'سوقصد':
        keyboard.append([{"text": "🛡️ دفاع با شاه‌دخت", "callback_data": "block_contessa"}])
    elif engine.action == 'باج‌گیری':
        keyboard.append([{"text": "🛡️ دفاع با فرمانده", "callback_data": "block_captain"}])
        if engine.mode == GameMode.CLASSIC:
            keyboard.append([{"text": "🛡️ دفاع با سفیر", "callback_data": "block_ambassador"}])
        else:
            keyboard.append([{"text": "🛡️ دفاع با بازرس", "callback_data": "block_inquisitor"}])
    
    keyboard.append([{"text": "😔 تسلیم", "callback_data": "surrender"}])
    return {"inline_keyboard": keyboard}


def build_drop_keyboard(engine: GameEngine, user_id: int) -> dict:
    """ساخت کیبورد سوزاندن کارت"""
    cards = engine.get_drop_cards(user_id)
    if not cards:
        return {"inline_keyboard": []}
    
    buttons = [
        [{"text": f"🔥 سوزاندن {c}", "callback_data": f"drop_{i}"}]
        for i, c in enumerate(cards)
    ]
    return {"inline_keyboard": buttons}


def build_exchange_keyboard(engine: GameEngine, user_id: int) -> dict:
    """ساخت کیبورد تبادل کارت"""
    cards = engine.get_exchange_cards(user_id)
    if not cards:
        return {"inline_keyboard": []}
    
    buttons = []
    for i, c in enumerate(cards):
        buttons.append([{"text": f"➕ نگه‌داشتن {c}", "callback_data": f"exch_keep_{i}"}])
        buttons.append([{"text": f"↩️ برگشت {c}", "callback_data": f"exch_return_{i}"}])
    
    return {"inline_keyboard": buttons}


def build_inq_show_keyboard(engine: GameEngine, user_id: int) -> dict:
    """ساخت کیبورد نمایش کارت به بازرس"""
    cards = engine.get_inq_show_cards(user_id)
    if not cards:
        return {"inline_keyboard": []}
    
    buttons = [
        [{"text": f"👁 نمایش {c}", "callback_data": f"inqshow_{i}"}]
        for i, c in enumerate(cards)
    ]
    return {"inline_keyboard": buttons}


def build_inq_decide_keyboard() -> dict:
    """ساخت کیبورد تصمیم بازرس"""
    return {
        "inline_keyboard": [
            [{"text": "✅ نگه دارد", "callback_data": "inq_keep"}],
            [{"text": "🔄 تعویض کند", "callback_data": "inq_force_exchange"}]
        ]
    }


async def update_dashboard(chat_id: int, engine: GameEngine):
    """آپدیت پیام اصلی بازی در گروه"""
    if engine.msg_id is None:
        logger.warning(f"update_dashboard skipped: msg_id is None for chat {chat_id}")
        return
    
    text = build_dashboard_text(engine)
    markup = build_dashboard_keyboard(engine)
    
    await edit_message_text(chat_id, engine.msg_id, text, markup)


def build_dashboard_text(engine: GameEngine) -> str:
    """ساخت متن داشبورد بازی"""
    state = engine.state
    
    mode_name = "کلاسیک" if engine.mode == GameMode.CLASSIC else "الحاقی (بازرس)"
    timer_txt = "بدون تایمر" if engine.timeout_sec == 0 else f"{engine.timeout_sec}s"
    
    text = f"📊 **بازی کودتا** | {mode_name} | ⏱ {timer_txt}\n\n"
    
    # وضعیت بازیکنان
    for uid in engine.order:
        p = engine.players.get(uid)
        if not p:
            continue
        if not p.is_alive:
            status = "💀 مرده"
        else:
            status = f"💳 {len(p.cards)} کارت | 💰 {p.coins} سکه"
        
        arrow = "👉 " if uid == engine.get_current_player_id() and p.is_alive else "   "
        text += f"{arrow}{p.name}: {status}\n"
    
    # متن مخصوص هر state
    if engine.state == GameState.PLAYING:
        player = engine.get_current_player()
        if player:
            text += f"\n🔔 نوبت **{player.name}** است."
            if engine.must_coup():
                text += "\n⚠️ **۱۰+ سکه! مجبور به کودتا هستید!**"
    
    elif engine.state == GameState.WAITING_TARGET:
        actor = engine.players.get(engine.actor_id) if engine.actor_id else None
        if actor:
            text += f"\n🎯 **{actor.name}** در حال انتخاب هدف..."
    
    elif engine.state == GameState.CHALLENGE_ACT:
        actor = engine.players.get(engine.actor_id) if engine.actor_id else None
        if actor:
            target_text = ""
            if engine.target_id and engine.target_id in engine.players:
                target_text = f" روی **{engine.players[engine.target_id].name}**"
            text += f"\n🚨 **{actor.name}** ادعای **{engine.claimed_card}** دارد{target_text}!"
            text += "\n⚠️ چالش می‌کنید؟"
    
    elif engine.state == GameState.BLOCK_FOREIGN:
        actor = engine.players.get(engine.actor_id) if engine.actor_id else None
        if actor:
            text += f"\n🌐 **{actor.name}** درخواست کمک خارجی دارد."
            text += "\n🛡️ کسی با دوک بلاک می‌کند؟"
    
    elif engine.state == GameState.BLOCK_PHASE:
        target = engine.players.get(engine.target_id) if engine.target_id else None
        if target:
            text += f"\n🛡️ **{target.name}** مورد حمله ({engine.action}) قرار گرفته!"
            text += "\nدفاع می‌کنی یا تسلیم می‌شوی؟"
    
    elif engine.state == GameState.CHALLENGE_BLK:
        blocker = engine.players.get(engine.blocker_id) if engine.blocker_id else None
        if blocker:
            text += f"\n🛡️ **{blocker.name}** با **{engine.block_card}** دفاع کرد!"
            text += "\n⚠️ چالش می‌کنید؟"
    
    elif engine.state == GameState.WAITING_DROP:
        dropper = engine.players.get(engine.dropping_uid) if engine.dropping_uid else None
        if dropper:
            text += f"\n⏳ **{dropper.name}** در حال سوزاندن کارت در PV..."
    
    elif engine.state == GameState.WAITING_EXCHANGE:
        text += "\n🔄 سفیر در حال تبادل کارت در PV..."
    
    elif engine.state == GameState.WAITING_INQ_SHOW:
        text += "\n👁 منتظر نمایش کارت به بازرس..."
    
    elif engine.state == GameState.WAITING_INQ_DECIDE:
        text += "\n👁 بازرس در حال تصمیم‌گیری..."
    
    # لاگ بازی
    if engine.game_log:
        text += f"\n\n📜 {' | '.join(engine.game_log)}"
    
    return text


def build_dashboard_keyboard(engine: GameEngine) -> dict:
    """ساخت کیبورد داشبورد بر اساس state"""
    if engine.state == GameState.PLAYING:
        current = engine.get_current_player()
        if current:
            return build_main_menu_keyboard(engine, current.user_id)
    
    return {"inline_keyboard": []}


async def handle_player_action(chat_id: int, engine: GameEngine, user_id: int, action: str, target_id: Optional[int] = None):
    """مدیریت اکشن‌های بازیکن و آپدیت بازی"""
    
    # === درآمد ===
    if action == "income":
        success, msg = engine.income(user_id)
        if not success:
            await answer_callback(str(chat_id) + "_" + str(user_id), msg, True)
            return
        
        save_game(chat_id, GameStateModel.from_engine(engine))
        
        # اگر بازی تموم شده
        if "🏆" in msg:
            await update_dashboard(chat_id, engine)
            await send_message(chat_id, msg)
            delete_game(chat_id)
            return
        
        # شروع تایمر نوبت بعدی
        next_player = engine.get_current_player()
        if next_player and engine.timeout_sec > 0:
            task_id = await start_turn_timer(chat_id, next_player.user_id, engine.timeout_sec)
            engine.timer_task_id = task_id
        
        save_game(chat_id, GameStateModel.from_engine(engine))
        await update_dashboard(chat_id, engine)
    
    # === کمک خارجی ===
    elif action == "foreign_aid":
        success, msg = engine.foreign_aid(user_id)
        if not success:
            await answer_callback(str(chat_id) + "_" + str(user_id), msg, True)
            return
        
        task_id = await start_challenge_timer(
            chat_id, "foreign_aid", user_id, None, engine.timeout_sec or 30
        )
        engine.timer_task_id = task_id
        
        save_game(chat_id, GameStateModel.from_engine(engine))
        await update_dashboard(chat_id, engine)
    
    # === دوک (مالیات) ===
    elif action == "duke":
        success, msg = engine.tax(user_id)
        if not success:
            await answer_callback(str(chat_id) + "_" + str(user_id), msg, True)
            return
        
        task_id = await start_challenge_timer(
            chat_id, "duke", user_id, None, engine.timeout_sec or 30
        )
        engine.timer_task_id = task_id
        
        save_game(chat_id, GameStateModel.from_engine(engine))
        await update_dashboard(chat_id, engine)
    
    # === سفیر (تبادل) ===
    elif action == "ambassador":
        success, msg = engine.exchange(user_id)
        if not success:
            await answer_callback(str(chat_id) + "_" + str(user_id), msg, True)
            return
        
        task_id = await start_challenge_timer(
            chat_id, "ambassador", user_id, None, engine.timeout_sec or 30
        )
        engine.timer_task_id = task_id
        
        save_game(chat_id, GameStateModel.from_engine(engine))
        await update_dashboard(chat_id, engine)
    
    # === آدم‌کش ===
    elif action == "assassin":
        alive_others = [p for p in engine.players.values() if p.is_alive and p.user_id != user_id]
        if not alive_others:
            await answer_callback(str(chat_id) + "_" + str(user_id), "هدفی وجود ندارد!", True)
            return
        
        buttons = [[{"text": f"🎯 {p.name}", "callback_data": f"assassinate_target_{p.user_id}"}] for p in alive_others]
        buttons.append([{"text": "🔙 انصراف", "callback_data": "action_cancel"}])
        
        if engine.msg_id is None:
            return

        await edit_message_text(
            chat_id, engine.msg_id,
            f"🗡️ انتخاب هدف ترور:",
            {"inline_keyboard": buttons}
        )

    
    # === فرمانده (باج‌گیری) ===
    elif action == "captain":
        alive_others = [p for p in engine.players.values() if p.is_alive and p.user_id != user_id and p.coins > 0]
        if not alive_others:
            await answer_callback(str(chat_id) + "_" + str(user_id), "هدفی با سکه وجود ندارد!", True)
            return
        
        buttons = [[{"text": f"🎯 {p.name} ({p.coins}💰)", "callback_data": f"steal_target_{p.user_id}"}] for p in alive_others]
        buttons.append([{"text": "🔙 انصراف", "callback_data": "action_cancel"}])
        
        if engine.msg_id is None:
            return

        await edit_message_text(
            chat_id, engine.msg_id,
            f"🗡️ انتخاب هدف ترور:",
            {"inline_keyboard": buttons}
        )

    
    # === کودتا ===
    elif action == "coup":
        alive_others = [p for p in engine.players.values() if p.is_alive and p.user_id != user_id]
        if not alive_others:
            await answer_callback(str(chat_id) + "_" + str(user_id), "هدفی وجود ندارد!", True)
            return
        
        buttons = [[{"text": f"💀 {p.name}", "callback_data": f"coup_target_{p.user_id}"}] for p in alive_others]
        buttons.append([{"text": "🔙 انصراف", "callback_data": "action_cancel"}])
        
        if engine.msg_id is None:
            return

        await edit_message_text(
            chat_id, engine.msg_id,
            f"🗡️ انتخاب هدف ترور:",
            {"inline_keyboard": buttons}
        )



async def handle_timeout_result(chat_id: int, engine: GameEngine, result: str):
    """هندل نتیجه تایم‌اوت"""
    if result == 'auto_income':
        player = engine.get_current_player()
        if player:
            engine.execute_auto_income(player.user_id)
    elif result == 'auto_accept':
        engine.execute_auto_accept()
    elif result == 'random_drop':
        if engine.dropping_uid:
            engine.random_drop(engine.dropping_uid)
    elif result == 'random_exchange':
        if engine.actor_id:
            engine.random_exchange(engine.actor_id)
    elif result == 'random_show':
        if engine.target_id:
            engine.random_show_to_inq(engine.target_id)
    elif result == 'keep_card':
        if engine.actor_id:
            engine.inq_decision(engine.actor_id, False)
    elif result == 'skip_turn':
        engine.next_turn()


# ==================== Lobby UI ====================

LOBBY_TEXT = """🎭 **بازی جدید کودتا!**

🔹 برای ورود روی دکمه زیر بزنید
🔹 سازنده می‌تواند تنظیمات را تغییر دهد
🔹 حداقل {min} و حداکثر {max} بازیکن"""

LOBBY_KEYBOARD = {
    "inline_keyboard": [
        [{"text": "🎮 ورود به بازی", "callback_data": "join"}],
        [{"text": "📚 راهنما", "callback_data": "show_guide"}],
        [{"text": "⚙️ تنظیمات", "callback_data": "show_settings"}],
        [{"text": "▶️ شروع بازی", "callback_data": "start_game"}]
    ]
}

SETTINGS_KEYBOARD = {
    "inline_keyboard": [
        [
            {"text": "۳۰s", "callback_data": "set_timer_30"},
            {"text": "۶۰s", "callback_data": "set_timer_60"},
            {"text": "۹۰s", "callback_data": "set_timer_90"},
            {"text": "۱۲۰s", "callback_data": "set_timer_120"}
        ],
        [{"text": "🔙 بازگشت", "callback_data": "back_to_lobby"}]
    ]
}

GUIDE_TEXT = """📚 **راهنمای بازی کودتا**

🎯 **هدف:** آخرین بازیکن زنده باشی!

🃏 **شخصیت‌ها:**
• 👑 دوک - مالیات ۳ سکه
• 🗡️ آدم‌کش - ترور (۳ سکه)
• 🛡️ شاه‌دخت - خنثی‌کردن ترور
• 🏴‍☠️ فرمانده - باج‌گیری ۲ سکه
• 🔄 سفیر - تبادل کارت‌ها

💰 **اکشن‌های پایه:**
• درآمد: +۱ سکه
• کمک خارجی: +۲ سکه (قابل بلاک با دوک)
• کودتا: ۷ سکه → حذف کارت هدف

⚠️ **چالش (مچ‌گیری):**
• اگه کسی ادعای شخصیتی کرد می‌تونی مچ بگیری
• راست می‌گفت → تو کارت از دست میدی
• بلوف می‌زد → اون کارت از دست میده"""

# ==================== Webhook Endpoint ====================

@app.get("/")
async def root():
    return {"status": "Coup Bot Running!", "version": "2.0"}


@app.post("/webhook")
async def webhook(request: Request):
    """Webhook endpoint برای دریافت آپدیت‌های بله"""
    try:
        update = await request.json()
        
        # ==================== MESSAGE ====================
        if "message" in update:
            msg = update["message"]
            chat_id = msg["chat"]["id"]
            text = msg.get("text", "")
            user = msg["from"]
            
            # /newgame
            if text.startswith("/newgame"):
                existing = get_game(chat_id)
                if existing:
                    await send_message(chat_id, "❌ یک بازی از قبل در جریان است!")
                    return {"status": "ok"}
                
                engine = GameEngine(chat_id=chat_id, creator_id=user["id"])
                
                engine.set_mode("classic")           # SET_MODE → SET_TIMER
                engine.set_timer(engine.timeout_sec) # SET_TIMER → LOBBY
                
                engine.join(user["id"], user.get("first_name", "ناشناس"))
                
                save_game(chat_id, GameStateModel.from_engine(engine))
                
                await send_message(
                    chat_id,
                    LOBBY_TEXT.format(min=MIN_PLAYERS, max=MAX_PLAYERS),
                    LOBBY_KEYBOARD
                )
                logger.info(f"New game created in chat {chat_id} by user {user['id']}")
                return {"status": "ok"}
            
            # /stopgame
            elif text.startswith("/stopgame"):
                game_model = get_game(chat_id)
                if not game_model:
                    await send_message(chat_id, "❌ بازی در جریانی نیست!")
                    return {"status": "ok"}
                
                engine = game_model.to_engine()
                if user["id"] != engine.creator_id:
                    await send_message(chat_id, "❌ فقط سازنده می‌تواند متوقف کند!")
                    return {"status": "ok"}
                
                delete_game(chat_id)
                await send_message(chat_id, "🛑 بازی متوقف شد.")
                return {"status": "ok"}
            
            # /rules
            elif text.startswith("/rules"):
                await send_message(chat_id, GUIDE_TEXT)
                return {"status": "ok"}
            
            # /status
            elif text.startswith("/status"):
                game_model = get_game(chat_id)
                if not game_model:
                    await send_message(chat_id, "⚠️ بازی در جریانی نیست!")
                    return {"status": "ok"}
                
                engine = game_model.to_engine()
                if user["id"] != engine.creator_id:
                    await send_message(chat_id, "❌ فقط سازنده می‌تواند وضعیت را ببیند!")
                    return {"status": "ok"}
                
                state = engine.state
                waiting_for = []
                
                if state == GameState.PLAYING:
                    p = engine.get_current_player()
                    if p:
                        waiting_for.append(p.name)
                elif state == GameState.WAITING_TARGET:
                    p = engine.players.get(engine.actor_id)
                    if p:
                        waiting_for.append(p.name)
                elif state == GameState.WAITING_DROP:
                    p = engine.players.get(engine.dropping_uid)
                    if p:
                        waiting_for.append(p.name)
                else:
                    waiting_for.append("واکنش گروه")
                
                await send_message(chat_id, f"⏳ منتظر: {', '.join(waiting_for)}")
                return {"status": "ok"}
            
            # /skip
            elif text.startswith("/skip"):
                game_model = get_game(chat_id)
                if not game_model:
                    await send_message(chat_id, "⚠️ بازی در جریانی نیست!")
                    return {"status": "ok"}
                
                engine = game_model.to_engine()
                if user["id"] != engine.creator_id:
                    await send_message(chat_id, "❌ فقط سازنده می‌تواند اسکیپ کند!")
                    return {"status": "ok"}
                
                result = engine.handle_timeout(engine.state, engine.turn_index)
                await handle_timeout_result(chat_id, engine, result)
                save_game(chat_id, GameStateModel.from_engine(engine))
                await update_dashboard(chat_id, engine)
                await send_message(chat_id, "⏭️ سازنده نوبت را رد کرد!")
                return {"status": "ok"}
        
        # ==================== CALLBACK QUERY ====================
        elif "callback_query" in update:
            cq = update["callback_query"]
            chat_id = cq["message"]["chat"]["id"]
            message_id = cq["message"]["message_id"]
            user = cq["from"]
            data = cq["data"]
            cq_id = cq["id"]
            
            # ==================== LOBBY CALLBACKS ====================
            
            if data == "join":
                game_model = get_game(chat_id)
                if not game_model:
                    await answer_callback(cq_id, "❌ بازی وجود ندارد!", True)
                    return {"status": "ok"}
                
                engine = game_model.to_engine()
                engine.msg_id = message_id
                
                success = engine.join(user["id"], user.get("first_name", "ناشناس"))
                if not success:
                    await answer_callback(cq_id, "⚠️ نمی‌توانید وارد شوید!", True)
                    return {"status": "ok"}
                
                save_game(chat_id, GameStateModel.from_engine(engine))
                
                players_list = "\n".join([f"{i+1}. {p.name}" for i, (uid, p) in enumerate(engine.players.items())])
                await edit_message_text(
                    chat_id, message_id,
                    f"🎭 **لابی بازی**\n\n👥 بازیکنان ({len(engine.players)}/{MAX_PLAYERS}):\n{players_list}",
                    LOBBY_KEYBOARD
                )
                await answer_callback(cq_id, "✅ وارد شدید!")
                return {"status": "ok"}
            
            elif data == "show_guide":
                await answer_callback(cq_id, "📚 راهنما ارسال شد")
                await send_message(chat_id, GUIDE_TEXT)
                return {"status": "ok"}
            
            elif data == "show_settings":
                game_model = get_game(chat_id)
                if not game_model:
                    await answer_callback(cq_id, "❌", True)
                    return {"status": "ok"}
                
                engine = game_model.to_engine()
                if user["id"] != engine.creator_id:
                    await answer_callback(cq_id, "❌ فقط سازنده!", True)
                    return {"status": "ok"}
                
                await edit_message_text(
                    chat_id, message_id,
                    f"⚙️ **تنظیمات تایمر**\n\nفعلی: {engine.timeout_sec}s",
                    SETTINGS_KEYBOARD
                )
                await answer_callback(cq_id, "⚙️")
                return {"status": "ok"}
            
            elif data.startswith("set_timer_"):
                game_model = get_game(chat_id)
                if not game_model:
                    await answer_callback(cq_id, "❌", True)
                    return {"status": "ok"}
                
                engine = game_model.to_engine()
                if user["id"] != engine.creator_id:
                    await answer_callback(cq_id, "❌ فقط سازنده!", True)
                    return {"status": "ok"}
                
                seconds = int(data.replace("set_timer_", ""))
                engine.timeout_sec = seconds
                engine.msg_id = message_id
                save_game(chat_id, GameStateModel.from_engine(engine))
                
                players_list = "\n".join([f"{i+1}. {p.name}" for i, (uid, p) in enumerate(engine.players.items())])
                await edit_message_text(
                    chat_id, message_id,
                    f"🎭 **لابی بازی**\n\n⏱ تایمر: {seconds}s\n👥 بازیکنان ({len(engine.players)}/{MAX_PLAYERS}):\n{players_list}",
                    LOBBY_KEYBOARD
                )
                await answer_callback(cq_id, f"✅ تایمر: {seconds}s")
                return {"status": "ok"}
            
            elif data == "back_to_lobby":
                game_model = get_game(chat_id)
                if not game_model:
                    await answer_callback(cq_id, "❌", True)
                    return {"status": "ok"}
                
                engine = game_model.to_engine()
                players_list = "\n".join([f"{i+1}. {p.name}" for i, (uid, p) in enumerate(engine.players.items())])
                await edit_message_text(
                    chat_id, message_id,
                    f"🎭 **لابی بازی**\n\n👥 بازیکنان ({len(engine.players)}/{MAX_PLAYERS}):\n{players_list}",
                    LOBBY_KEYBOARD
                )
                await answer_callback(cq_id, "🔙")
                return {"status": "ok"}
            
            elif data == "start_game":
                game_model = get_game(chat_id)
                if not game_model:
                    await answer_callback(cq_id, "❌", True)
                    return {"status": "ok"}
                
                engine = game_model.to_engine()
                engine.msg_id = message_id
                
                if user["id"] != engine.creator_id:
                    await answer_callback(cq_id, "❌ فقط سازنده می‌تواند شروع کند!", True)
                    return {"status": "ok"}
                
                if not engine.can_start():
                    await answer_callback(cq_id, f"⚠️ حداقل {MIN_PLAYERS} بازیکن نیاز است!", True)
                    return {"status": "ok"}
                
                if not engine.mode:
                    engine.set_mode("classic")
    
                if engine.state == GameState.SET_TIMER:      # ← این خط رو اضافه کن
                    engine.set_timer(engine.timeout_sec)      # ← این خط رو اضافه کن                    
                
                success, msg = engine.start_game()
                if not success:
                    await answer_callback(cq_id, msg, True)
                    return {"status": "ok"}
                
                # ذخیره اولیه با msg_id
                save_game(chat_id, GameStateModel.from_engine(engine))
                
                # ارسال کارت‌ها به PV
                for uid in engine.order:
                    await notify_player_cards(engine, uid)
                
                # تایمر نوبت اول
                current = engine.get_current_player()
                if current and engine.timeout_sec > 0:
                    task_id = await start_turn_timer(chat_id, current.user_id, engine.timeout_sec)
                    engine.timer_task_id = task_id
                    save_game(chat_id, GameStateModel.from_engine(engine))
                
                await update_dashboard(chat_id, engine)
                await answer_callback(cq_id, "🎮 بازی شروع شد!")
                return {"status": "ok"}
            
            # ==================== GAME CALLBACKS ====================
            
            game_model = get_game(chat_id)
            if not game_model:
                await answer_callback(cq_id, "❌ بازی وجود ندارد!", True)
                return {"status": "ok"}
            
            engine = game_model.to_engine()
            engine.msg_id = message_id  # همیشه msg_id رو از callback بگیر
            
            # ACTION: INCOME
            if data == "action_income":
                await handle_player_action(chat_id, engine, user["id"], "income")
            
            # ACTION: FOREIGN AID
            elif data == "action_foreign_aid":
                await handle_player_action(chat_id, engine, user["id"], "foreign_aid")
            
            # ACTION: COUP
            elif data == "action_coup":
                await handle_player_action(chat_id, engine, user["id"], "coup")
            
            # COUP TARGET
            elif data.startswith("coup_target_"):
                target_id = int(data.replace("coup_target_", ""))
                success, msg = engine.coup(user["id"], target_id)
                if not success:
                    await answer_callback(cq_id, msg, True)
                    return {"status": "ok"}
                
                if msg == "drop_required":
                    save_game(chat_id, GameStateModel.from_engine(engine))
                    await update_dashboard(chat_id, engine)
                    await send_message(
                        target_id,
                        "💀 **کودتا شدید!** یک کارت باید بسوزانید:",
                        build_drop_keyboard(engine, target_id)
                    )
                    await answer_callback(cq_id, "💀 کودتا انجام شد")
                return {"status": "ok"}
            
            # CHARACTER: DUKE
            elif data == "char_duke":
                await handle_player_action(chat_id, engine, user["id"], "duke")
            
            # CHARACTER: ASSASSIN
            elif data == "char_assassin":
                await handle_player_action(chat_id, engine, user["id"], "assassin")
            
            # ASSASSIN TARGET
            elif data.startswith("assassinate_target_"):
                target_id = int(data.replace("assassinate_target_", ""))
                success, msg = engine.assassinate(user["id"], target_id)
                if not success:
                    await answer_callback(cq_id, msg, True)
                    return {"status": "ok"}
                
                engine.state = GameState.CHALLENGE_ACT
                engine.responses = []
                
                task_id = await start_challenge_timer(
                    chat_id, "assassin", user["id"], target_id, engine.timeout_sec or 30
                )
                engine.timer_task_id = task_id
                
                save_game(chat_id, GameStateModel.from_engine(engine))
                await update_dashboard(chat_id, engine)
                return {"status": "ok"}
            
            # CHARACTER: CAPTAIN
            elif data == "char_captain":
                await handle_player_action(chat_id, engine, user["id"], "captain")
            
            # STEAL TARGET
            elif data.startswith("steal_target_"):
                target_id = int(data.replace("steal_target_", ""))
                success, msg = engine.steal(user["id"], target_id)
                if not success:
                    await answer_callback(cq_id, msg, True)
                    return {"status": "ok"}
                
                engine.state = GameState.CHALLENGE_ACT
                engine.responses = []
                
                task_id = await start_challenge_timer(
                    chat_id, "captain", user["id"], target_id, engine.timeout_sec or 30
                )
                engine.timer_task_id = task_id
                
                save_game(chat_id, GameStateModel.from_engine(engine))
                await update_dashboard(chat_id, engine)
                return {"status": "ok"}
            
            # CHARACTER: AMBASSADOR
            elif data == "char_ambassador":
                await handle_player_action(chat_id, engine, user["id"], "ambassador")
            
            # SKIP TURN
            elif data == "skip_turn":
                if not engine.is_player_turn(user["id"]):
                    await answer_callback(cq_id, "⚠️ نوبت شما نیست!", True)
                    return {"status": "ok"}
                
                engine.game_log.append(f"⏭️ {engine.players[user['id']].name} نوبت را رد کرد")
                engine.next_turn()
                
                current = engine.get_current_player()
                if current and engine.timeout_sec > 0:
                    task_id = await start_turn_timer(chat_id, current.user_id, engine.timeout_sec)
                    engine.timer_task_id = task_id
                
                save_game(chat_id, GameStateModel.from_engine(engine))
                await update_dashboard(chat_id, engine)
                await answer_callback(cq_id, "⏭️ نوبت رد شد")
                return {"status": "ok"}
            
            # CANCEL
            elif data == "action_cancel":
                await update_dashboard(chat_id, engine)
                await answer_callback(cq_id, "🔙")
                return {"status": "ok"}
            
            # ==================== CHALLENGE & ACCEPT ====================
            
            elif data == "accept_action":
                success, msg = engine.accept(user["id"])
                if not success:
                    await answer_callback(cq_id, msg, True)
                    return {"status": "ok"}
                
                if msg == "all_accepted":
                    if engine.timer_task_id:
                        await cancel_timer_task(engine.timer_task_id)
                    
                    result, result_msg = engine.execute_action_after_challenge()
                    
                    if result_msg == "exchange_started":
                        save_game(chat_id, GameStateModel.from_engine(engine))
                        await update_dashboard(chat_id, engine)
                        await send_message(
                            engine.actor_id,
                            f"🔄 **سفیر:** کارت‌های خود را انتخاب کنید\n\nکارت‌های موجود: {', '.join(engine.get_exchange_cards(engine.actor_id) or [])}",
                            build_exchange_keyboard(engine, engine.actor_id)
                        )
                        await answer_callback(cq_id, "✅ تبادل شروع شد")
                        return {"status": "ok"}
                    
                    elif result_msg == "block_phase":
                        save_game(chat_id, GameStateModel.from_engine(engine))
                        await update_dashboard(chat_id, engine)
                        await answer_callback(cq_id, "✅ منتظر دفاع هدف")
                        return {"status": "ok"}
                    
                    else:
                        save_game(chat_id, GameStateModel.from_engine(engine))
                        
                        winner = engine._check_winner()
                        if winner:
                            await update_dashboard(chat_id, engine)
                            await send_message(chat_id, f"🏆 {winner.name} برنده شد!")
                            delete_game(chat_id)
                            return {"status": "ok"}
                        
                        current = engine.get_current_player()
                        if current and engine.timeout_sec > 0:
                            task_id = await start_turn_timer(chat_id, current.user_id, engine.timeout_sec)
                            engine.timer_task_id = task_id
                        
                        save_game(chat_id, GameStateModel.from_engine(engine))
                        await update_dashboard(chat_id, engine)
                        await answer_callback(cq_id, "✅ انجام شد")
                        return {"status": "ok"}
                
                save_game(chat_id, GameStateModel.from_engine(engine))
                await answer_callback(cq_id, "✅ پذیرفته شد")
                return {"status": "ok"}
            
            elif data == "challenge":
                result = engine.challenge(user["id"])
                
                if not result.message:
                    await answer_callback(cq_id, "❌ نمی‌توانید چالش کنید!", True)
                    return {"status": "ok"}
                
                if result.challenger_lost:
                    save_game(chat_id, GameStateModel.from_engine(engine))
                    await update_dashboard(chat_id, engine)
                    await send_message(chat_id, result.message)
                    
                    if len(engine.players[result.challenger_id].cards) > 1:
                        await send_message(
                            result.challenger_id,
                            "❌ چالش ناموفق! یک کارت را برای سوزاندن انتخاب کنید:",
                            build_drop_keyboard(engine, result.challenger_id)
                        )
                    else:
                        engine.drop_card(result.challenger_id, 0)
                        save_game(chat_id, GameStateModel.from_engine(engine))
                
                elif result.success:
                    save_game(chat_id, GameStateModel.from_engine(engine))
                    await update_dashboard(chat_id, engine)
                    await send_message(chat_id, result.message)
                    
                    if len(engine.players[result.target_id].cards) > 1:
                        await send_message(
                            result.target_id,
                            "❌ مچ‌گیری شدید! یک کارت را برای سوزاندن انتخاب کنید:",
                            build_drop_keyboard(engine, result.target_id)
                        )
                    else:
                        engine.drop_card(result.target_id, 0)
                        save_game(chat_id, GameStateModel.from_engine(engine))
                
                await answer_callback(cq_id, "⚠️")
                return {"status": "ok"}
            
            # ==================== BLOCK ====================
            
            elif data == "block_foreign":
                success, msg = engine.block_foreign_aid(user["id"])
                if not success:
                    await answer_callback(cq_id, msg, True)
                    return {"status": "ok"}
                
                save_game(chat_id, GameStateModel.from_engine(engine))
                await update_dashboard(chat_id, engine)
                await answer_callback(cq_id, "🛡️ بلاک شد")
                return {"status": "ok"}
            
            elif data == "block_contessa":
                if user["id"] != engine.target_id:
                    await answer_callback(cq_id, "❌ فقط هدف می‌تواند دفاع کند!", True)
                    return {"status": "ok"}
                
                success, msg = engine.block_assassinate(user["id"])
                if not success:
                    await answer_callback(cq_id, msg, True)
                    return {"status": "ok"}
                
                save_game(chat_id, GameStateModel.from_engine(engine))
                await update_dashboard(chat_id, engine)
                await answer_callback(cq_id, "🛡️ دفاع با شاه‌دخت")
                return {"status": "ok"}
            
            elif data.startswith("block_") and data in ["block_captain", "block_ambassador", "block_inquisitor"]:
                if user["id"] != engine.target_id:
                    await answer_callback(cq_id, "❌ فقط هدف می‌تواند دفاع کند!", True)
                    return {"status": "ok"}
                
                card_map = {
                    "block_captain": "فرمانده",
                    "block_ambassador": "سفیر",
                    "block_inquisitor": "بازرس"
                }
                engine.set_block_card(card_map[data])
                success, msg = engine.block_steal(user["id"])
                if not success:
                    await answer_callback(cq_id, msg, True)
                    return {"status": "ok"}
                
                save_game(chat_id, GameStateModel.from_engine(engine))
                await update_dashboard(chat_id, engine)
                await answer_callback(cq_id, f"🛡️ دفاع با {card_map[data]}")
                return {"status": "ok"}
            
            elif data == "surrender":
                if user["id"] != engine.target_id:
                    await answer_callback(cq_id, "❌ فقط هدف می‌تواند تسلیم شود!", True)
                    return {"status": "ok"}
                
                success, msg = engine.surrender(user["id"])
                if not success:
                    await answer_callback(cq_id, msg, True)
                    return {"status": "ok"}
                
                if msg == "drop_required":
                    save_game(chat_id, GameStateModel.from_engine(engine))
                    await update_dashboard(chat_id, engine)
                    await send_message(
                        engine.target_id,
                        "⚠️ حمله موفق بود! یک کارت را برای سوزاندن انتخاب کنید:",
                        build_drop_keyboard(engine, engine.target_id)
                    )
                    await answer_callback(cq_id, "😔 تسلیم")
                else:
                    save_game(chat_id, GameStateModel.from_engine(engine))
                    await update_dashboard(chat_id, engine)
                    await answer_callback(cq_id, msg)
                return {"status": "ok"}
            
            # ==================== DROP ====================
            
            elif data.startswith("drop_"):
                card_index = int(data.replace("drop_", ""))
                success, msg = engine.drop_card(user["id"], card_index)
                
                if not success:
                    await answer_callback(cq_id, msg, True)
                    return {"status": "ok"}
                
                player = engine.players[user["id"]]
                if player.cards:
                    await send_message(user["id"], f"🔥 کارت سوخت!\nکارت‌های باقی‌مانده: {', '.join(player.cards)}")
                else:
                    await send_message(user["id"], "💀 آخرین کارت شما سوخت! حذف شدید.")
                
                save_game(chat_id, GameStateModel.from_engine(engine))
                
                winner = engine._check_winner()
                if winner:
                    await update_dashboard(chat_id, engine)
                    await send_message(chat_id, f"🏆 {winner.name} برنده شد!")
                    delete_game(chat_id)
                    return {"status": "ok"}
                
                if msg == "next_turn":
                    current = engine.get_current_player()
                    if current and engine.timeout_sec > 0:
                        task_id = await start_turn_timer(chat_id, current.user_id, engine.timeout_sec)
                        engine.timer_task_id = task_id
                    save_game(chat_id, GameStateModel.from_engine(engine))
                    await update_dashboard(chat_id, engine)
                elif msg == "block_phase":
                    save_game(chat_id, GameStateModel.from_engine(engine))
                    await update_dashboard(chat_id, engine)
                elif msg.startswith("exchange") or msg.startswith("inq"):
                    save_game(chat_id, GameStateModel.from_engine(engine))
                    await update_dashboard(chat_id, engine)
                
                await answer_callback(cq_id, "🔥")
                return {"status": "ok"}
            
            # ==================== EXCHANGE ====================
            
            elif data.startswith("exch_keep_"):
                card_index = int(data.replace("exch_keep_", ""))
                cards = engine.get_exchange_cards(user["id"])
                if not cards or card_index >= len(cards):
                    await answer_callback(cq_id, "❌ کارت نامعتبر!", True)
                    return {"status": "ok"}
                
                card = cards[card_index]
                success, msg = engine.keep_exchange_card(user["id"], card)
                
                if not success:
                    await answer_callback(cq_id, msg, True)
                    return {"status": "ok"}
                
                if "کامل شد" in msg:
                    save_game(chat_id, GameStateModel.from_engine(engine))
                    await update_dashboard(chat_id, engine)
                    await send_message(
                        user["id"],
                        f"✅ تبادل انجام شد!\nکارت‌های شما: {', '.join(engine.players[user['id']].cards)}"
                    )
                    await answer_callback(cq_id, "✅ تبادل کامل شد")
                else:
                    save_game(chat_id, GameStateModel.from_engine(engine))
                    cards = engine.get_exchange_cards(user["id"]) or []
                    cards_text = ', '.join(cards)
                    await send_message(
                        user["id"],
                        msg + f"\n\nکارت‌های موجود: {cards_text}",
                        build_exchange_keyboard(engine, user["id"])
                    )
                    await answer_callback(cq_id, "✅")
                return {"status": "ok"}
            
            elif data.startswith("exch_return_"):
                card_index = int(data.replace("exch_return_", ""))
                cards = engine.get_exchange_cards(user["id"])
                if not cards or card_index >= len(cards):
                    await answer_callback(cq_id, "❌ کارت نامعتبر!", True)
                    return {"status": "ok"}
                
                card = cards[card_index]
                success, msg = engine.return_exchange_card(user["id"], card)
                
                if not success:
                    await answer_callback(cq_id, msg, True)
                    return {"status": "ok"}
                
                if "کامل شد" in msg:
                    save_game(chat_id, GameStateModel.from_engine(engine))
                    await update_dashboard(chat_id, engine)
                    await send_message(
                        user["id"],
                        f"✅ تبادل انجام شد!\nکارت‌های شما: {', '.join(engine.players[user['id']].cards)}"
                    )
                    await answer_callback(cq_id, "✅ تبادل کامل شد")
                else:
                    save_game(chat_id, GameStateModel.from_engine(engine))
                    exchange_cards = engine.get_exchange_cards(user["id"]) or []
                    cards_text = ', '.join(exchange_cards)
                    await send_message(
                        user["id"],
                        msg + f"\n\nکارت‌های موجود: {cards_text}",
                        build_exchange_keyboard(engine, user["id"])
                    )
                    await answer_callback(cq_id, "↩️")
                return {"status": "ok"}
    
    except Exception as e:
        logger.error(f"Webhook error: {e}", exc_info=True)
        return {"status": "error", "message": str(e)}
    
    return {"status": "ok"}