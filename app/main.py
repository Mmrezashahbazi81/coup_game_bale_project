# app/main.py
from fastapi import FastAPI, Request
import httpx
from app.models import GameState, Player
from app.database import get_game, save_game, delete_game
from app.config import settings
from app.worker import turn_timer, challenge_timer, cancel_timer
import logging
import random

logger = logging.getLogger(__name__)
logging.basicConfig(level=getattr(logging, settings.LOG_LEVEL))

app = FastAPI()

# ============================================
# توابع کمکی
# ============================================

async def send_message(chat_id: int, text: str, reply_markup: dict = None):
    try:
        async with httpx.AsyncClient() as client:
            payload = {"chat_id": chat_id, "text": text}
            if reply_markup:
                payload["reply_markup"] = reply_markup
            response = await client.post(settings.API_URL + "sendMessage", json=payload)
            logger.info(f"SEND MSG: {response.status_code}")
            return response
    except Exception as e:
        logger.error(f"Error sending message: {e}")

async def edit_message_text(chat_id: int, message_id: int, text: str, reply_markup: dict = None):
    try:
        async with httpx.AsyncClient() as client:
            payload = {"chat_id": chat_id, "message_id": message_id, "text": text}
            if reply_markup:
                payload["reply_markup"] = reply_markup
            response = await client.post(settings.API_URL + "editMessageText", json=payload)
            logger.info(f"EDIT MSG: {response.status_code}")
            return response
    except Exception as e:
        logger.error(f"Error editing message: {e}")

async def answer_callback_query(callback_query_id: str, text: str, show_alert: bool = False):
    try:
        async with httpx.AsyncClient() as client:
            payload = {"callback_query_id": callback_query_id, "text": text, "show_alert": show_alert}
            response = await client.post(settings.API_URL + "answerCallbackQuery", json=payload)
            logger.info(f"CALLBACK: {response.status_code}")
            return response
    except Exception as e:
        logger.error(f"Error answering callback: {e}")

def is_player_turn(game, user_id):
    if not game or game.state != "PLAYING":
        return False
    current_player = game.get_current_player()
    return current_player.user_id == user_id

# NEW: لاگ بازی - اضافه کردن به پیام اصلی
def add_game_log(game, message):
    """اضافه کردن لاگ به تاریخچه بازی"""
    if not hasattr(game, 'game_log'):
        game.game_log = []
    game.game_log.append(message)
    # فقط ۵ تای آخر رو نگه دار
    if len(game.game_log) > 5:
        game.game_log = game.game_log[-5:]

def get_game_log_text(game):
    """دریافت متن لاگ‌ها"""
    if not hasattr(game, 'game_log') or not game.game_log:
        return ""
    return "\n".join([f"📜 {log}" for log in game.game_log])

def get_main_menu_keyboard(game=None, user_id=None):
    """منوی اصلی با دکمه‌های کامل و مدیریت"""
    keyboard = [
        [{"text": "💰 درآمد (+۱ سکه)", "callback_data": "action_income"}],
        [{"text": "🌐 کمک خارجی (+۲ سکه)", "callback_data": "action_foreign_aid"}],
        [{"text": "💀 کودتا (۷ سکه)", "callback_data": "action_coup"}],
        [{"text": "👑 Duke - مالیات (۳ سکه)", "callback_data": "char_duke"}],
        [{"text": "🗡️ Assassin - ترور (۳ سکه)", "callback_data": "char_assassin"}],
        [{"text": "🏴‍☠️ Captain - دزدی (۲ سکه)", "callback_data": "char_captain"}],
        [{"text": "🔄 Ambassador - تعویض کارت", "callback_data": "char_ambassador"}],
        [{"text": "⏭️ رد نوبت", "callback_data": "skip_turn"}]
    ]
    
    # NEW: فقط سازنده بازی دکمه اتمام رو میبینه
    if game and user_id and game.creator_id == user_id:
        keyboard.append([{"text": "🏁 اتمام بازی", "callback_data": "end_game"}])
    
    return {"inline_keyboard": keyboard}

def get_game_status_text(game, user_id=None):
    """متن وضعیت بازی با لاگ"""
    next_player = game.get_current_player()
    players_status = "\n".join([
        f"{'🟢' if game.players[str(uid)].is_alive else '💀'} {game.players[str(uid)].name}: {game.players[str(uid)].coins}💰"
        for uid in game.player_order
    ])
    
    base_text = f"🎮 بازی در جریان است!\n\n👥 وضعیت:\n{players_status}\n\n🔹 نوبت: {next_player.name}"
    
    # NEW: اضافه کردن لاگ
    log_text = get_game_log_text(game)
    if log_text:
        base_text += f"\n\n{log_text}"
    
    return base_text

async def start_turn_timer(chat_id, user_id, turn_timer_duration):
    task = turn_timer.apply_async(args=[chat_id, user_id], countdown=turn_timer_duration)
    return task.id

async def start_challenge_timer_handler(chat_id, action, actor_id, target_id, challenge_timer_duration):
    task = challenge_timer.apply_async(args=[chat_id, action, actor_id, target_id], countdown=challenge_timer_duration)
    return task.id

def reveal_and_replace_card(game, player, revealed_card):
    """کارت لو رفته رو عوض کن"""
    if revealed_card in player.cards:
        player.cards.remove(revealed_card)
        game.deck.append(revealed_card)
        random.shuffle(game.deck)
        if game.deck:
            new_card = game.deck.pop()
            player.cards.append(new_card)
            return new_card
    return None

def check_player_has_character(player, character):
    return character in player.cards

# NEW: اجرای فوری اکشن (برای وقتی همه رد کردن)
def execute_action_immediately(game, action, actor_id, target_id):
    """اجرای فوری اکشن بدون نیاز به تایمر"""
    if action == "foreign_aid":
        game.add_coins(actor_id, 2)
        return f"{game.players[str(actor_id)].name} +۲ سکه کمک خارجی گرفت"
    
    elif action == "duke":
        game.add_coins(actor_id, 3)
        return f"{game.players[str(actor_id)].name} +۳ سکه مالیات گرفت"
    
    elif action == "assassin":
        if target_id:
            game.remove_coins(actor_id, 3)
            target = game.players[str(target_id)]
            if target.cards:
                lost_card = target.cards.pop()
                target.dead_cards.append(lost_card)
                if not target.cards:
                    target.is_alive = False
                return f"{game.players[str(actor_id)].name} 🗡️ {target.name} را ترور کرد! (-{lost_card})"
    
    elif action == "captain":
        if target_id:
            target = game.players[str(target_id)]
            steal_amount = min(2, target.coins)
            game.remove_coins(target_id, steal_amount)
            game.add_coins(actor_id, steal_amount)
            return f"{game.players[str(actor_id)].name} 🏴‍☠️ {steal_amount} سکه از {target.name} دزدید"
    
    return ""

# ============================================
# Endpoint ها
# ============================================

@app.get("/")
async def root():
    return {"status": "Bot is running!", "game": "Coup"}

@app.post("/webhook")
async def webhook(request: Request):
    try:
        update = await request.json()
        logger.info(f"Received update: {update}")
        
        if "message" in update:
            msg = update["message"]
            chat_id = msg["chat"]["id"]
            text = msg.get("text", "")
            user = msg["from"]
            
            if text.startswith("/newgame"):
                game = get_game(chat_id)
                if game:
                    await send_message(chat_id, "❌ یک بازی در حال اجراست!")
                    return {"status": "ok"}
                
                new_game = GameState(
                    chat_id=chat_id,
                    creator_id=user["id"],
                    state="LOBBY"
                )
                new_game.players[str(user["id"])] = Player(
                    user_id=user["id"],
                    name=user.get("first_name", "ناشناس")
                )
                # NEW: مقداردهی game_log
                new_game.game_log = []
                save_game(chat_id, new_game)
                
                reply_markup = {
                    "inline_keyboard": [
                        [{"text": "🎮 ورود به بازی", "callback_data": "join"}],
                        [{"text": "⚙️ تنظیمات", "callback_data": "settings"}],
                        [{"text": "▶️ شروع بازی", "callback_data": "start"}]
                    ]
                }
                text_msg = f"🎭 بازی جدید ایجاد شد!\n\n👥 بازیکنان:\n1. {user.get('first_name', 'ناشناس')}"
                await send_message(chat_id, text_msg, reply_markup)
                logger.info(f"New game created in chat {chat_id}")
        
        elif "callback_query" in update:
            cq = update["callback_query"]
            chat_id = cq["message"]["chat"]["id"]
            message_id = cq["message"]["message_id"]
            user = cq["from"]
            data = cq["data"]
            cq_id = cq["id"]
            
            # ============================================
            # END GAME (اتمام بازی توسط سازنده)
            # ============================================
            if data == "end_game":
                game = get_game(chat_id)
                if not game:
                    await answer_callback_query(cq_id, "❌ بازی نیست!", True)
                    return {"status": "ok"}
                
                if user["id"] != game.creator_id:
                    await answer_callback_query(cq_id, "❌ فقط سازنده بازی میتواند بازی را تمام کند!", True)
                    return {"status": "ok"}
                
                delete_game(chat_id)
                await edit_message_text(chat_id, message_id, "🏁 بازی توسط سازنده به پایان رسید.", None)
                await answer_callback_query(cq_id, "🏁 بازی تمام شد!")
                return {"status": "ok"}
            
            # ============================================
            # SKIP TURN (رد نوبت)
            # ============================================
            if data == "skip_turn":
                game = get_game(chat_id)
                if not game or game.state != "PLAYING":
                    await answer_callback_query(cq_id, "❌ بازی در جریان نیست!", True)
                    return {"status": "ok"}
                
                if not is_player_turn(game, user["id"]):
                    await answer_callback_query(cq_id, "⚠️ نوبت شما نیست!", True)
                    return {"status": "ok"}
                
                if game.timer_task_id:
                    cancel_timer.delay(game.timer_task_id)
                
                player_name = game.players[str(user["id"])].name
                add_game_log(game, f"⏭️ {player_name} نوبت را رد کرد")
                
                game.next_turn()
                save_game(chat_id, game)
                
                next_player = game.get_current_player()
                task_id = await start_turn_timer(chat_id, next_player.user_id, game.turn_timer)
                game.timer_task_id = task_id
                save_game(chat_id, game)
                
                text_msg = get_game_status_text(game, user["id"])
                await edit_message_text(chat_id, message_id, text_msg, get_main_menu_keyboard(game, user["id"]))
                await answer_callback_query(cq_id, "⏭️ نوبت رد شد")
            
            # ============================================
            # JOIN
            # ============================================
            elif data == "join":
                game = get_game(chat_id)
                if not game or game.state != "LOBBY":
                    await answer_callback_query(cq_id, "❌ بازی در حال عضوگیری نیست!", True)
                    return {"status": "ok"}
                
                user_id_str = str(user["id"])
                if user_id_str in game.players:
                    await answer_callback_query(cq_id, "⚠️ شما قبلاً وارد شدید!", True)
                else:
                    game.players[user_id_str] = Player(
                        user_id=user["id"],
                        name=user.get("first_name", "ناشناس")
                    )
                    save_game(chat_id, game)
                    await answer_callback_query(cq_id, "✅ وارد بازی شدید!")
                    
                    players_list = "\n".join([f"{i+1}. {p.name}" for i, p in enumerate(game.players.values())])
                    text_msg = f"🎭 بازی جدید ایجاد شد!\n\n👥 بازیکنان:\n{players_list}\n\nحداقل ۲ نفر برای شروع.\n⏱ نوبت: {game.turn_timer}s | ⚠️ چالش: {game.challenge_timer}s"
                    reply_markup = {
                        "inline_keyboard": [
                            [{"text": "🎮 ورود به بازی", "callback_data": "join"}],
                            [{"text": "⚙️ تنظیمات", "callback_data": "settings"}],
                            [{"text": "▶️ شروع بازی", "callback_data": "start"}]
                        ]
                    }
                    await edit_message_text(chat_id, message_id, text_msg, reply_markup)
            
            # ============================================
            # SETTINGS (بدون تغییر)
            # ============================================
            elif data == "settings":
                game = get_game(chat_id)
                if not game or game.state != "LOBBY":
                    await answer_callback_query(cq_id, "❌ فقط قبل از شروع!", True)
                    return {"status": "ok"}
                
                text_msg = f"⚙️ تنظیمات:\n\n⏱ نوبت: {game.turn_timer}s\n⚠️ چالش: {game.challenge_timer}s"
                reply_markup = {
                    "inline_keyboard": [
                        [{"text": "⏱ نوبت: ۳۰s", "callback_data": "set_turn_30"},
                         {"text": "۶۰s", "callback_data": "set_turn_60"},
                         {"text": "۹۰s", "callback_data": "set_turn_90"}],
                        [{"text": "⚠️ چالش: ۱۵s", "callback_data": "set_challenge_15"},
                         {"text": "۳۰s", "callback_data": "set_challenge_30"},
                         {"text": "۴۵s", "callback_data": "set_challenge_45"}],
                        [{"text": "🔙 بازگشت", "callback_data": "back_to_lobby"}]
                    ]
                }
                await edit_message_text(chat_id, message_id, text_msg, reply_markup)
                await answer_callback_query(cq_id, "⚙️ تنظیمات")
            
            elif data.startswith("set_turn_"):
                game = get_game(chat_id)
                if not game or game.state != "LOBBY":
                    await answer_callback_query(cq_id, "❌ قابل تغییر نیست!", True)
                    return {"status": "ok"}
                seconds = int(data.replace("set_turn_", ""))
                game.turn_timer = seconds
                save_game(chat_id, game)
                await answer_callback_query(cq_id, f"✅ نوبت: {seconds}s")
            
            elif data.startswith("set_challenge_"):
                game = get_game(chat_id)
                if not game or game.state != "LOBBY":
                    await answer_callback_query(cq_id, "❌ قابل تغییر نیست!", True)
                    return {"status": "ok"}
                seconds = int(data.replace("set_challenge_", ""))
                game.challenge_timer = seconds
                save_game(chat_id, game)
                await answer_callback_query(cq_id, f"✅ چالش: {seconds}s")
            
            elif data == "back_to_lobby":
                game = get_game(chat_id)
                if not game or game.state != "LOBBY":
                    await answer_callback_query(cq_id, "❌ خطا!", True)
                    return {"status": "ok"}
                
                players_list = "\n".join([f"{i+1}. {p.name}" for i, p in enumerate(game.players.values())])
                text_msg = f"🎭 بازی جدید!\n\n👥 بازیکنان:\n{players_list}\n\n⏱ نوبت: {game.turn_timer}s | ⚠️ چالش: {game.challenge_timer}s"
                reply_markup = {
                    "inline_keyboard": [
                        [{"text": "🎮 ورود به بازی", "callback_data": "join"}],
                        [{"text": "⚙️ تنظیمات", "callback_data": "settings"}],
                        [{"text": "▶️ شروع بازی", "callback_data": "start"}]
                    ]
                }
                await edit_message_text(chat_id, message_id, text_msg, reply_markup)
                await answer_callback_query(cq_id, "🔙")
            
            # ============================================
            # START
            # ============================================
            elif data == "start":
                game = get_game(chat_id)
                if not game or game.state != "LOBBY":
                    await answer_callback_query(cq_id, "❌ بازی عضوگیری نیست!", True)
                    return {"status": "ok"}
                
                if not game.can_start():
                    await answer_callback_query(cq_id, f"⚠️ حداقل ۲ بازیکن! ({len(game.players)} نفر)", True)
                    return {"status": "ok"}
                
                game.state = "PLAYING"
                game.deal_cards()
                game.set_player_order()
                save_game(chat_id, game)
                
                current = game.get_current_player()
                players_list = "\n".join([f"{i+1}. {game.players[str(uid)].name}" for i, uid in enumerate(game.player_order)])
                add_game_log(game, "🎮 بازی شروع شد!")
                
                text_msg = f"🎮 بازی شروع شد!\n\n👥 ترتیب:\n{players_list}\n\n🃏 کارت‌ها توزیع شد!\n💰 هر بازیکن ۲ سکه.\n⏱ تایمر: {game.turn_timer}s\n\n🔹 نوبت: {current.name}\n\n📜 🎮 بازی شروع شد!"
                
                await edit_message_text(chat_id, message_id, text_msg, get_main_menu_keyboard(game, user["id"]))
                
                task_id = await start_turn_timer(chat_id, current.user_id, game.turn_timer)
                game.timer_task_id = task_id
                save_game(chat_id, game)
                
                for uid, player in game.players.items():
                    cards_text = " | ".join(player.cards)
                    private_msg = f"🃏 کارت‌های شما: {cards_text}\n💰 سکه‌ها: {player.coins}"
                    await send_message(int(uid), private_msg)
            
            # ============================================
            # INCOME
            # ============================================
            elif data == "action_income":
                game = get_game(chat_id)
                if not game or game.state != "PLAYING":
                    await answer_callback_query(cq_id, "❌ بازی نیست!", True)
                    return {"status": "ok"}
                
                if not is_player_turn(game, user["id"]):
                    await answer_callback_query(cq_id, "⚠️ نوبت شما نیست!", True)
                    return {"status": "ok"}
                
                if game.timer_task_id:
                    cancel_timer.delay(game.timer_task_id)
                
                game.add_coins(user["id"], 1)
                player_name = game.players[str(user["id"])].name
                add_game_log(game, f"💰 {player_name} +۱ سکه درآمد")
                
                game.next_turn()
                save_game(chat_id, game)
                
                winner = game.check_winner()
                if winner:
                    text_msg = f"🏆 {winner.name} برنده شد! 🎉"
                    await edit_message_text(chat_id, message_id, text_msg, None)
                    delete_game(chat_id)
                    return {"status": "ok"}
                
                next_player = game.get_current_player()
                task_id = await start_turn_timer(chat_id, next_player.user_id, game.turn_timer)
                game.timer_task_id = task_id
                save_game(chat_id, game)
                
                text_msg = get_game_status_text(game, user["id"])
                await edit_message_text(chat_id, message_id, text_msg, get_main_menu_keyboard(game, user["id"]))
                await answer_callback_query(cq_id, f"✅ +۱ سکه (مجموع: {game.players[str(user['id'])].coins}💰)")
            
            # ============================================
            # FOREIGN AID (NEW: همه رد کنن → فوری اجرا)
            # ============================================
            elif data == "action_foreign_aid":
                game = get_game(chat_id)
                if not game or game.state != "PLAYING":
                    await answer_callback_query(cq_id, "❌ بازی نیست!", True)
                    return {"status": "ok"}
                
                if not is_player_turn(game, user["id"]):
                    await answer_callback_query(cq_id, "⚠️ نوبت شما نیست!", True)
                    return {"status": "ok"}
                
                if game.timer_task_id:
                    cancel_timer.delay(game.timer_task_id)
                
                game.state = "CHALLENGING"
                game.current_action = "foreign_aid"
                game.actor_id = user["id"]
                save_game(chat_id, game)
                
                task_id = await start_challenge_timer_handler(chat_id, "foreign_aid", user["id"], None, game.challenge_timer)
                game.timer_task_id = task_id
                save_game(chat_id, game)
                
                player_name = game.players[str(user["id"])].name
                text_msg = f"🌐 {player_name} درخواست کمک خارجی (+۲ سکه)\n⚠️ {game.challenge_timer}s فرصت چالش با Duke!\n\n📜 ⏳ منتظر چالش..."
                reply_markup = {
                    "inline_keyboard": [
                        [{"text": "👑 چالش با Duke", "callback_data": f"block_foreign_aid_{user['id']}"}],
                        [{"text": "✅ قبول (همه)", "callback_data": f"accept_all_foreign_aid_{user['id']}"}]
                    ]
                }
                await edit_message_text(chat_id, message_id, text_msg, reply_markup)
                await answer_callback_query(cq_id, f"⏳ {game.challenge_timer}s فرصت...")
            
            # ============================================
            # NEW: قبول همه برای Foreign Aid
            # ============================================
            elif data.startswith("accept_all_foreign_aid_"):
                game = get_game(chat_id)
                if not game or game.state != "CHALLENGING":
                    await answer_callback_query(cq_id, "❌ خطا!", True)
                    return {"status": "ok"}
                
                actor_id = int(data.replace("accept_all_foreign_aid_", ""))
                
                if game.timer_task_id:
                    cancel_timer.delay(game.timer_task_id)
                
                # اجرای فوری
                log_msg = execute_action_immediately(game, "foreign_aid", actor_id, None)
                add_game_log(game, log_msg)
                
                game.state = "PLAYING"
                game.current_action = None
                game.actor_id = None
                game.next_turn()
                save_game(chat_id, game)
                
                next_player = game.get_current_player()
                task_id = await start_turn_timer(chat_id, next_player.user_id, game.turn_timer)
                game.timer_task_id = task_id
                save_game(chat_id, game)
                
                text_msg = get_game_status_text(game, user["id"])
                await edit_message_text(chat_id, message_id, text_msg, get_main_menu_keyboard(game, user["id"]))
                await answer_callback_query(cq_id, "✅ کمک خارجی پذیرفته شد")
            
            # ============================================
            # BLOCK FOREIGN AID
            # ============================================
            elif data.startswith("block_foreign_aid_"):
                game = get_game(chat_id)
                if not game or game.state != "CHALLENGING":
                    await answer_callback_query(cq_id, "❌ خطا!", True)
                    return {"status": "ok"}
                
                actor_id = int(data.replace("block_foreign_aid_", ""))
                
                if user["id"] == actor_id:
                    await answer_callback_query(cq_id, "❌ خودتان را بلاک نکنید!", True)
                    return {"status": "ok"}
                
                if game.timer_task_id:
                    cancel_timer.delay(game.timer_task_id)
                
                game.current_action = "block_foreign_aid"
                game.actor_id = actor_id
                game.target_id = user["id"]
                save_game(chat_id, game)
                
                blocker = game.players[str(user["id"])]
                text_msg = f"🛡️ {blocker.name} با Duke کمک خارجی را بلاک کرد!\n⚠️ {game.challenge_timer}s فرصت چالش Duke!\n\n📜 ⏳ منتظر چالش..."
                reply_markup = {
                    "inline_keyboard": [
                        [{"text": "⚠️ چالش Duke", "callback_data": f"challenge_block_duke_{user['id']}"}],
                        [{"text": "✅ قبول (همه)", "callback_data": f"accept_block_all_{user['id']}"}]
                    ]
                }
                await edit_message_text(chat_id, message_id, text_msg, reply_markup)
                await answer_callback_query(cq_id, "🛡️ بلاک شد!")
            
            # ============================================
            # NEW: قبول همه برای بلاک
            # ============================================
            elif data.startswith("accept_block_all_"):
                game = get_game(chat_id)
                if not game or game.state != "CHALLENGING":
                    await answer_callback_query(cq_id, "❌ خطا!", True)
                    return {"status": "ok"}
                
                if game.timer_task_id:
                    cancel_timer.delay(game.timer_task_id)
                
                blocker_name = game.players[str(game.target_id)].name
                add_game_log(game, f"🛡️ {blocker_name} کمک خارجی را بلاک کرد (قبول همه)")
                
                game.state = "PLAYING"
                game.current_action = None
                game.actor_id = None
                game.target_id = None
                game.next_turn()
                save_game(chat_id, game)
                
                next_player = game.get_current_player()
                task_id = await start_turn_timer(chat_id, next_player.user_id, game.turn_timer)
                game.timer_task_id = task_id
                save_game(chat_id, game)
                
                text_msg = get_game_status_text(game, user["id"])
                await edit_message_text(chat_id, message_id, text_msg, get_main_menu_keyboard(game, user["id"]))
                await answer_callback_query(cq_id, "✅ بلاک پذیرفته شد")
            
            # ============================================
            # CHALLENGE (با لاگ)
            # ============================================
            elif data.startswith("challenge_"):
                game = get_game(chat_id)
                if not game or game.state != "CHALLENGING":
                    await answer_callback_query(cq_id, "❌ بازی در چالش نیست!", True)
                    return {"status": "ok"}
                
                if str(user["id"]) not in game.players:
                    await answer_callback_query(cq_id, "❌ شما در بازی نیستید!", True)
                    return {"status": "ok"}
                
                if not game.players[str(user["id"])].is_alive:
                    await answer_callback_query(cq_id, "❌ حذف شدید!", True)
                    return {"status": "ok"}
                
                if game.timer_task_id:
                    cancel_timer.delay(game.timer_task_id)
                
                # تشخیص هدف چالش
                if data.startswith("challenge_block_"):
                    target_to_check = game.players[str(game.target_id)]
                    character_to_check = "Duke" if "duke" in data else "Captain"
                else:
                    target_to_check = game.players[str(game.actor_id)]
                    action = data.replace("challenge_", "").rsplit("_", 1)[0]
                    character_map = {
                        "duke": "Duke",
                        "assassin": "Assassin",
                        "captain": "Captain",
                        "contessa": "Contessa"
                    }
                    character_to_check = character_map.get(action, "Unknown")
                
                challenger = game.players[str(user["id"])]
                has_character = check_player_has_character(target_to_check, character_to_check)
                
                if has_character:
                    lost_card = challenger.cards.pop() if challenger.cards else "هیچ"
                    challenger.dead_cards.append(lost_card)
                    if not challenger.cards:
                        challenger.is_alive = False
                    
                    new_card = reveal_and_replace_card(game, target_to_check, character_to_check)
                    
                    log_msg = f"❌ چالش ناموفق! {target_to_check.name} {character_to_check} داشت. {challenger.name} کارت {lost_card} را از دست داد"
                    add_game_log(game, log_msg)
                    
                    await answer_callback_query(cq_id, f"❌ چالش ناموفق! {target_to_check.name} {character_to_check} داشت.\nشما: -{lost_card}")
                else:
                    lost_card = target_to_check.cards.pop() if target_to_check.cards else "هیچ"
                    target_to_check.dead_cards.append(lost_card)
                    if not target_to_check.cards:
                        target_to_check.is_alive = False
                    
                    log_msg = f"✅ چالش موفق! {target_to_check.name} بلوف میزد. کارت {lost_card} سوخت"
                    add_game_log(game, log_msg)
                    
                    await answer_callback_query(cq_id, f"✅ چالش موفق! {target_to_check.name} بلوف میزد.\nاو: -{lost_card}")
                
                game.state = "PLAYING"
                game.current_action = None
                game.actor_id = None
                game.target_id = None
                game.next_turn()
                save_game(chat_id, game)
                
                winner = game.check_winner()
                if winner:
                    text_msg = f"🏆 {winner.name} برنده شد! 🎉\n\n📜 {log_msg}"
                    await edit_message_text(chat_id, message_id, text_msg, None)
                    delete_game(chat_id)
                    return {"status": "ok"}
                
                next_player = game.get_current_player()
                task_id = await start_turn_timer(chat_id, next_player.user_id, game.turn_timer)
                game.timer_task_id = task_id
                save_game(chat_id, game)
                
                text_msg = get_game_status_text(game, user["id"])
                await edit_message_text(chat_id, message_id, text_msg, get_main_menu_keyboard(game, user["id"]))
            
            # ============================================
            # COUP
            # ============================================
            elif data == "action_coup":
                game = get_game(chat_id)
                if not game or game.state != "PLAYING":
                    await answer_callback_query(cq_id, "❌ بازی نیست!", True)
                    return {"status": "ok"}
                
                if not is_player_turn(game, user["id"]):
                    await answer_callback_query(cq_id, "⚠️ نوبت شما نیست!", True)
                    return {"status": "ok"}
                
                player = game.players[str(user["id"])]
                if player.coins < 7:
                    await answer_callback_query(cq_id, f"❌ ۷ سکه لازم است! شما: {player.coins}💰", True)
                    return {"status": "ok"}
                
                alive_players = [p for uid, p in game.players.items() if p.is_alive and p.user_id != user["id"]]
                
                if not alive_players:
                    await answer_callback_query(cq_id, "❌ هدفی نیست!", True)
                    return {"status": "ok"}
                
                target_buttons = []
                for p in alive_players:
                    target_buttons.append([{"text": f"💀 {p.name}", "callback_data": f"coup_target_{p.user_id}"}])
                target_buttons.append([{"text": "🔙 انصراف", "callback_data": "action_cancel"}])
                
                await answer_callback_query(cq_id, "🎯 انتخاب هدف:")
                
                text_msg = f"💀 کودتا! (۷ سکه)\n\nانتخاب هدف:"
                reply_markup = {"inline_keyboard": target_buttons}
                await edit_message_text(chat_id, message_id, text_msg, reply_markup)
            
            elif data.startswith("coup_target_"):
                game = get_game(chat_id)
                if not game or game.state != "PLAYING":
                    await answer_callback_query(cq_id, "❌ بازی نیست!", True)
                    return {"status": "ok"}
                
                target_id = int(data.replace("coup_target_", ""))
                
                if not game.remove_coins(user["id"], 7):
                    await answer_callback_query(cq_id, "❌ سکه کافی نیست!", True)
                    return {"status": "ok"}
                
                target = game.players[str(target_id)]
                lost_card = target.cards.pop() if target.cards else "هیچ"
                target.dead_cards.append(lost_card)
                
                if not target.cards:
                    target.is_alive = False
                    log_msg = f"💀 {target.name} با کودتا حذف شد!"
                else:
                    log_msg = f"💀 کودتا! یک کارت {target.name} سوخت ({lost_card})"
                
                add_game_log(game, log_msg)
                
                if game.timer_task_id:
                    cancel_timer.delay(game.timer_task_id)
                
                winner = game.check_winner()
                if winner:
                    game.next_turn()
                    save_game(chat_id, game)
                    text_msg = f"🏆 {winner.name} برنده شد! 🎉\n\n📜 {log_msg}"
                    await edit_message_text(chat_id, message_id, text_msg, None)
                    delete_game(chat_id)
                    return {"status": "ok"}
                
                game.next_turn()
                save_game(chat_id, game)
                
                next_player = game.get_current_player()
                task_id = await start_turn_timer(chat_id, next_player.user_id, game.turn_timer)
                game.timer_task_id = task_id
                save_game(chat_id, game)
                
                text_msg = get_game_status_text(game, user["id"])
                await edit_message_text(chat_id, message_id, text_msg, get_main_menu_keyboard(game, user["id"]))
                await answer_callback_query(cq_id, log_msg)
            
            # ============================================
            # DUKE (مستقیم زیر پیام)
            # ============================================
            elif data == "char_duke":
                game = get_game(chat_id)
                if not game or game.state != "PLAYING":
                    await answer_callback_query(cq_id, "❌ بازی نیست!", True)
                    return {"status": "ok"}
                
                if not is_player_turn(game, user["id"]):
                    await answer_callback_query(cq_id, "⚠️ نوبت شما نیست!", True)
                    return {"status": "ok"}
                
                if game.timer_task_id:
                    cancel_timer.delay(game.timer_task_id)
                
                game.state = "CHALLENGING"
                game.current_action = "duke"
                game.actor_id = user["id"]
                save_game(chat_id, game)
                
                task_id = await start_challenge_timer_handler(chat_id, "duke", user["id"], None, game.challenge_timer)
                game.timer_task_id = task_id
                save_game(chat_id, game)
                
                player_name = game.players[str(user["id"])].name
                text_msg = f"👑 {player_name} ادعای Duke (مالیات ۳ سکه)\n⚠️ {game.challenge_timer}s فرصت چالش!\n\n📜 ⏳ منتظر چالش..."
                reply_markup = {
                    "inline_keyboard": [
                        [{"text": "⚠️ چالش Duke", "callback_data": f"challenge_duke_{user['id']}"}],
                        [{"text": "✅ قبول (همه)", "callback_data": f"accept_all_duke_{user['id']}"}]
                    ]
                }
                await edit_message_text(chat_id, message_id, text_msg, reply_markup)
                await answer_callback_query(cq_id, f"⏳ {game.challenge_timer}s فرصت...")
            
            # NEW: قبول همه برای Duke
            elif data.startswith("accept_all_duke_"):
                game = get_game(chat_id)
                if not game or game.state != "CHALLENGING":
                    await answer_callback_query(cq_id, "❌ خطا!", True)
                    return {"status": "ok"}
                
                if game.timer_task_id:
                    cancel_timer.delay(game.timer_task_id)
                
                actor_id = int(data.replace("accept_all_duke_", ""))
                log_msg = execute_action_immediately(game, "duke", actor_id, None)
                add_game_log(game, log_msg)
                
                game.state = "PLAYING"
                game.current_action = None
                game.actor_id = None
                game.next_turn()
                save_game(chat_id, game)
                
                next_player = game.get_current_player()
                task_id = await start_turn_timer(chat_id, next_player.user_id, game.turn_timer)
                game.timer_task_id = task_id
                save_game(chat_id, game)
                
                text_msg = get_game_status_text(game, user["id"])
                await edit_message_text(chat_id, message_id, text_msg, get_main_menu_keyboard(game, user["id"]))
                await answer_callback_query(cq_id, "✅ مالیات پذیرفته شد")
            
            # ============================================
            # ASSASSIN
            # ============================================
            elif data == "char_assassin":
                game = get_game(chat_id)
                if not game or game.state != "PLAYING":
                    await answer_callback_query(cq_id, "❌ بازی نیست!", True)
                    return {"status": "ok"}
                
                if not is_player_turn(game, user["id"]):
                    await answer_callback_query(cq_id, "⚠️ نوبت شما نیست!", True)
                    return {"status": "ok"}
                
                player = game.players[str(user["id"])]
                if player.coins < 3:
                    await answer_callback_query(cq_id, f"❌ ۳ سکه لازم! شما: {player.coins}💰", True)
                    return {"status": "ok"}
                
                alive_players = [p for uid, p in game.players.items() if p.is_alive and p.user_id != user["id"]]
                
                if not alive_players:
                    await answer_callback_query(cq_id, "❌ هدفی نیست!", True)
                    return {"status": "ok"}
                
                target_buttons = []
                for p in alive_players:
                    target_buttons.append([{"text": f"🗡️ {p.name}", "callback_data": f"assassinate_target_{p.user_id}"}])
                target_buttons.append([{"text": "🔙 انصراف", "callback_data": "action_cancel"}])
                
                await answer_callback_query(cq_id, "🎯 انتخاب هدف:")
                
                text_msg = f"🗡️ ترور! (۳ سکه)\n\nانتخاب هدف:"
                reply_markup = {"inline_keyboard": target_buttons}
                await edit_message_text(chat_id, message_id, text_msg, reply_markup)
            
            elif data.startswith("assassinate_target_"):
                game = get_game(chat_id)
                if not game or game.state != "PLAYING":
                    await answer_callback_query(cq_id, "❌ بازی نیست!", True)
                    return {"status": "ok"}
                
                target_id = int(data.replace("assassinate_target_", ""))
                
                if game.timer_task_id:
                    cancel_timer.delay(game.timer_task_id)
                
                game.state = "CHALLENGING"
                game.current_action = "assassin"
                game.actor_id = user["id"]
                game.target_id = target_id
                save_game(chat_id, game)
                
                task_id = await start_challenge_timer_handler(chat_id, "assassin", user["id"], target_id, game.challenge_timer)
                game.timer_task_id = task_id
                save_game(chat_id, game)
                
                actor_name = game.players[str(user["id"])].name
                target_name = game.players[str(target_id)].name
                text_msg = f"🗡️ {actor_name} میخواهد {target_name} را ترور کند! (۳ سکه)\n⚠️ {game.challenge_timer}s فرصت چالش یا دفاع!\n\n📜 ⏳ منتظر..."
                reply_markup = {
                    "inline_keyboard": [
                        [{"text": "⚠️ چالش Assassin", "callback_data": f"challenge_assassin_{user['id']}"}],
                        [{"text": "🛡️ دفاع Contessa", "callback_data": f"counter_contessa_{target_id}_{user['id']}"}],
                        [{"text": "✅ قبول (همه)", "callback_data": f"accept_all_assassin_{user['id']}_{target_id}"}]
                    ]
                }
                await edit_message_text(chat_id, message_id, text_msg, reply_markup)
                await answer_callback_query(cq_id, f"⏳ {game.challenge_timer}s فرصت...")
            
            # NEW: قبول همه برای Assassin
            elif data.startswith("accept_all_assassin_"):
                game = get_game(chat_id)
                if not game or game.state != "CHALLENGING":
                    await answer_callback_query(cq_id, "❌ خطا!", True)
                    return {"status": "ok"}
                
                if game.timer_task_id:
                    cancel_timer.delay(game.timer_task_id)
                
                parts = data.replace("accept_all_assassin_", "").split("_")
                actor_id = int(parts[0])
                target_id = int(parts[1])
                
                log_msg = execute_action_immediately(game, "assassin", actor_id, target_id)
                add_game_log(game, log_msg)
                
                game.state = "PLAYING"
                game.current_action = None
                game.actor_id = None
                game.target_id = None
                game.next_turn()
                save_game(chat_id, game)
                
                next_player = game.get_current_player()
                task_id = await start_turn_timer(chat_id, next_player.user_id, game.turn_timer)
                game.timer_task_id = task_id
                save_game(chat_id, game)
                
                text_msg = get_game_status_text(game, user["id"])
                await edit_message_text(chat_id, message_id, text_msg, get_main_menu_keyboard(game, user["id"]))
                await answer_callback_query(cq_id, "✅ ترور پذیرفته شد")
            
            # ============================================
            # COUNTER CONTESSA
            # ============================================
            elif data.startswith("counter_contessa_"):
                game = get_game(chat_id)
                if not game or game.state != "CHALLENGING":
                    await answer_callback_query(cq_id, "❌ خطا!", True)
                    return {"status": "ok"}
                
                parts = data.replace("counter_contessa_", "").split("_")
                target_id = int(parts[0])
                assassin_id = int(parts[1])
                
                if user["id"] != target_id:
                    await answer_callback_query(cq_id, "❌ فقط هدف ترور!", True)
                    return {"status": "ok"}
                
                if game.timer_task_id:
                    cancel_timer.delay(game.timer_task_id)
                
                game.current_action = "contessa"
                game.actor_id = assassin_id
                game.target_id = target_id
                save_game(chat_id, game)
                
                defender = game.players[str(target_id)]
                text_msg = f"🛡️ {defender.name} با Contessa ترور را خنثی کرد!\n⚠️ {game.challenge_timer}s فرصت چالش!\n\n📜 ⏳ منتظر چالش..."
                reply_markup = {
                    "inline_keyboard": [
                        [{"text": "⚠️ چالش Contessa", "callback_data": f"challenge_contessa_{target_id}"}],
                        [{"text": "✅ قبول (همه)", "callback_data": f"accept_all_contessa_{target_id}"}]
                    ]
                }
                await edit_message_text(chat_id, message_id, text_msg, reply_markup)
                await answer_callback_query(cq_id, "🛡️ دفاع با Contessa!")
            
            # NEW: قبول همه برای Contessa
            elif data.startswith("accept_all_contessa_"):
                game = get_game(chat_id)
                if not game or game.state != "CHALLENGING":
                    await answer_callback_query(cq_id, "❌ خطا!", True)
                    return {"status": "ok"}
                
                if game.timer_task_id:
                    cancel_timer.delay(game.timer_task_id)
                
                target_id = int(data.replace("accept_all_contessa_", ""))
                defender_name = game.players[str(target_id)].name
                add_game_log(game, f"🛡️ {defender_name} ترور را با Contessa خنثی کرد")
                
                game.state = "PLAYING"
                game.current_action = None
                game.actor_id = None
                game.target_id = None
                game.next_turn()
                save_game(chat_id, game)
                
                next_player = game.get_current_player()
                task_id = await start_turn_timer(chat_id, next_player.user_id, game.turn_timer)
                game.timer_task_id = task_id
                save_game(chat_id, game)
                
                text_msg = get_game_status_text(game, user["id"])
                await edit_message_text(chat_id, message_id, text_msg, get_main_menu_keyboard(game, user["id"]))
                await answer_callback_query(cq_id, "✅ دفاع پذیرفته شد")
            
            # ============================================
            # CAPTAIN
            # ============================================
            elif data == "char_captain":
                game = get_game(chat_id)
                if not game or game.state != "PLAYING":
                    await answer_callback_query(cq_id, "❌ بازی نیست!", True)
                    return {"status": "ok"}
                
                if not is_player_turn(game, user["id"]):
                    await answer_callback_query(cq_id, "⚠️ نوبت شما نیست!", True)
                    return {"status": "ok"}
                
                alive_players = [p for uid, p in game.players.items() if p.is_alive and p.user_id != user["id"] and p.coins > 0]
                
                if not alive_players:
                    await answer_callback_query(cq_id, "❌ هدفی با سکه نیست!", True)
                    return {"status": "ok"}
                
                target_buttons = []
                for p in alive_players:
                    steal_amount = min(2, p.coins)
                    target_buttons.append([{"text": f"🏴‍☠️ {p.name} ({p.coins}💰)", "callback_data": f"steal_target_{p.user_id}"}])
                target_buttons.append([{"text": "🔙 انصراف", "callback_data": "action_cancel"}])
                
                await answer_callback_query(cq_id, "🎯 انتخاب هدف:")
                
                text_msg = f"🏴‍☠️ دزدی! (حداکثر ۲ سکه)\n\nانتخاب هدف:"
                reply_markup = {"inline_keyboard": target_buttons}
                await edit_message_text(chat_id, message_id, text_msg, reply_markup)
            
            elif data.startswith("steal_target_"):
                game = get_game(chat_id)
                if not game or game.state != "PLAYING":
                    await answer_callback_query(cq_id, "❌ بازی نیست!", True)
                    return {"status": "ok"}
                
                target_id = int(data.replace("steal_target_", ""))
                
                if game.timer_task_id:
                    cancel_timer.delay(game.timer_task_id)
                
                game.state = "CHALLENGING"
                game.current_action = "captain"
                game.actor_id = user["id"]
                game.target_id = target_id
                save_game(chat_id, game)
                
                task_id = await start_challenge_timer_handler(chat_id, "captain", user["id"], target_id, game.challenge_timer)
                game.timer_task_id = task_id
                save_game(chat_id, game)
                
                actor_name = game.players[str(user["id"])].name
                target_name = game.players[str(target_id)].name
                text_msg = f"🏴‍☠️ {actor_name} میخواهد از {target_name} دزدی کند! (۲ سکه)\n⚠️ {game.challenge_timer}s فرصت چالش یا دفاع!\n\n📜 ⏳ منتظر..."
                reply_markup = {
                    "inline_keyboard": [
                        [{"text": "⚠️ چالش Captain", "callback_data": f"challenge_captain_{user['id']}"}],
                        [{"text": "🛡️ دفاع (Cap/Amb)", "callback_data": f"counter_steal_{target_id}_{user['id']}"}],
                        [{"text": "✅ قبول (همه)", "callback_data": f"accept_all_captain_{user['id']}_{target_id}"}]
                    ]
                }
                await edit_message_text(chat_id, message_id, text_msg, reply_markup)
                await answer_callback_query(cq_id, f"⏳ {game.challenge_timer}s فرصت...")
            
            # NEW: قبول همه برای Captain
            elif data.startswith("accept_all_captain_"):
                game = get_game(chat_id)
                if not game or game.state != "CHALLENGING":
                    await answer_callback_query(cq_id, "❌ خطا!", True)
                    return {"status": "ok"}
                
                if game.timer_task_id:
                    cancel_timer.delay(game.timer_task_id)
                
                parts = data.replace("accept_all_captain_", "").split("_")
                actor_id = int(parts[0])
                target_id = int(parts[1])
                
                log_msg = execute_action_immediately(game, "captain", actor_id, target_id)
                add_game_log(game, log_msg)
                
                game.state = "PLAYING"
                game.current_action = None
                game.actor_id = None
                game.target_id = None
                game.next_turn()
                save_game(chat_id, game)
                
                next_player = game.get_current_player()
                task_id = await start_turn_timer(chat_id, next_player.user_id, game.turn_timer)
                game.timer_task_id = task_id
                save_game(chat_id, game)
                
                text_msg = get_game_status_text(game, user["id"])
                await edit_message_text(chat_id, message_id, text_msg, get_main_menu_keyboard(game, user["id"]))
                await answer_callback_query(cq_id, "✅ دزدی پذیرفته شد")
            
            # ============================================
            # COUNTER STEAL
            # ============================================
            elif data.startswith("counter_steal_"):
                game = get_game(chat_id)
                if not game or game.state != "CHALLENGING":
                    await answer_callback_query(cq_id, "❌ خطا!", True)
                    return {"status": "ok"}
                
                parts = data.replace("counter_steal_", "").split("_")
                target_id = int(parts[0])
                thief_id = int(parts[1])
                
                if user["id"] != target_id:
                    await answer_callback_query(cq_id, "❌ فقط هدف دزدی!", True)
                    return {"status": "ok"}
                
                if game.timer_task_id:
                    cancel_timer.delay(game.timer_task_id)
                
                game.current_action = "block_steal"
                game.actor_id = thief_id
                game.target_id = target_id
                save_game(chat_id, game)
                
                defender = game.players[str(target_id)]
                text_msg = f"🛡️ {defender.name} با Captain/Ambassador دزدی را بلاک کرد!\n⚠️ {game.challenge_timer}s فرصت چالش!\n\n📜 ⏳ منتظر چالش..."
                reply_markup = {
                    "inline_keyboard": [
                        [{"text": "⚠️ چالش", "callback_data": f"challenge_block_steal_{target_id}"}],
                        [{"text": "✅ قبول (همه)", "callback_data": f"accept_all_steal_block_{target_id}"}]
                    ]
                }
                await edit_message_text(chat_id, message_id, text_msg, reply_markup)
                await answer_callback_query(cq_id, "🛡️ دفاع!")
            
            # NEW: قبول همه برای بلاک دزدی
            elif data.startswith("accept_all_steal_block_"):
                game = get_game(chat_id)
                if not game or game.state != "CHALLENGING":
                    await answer_callback_query(cq_id, "❌ خطا!", True)
                    return {"status": "ok"}
                
                if game.timer_task_id:
                    cancel_timer.delay(game.timer_task_id)
                
                target_id = int(data.replace("accept_all_steal_block_", ""))
                defender_name = game.players[str(target_id)].name
                add_game_log(game, f"🛡️ {defender_name} دزدی را بلاک کرد")
                
                game.state = "PLAYING"
                game.current_action = None
                game.actor_id = None
                game.target_id = None
                game.next_turn()
                save_game(chat_id, game)
                
                next_player = game.get_current_player()
                task_id = await start_turn_timer(chat_id, next_player.user_id, game.turn_timer)
                game.timer_task_id = task_id
                save_game(chat_id, game)
                
                text_msg = get_game_status_text(game, user["id"])
                await edit_message_text(chat_id, message_id, text_msg, get_main_menu_keyboard(game, user["id"]))
                await answer_callback_query(cq_id, "✅ بلاک پذیرفته شد")
            
            # ============================================
            # AMBASSADOR
            # ============================================
            elif data == "char_ambassador":
                game = get_game(chat_id)
                if not game or game.state != "PLAYING":
                    await answer_callback_query(cq_id, "❌ بازی نیست!", True)
                    return {"status": "ok"}
                
                if not is_player_turn(game, user["id"]):
                    await answer_callback_query(cq_id, "⚠️ نوبت شما نیست!", True)
                    return {"status": "ok"}
                
                if game.timer_task_id:
                    cancel_timer.delay(game.timer_task_id)
                
                player = game.players[str(user["id"])]
                old_cards = player.cards.copy()
                
                for card in old_cards:
                    game.deck.append(card)
                
                random.shuffle(game.deck)
                player.cards = [game.deck.pop(), game.deck.pop()]
                
                save_game(chat_id, game)
                
                add_game_log(game, f"🔄 {player.name} کارت‌هایش را تعویض کرد")
                
                cards_text = " | ".join(player.cards)
                private_msg = f"🔄 کارت‌های جدید: {cards_text}\n💰 سکه: {player.coins}\n\nقبلی: {' | '.join(old_cards)}"
                await send_message(int(user["id"]), private_msg)
                
                await answer_callback_query(cq_id, f"🔄 کارت‌ها تعویض شد!")
                
                game.next_turn()
                save_game(chat_id, game)
                
                next_player = game.get_current_player()
                task_id = await start_turn_timer(chat_id, next_player.user_id, game.turn_timer)
                game.timer_task_id = task_id
                save_game(chat_id, game)
                
                text_msg = get_game_status_text(game, user["id"])
                await edit_message_text(chat_id, message_id, text_msg, get_main_menu_keyboard(game, user["id"]))
            
            # ============================================
            # CANCEL
            # ============================================
            elif data == "action_cancel":
                game = get_game(chat_id)
                if not game:
                    await answer_callback_query(cq_id, "❌ خطا!", True)
                    return {"status": "ok"}
                
                if game.state == "CHALLENGING":
                    if game.timer_task_id:
                        cancel_timer.delay(game.timer_task_id)
                    game.state = "PLAYING"
                    game.current_action = None
                    game.actor_id = None
                    game.target_id = None
                    save_game(chat_id, game)
                
                if game.state == "PLAYING":
                    text_msg = get_game_status_text(game, user["id"])
                    await edit_message_text(chat_id, message_id, text_msg, get_main_menu_keyboard(game, user["id"]))
                await answer_callback_query(cq_id, "🔙 بازگشت")
        
        return {"status": "ok"}
    
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return {"status": "error", "message": str(e)}