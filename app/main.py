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

def get_main_menu_keyboard():
    return {
        "inline_keyboard": [
            [{"text": "💰 درآمد (+۱ سکه)", "callback_data": "action_income"}],
            [{"text": "🌐 کمک خارجی (+۲ سکه)", "callback_data": "action_foreign_aid"}],
            [{"text": "💀 کودتا (۷ سکه)", "callback_data": "action_coup"}],
            [{"text": "🎯 اقدامات شخصیت‌ها", "callback_data": "action_character"}]
        ]
    }

def get_game_status_text(game):
    next_player = game.get_current_player()
    players_status = "\n".join([
        f"{'🟢' if game.players[str(uid)].is_alive else '💀'} {game.players[str(uid)].name}: {game.players[str(uid)].coins}💰"
        for uid in game.player_order
    ])
    return f"🎮 بازی در جریان است!\n\n👥 وضعیت:\n{players_status}\n\n🔹 نوبت: {next_player.name}"

# NEW: شروع تایمر نوبت
async def start_turn_timer(chat_id, user_id, turn_timer_duration):
    """شروع تایمر نوبت - اگه بازیکن حرکت نکرد، auto-income"""
    task = turn_timer.apply_async(
        args=[chat_id, user_id],
        countdown=turn_timer_duration
    )
    return task.id

# NEW: شروع تایمر چالش
async def start_challenge_timer_handler(chat_id, action, actor_id, target_id, challenge_timer_duration):
    """شروع تایمر چالش"""
    task = challenge_timer.apply_async(
        args=[chat_id, action, actor_id, target_id],
        countdown=challenge_timer_duration
    )
    return task.id

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
            # دکمه ورود به بازی
            # ============================================
            if data == "join":
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
                    text_msg = f"🎭 بازی جدید ایجاد شد!\n\n👥 بازیکنان:\n{players_list}\n\nحداقل ۲ نفر برای شروع نیاز است.\n⏱ تایمر نوبت: {game.turn_timer}s\n⚠️ تایمر چالش: {game.challenge_timer}s"
                    reply_markup = {
                        "inline_keyboard": [
                            [{"text": "🎮 ورود به بازی", "callback_data": "join"}],
                            [{"text": "⚙️ تنظیمات", "callback_data": "settings"}],
                            [{"text": "▶️ شروع بازی", "callback_data": "start"}]
                        ]
                    }
                    await edit_message_text(chat_id, message_id, text_msg, reply_markup)
            
            # ============================================
            # NEW: منوی تنظیمات
            # ============================================
            elif data == "settings":
                game = get_game(chat_id)
                if not game or game.state != "LOBBY":
                    await answer_callback_query(cq_id, "❌ فقط قبل از شروع بازی میتوانید تنظیمات را تغییر دهید!", True)
                    return {"status": "ok"}
                
                text_msg = f"⚙️ تنظیمات بازی:\n\n⏱ تایمر نوبت: {game.turn_timer} ثانیه\n⚠️ تایمر چالش: {game.challenge_timer} ثانیه\n\nانتخاب کنید:"
                reply_markup = {
                    "inline_keyboard": [
                        [{"text": "⏱ تایمر نوبت: ۳۰s", "callback_data": "set_turn_30"},
                         {"text": "۶۰s", "callback_data": "set_turn_60"},
                         {"text": "۹۰s", "callback_data": "set_turn_90"}],
                        [{"text": "⚠️ تایمر چالش: ۱۵s", "callback_data": "set_challenge_15"},
                         {"text": "۳۰s", "callback_data": "set_challenge_30"},
                         {"text": "۴۵s", "callback_data": "set_challenge_45"}],
                        [{"text": "🔙 بازگشت", "callback_data": "back_to_lobby"}]
                    ]
                }
                await edit_message_text(chat_id, message_id, text_msg, reply_markup)
                await answer_callback_query(cq_id, "⚙️ منوی تنظیمات")
            
            # ============================================
            # NEW: تنظیم تایمر نوبت
            # ============================================
            elif data.startswith("set_turn_"):
                game = get_game(chat_id)
                if not game or game.state != "LOBBY":
                    await answer_callback_query(cq_id, "❌ قابل تغییر نیست!", True)
                    return {"status": "ok"}
                
                seconds = int(data.replace("set_turn_", ""))
                game.turn_timer = seconds
                save_game(chat_id, game)
                await answer_callback_query(cq_id, f"✅ تایمر نوبت روی {seconds} ثانیه تنظیم شد!")
            
            # ============================================
            # NEW: تنظیم تایمر چالش
            # ============================================
            elif data.startswith("set_challenge_"):
                game = get_game(chat_id)
                if not game or game.state != "LOBBY":
                    await answer_callback_query(cq_id, "❌ قابل تغییر نیست!", True)
                    return {"status": "ok"}
                
                seconds = int(data.replace("set_challenge_", ""))
                game.challenge_timer = seconds
                save_game(chat_id, game)
                await answer_callback_query(cq_id, f"✅ تایمر چالش روی {seconds} ثانیه تنظیم شد!")
            
            # ============================================
            # NEW: بازگشت به لابی
            # ============================================
            elif data == "back_to_lobby":
                game = get_game(chat_id)
                if not game or game.state != "LOBBY":
                    await answer_callback_query(cq_id, "❌ خطا!", True)
                    return {"status": "ok"}
                
                players_list = "\n".join([f"{i+1}. {p.name}" for i, p in enumerate(game.players.values())])
                text_msg = f"🎭 بازی جدید ایجاد شد!\n\n👥 بازیکنان:\n{players_list}\n\nحداقل ۲ نفر برای شروع نیاز است.\n⏱ تایمر نوبت: {game.turn_timer}s\n⚠️ تایمر چالش: {game.challenge_timer}s"
                reply_markup = {
                    "inline_keyboard": [
                        [{"text": "🎮 ورود به بازی", "callback_data": "join"}],
                        [{"text": "⚙️ تنظیمات", "callback_data": "settings"}],
                        [{"text": "▶️ شروع بازی", "callback_data": "start"}]
                    ]
                }
                await edit_message_text(chat_id, message_id, text_msg, reply_markup)
                await answer_callback_query(cq_id, "🔙 بازگشت به لابی")
            
            # ============================================
            # دکمه شروع بازی
            # ============================================
            elif data == "start":
                game = get_game(chat_id)
                if not game or game.state != "LOBBY":
                    await answer_callback_query(cq_id, "❌ بازی در حال عضوگیری نیست!", True)
                    return {"status": "ok"}
                
                if not game.can_start():
                    await answer_callback_query(cq_id, f"⚠️ حداقل ۲ بازیکن لازم است! (فعلاً {len(game.players)} نفر)", True)
                    return {"status": "ok"}
                
                game.state = "PLAYING"
                game.deal_cards()
                game.set_player_order()
                save_game(chat_id, game)
                
                current = game.get_current_player()
                players_list = "\n".join([f"{i+1}. {game.players[str(uid)].name}" for i, uid in enumerate(game.player_order)])
                text_msg = f"🎮 بازی شروع شد!\n\n👥 ترتیب بازیکنان:\n{players_list}\n\n🃏 کارت‌ها توزیع شد!\n💰 هر بازیکن ۲ سکه دارد.\n⏱ تایمر نوبت: {game.turn_timer}s\n⚠️ تایمر چالش: {game.challenge_timer}s\n\n🔹 نوبت: {current.name}"
                
                await edit_message_text(chat_id, message_id, text_msg, get_main_menu_keyboard())
                
                # NEW: شروع تایمر نوبت
                task_id = await start_turn_timer(chat_id, current.user_id, game.turn_timer)
                game.timer_task_id = task_id
                save_game(chat_id, game)
                
                for uid, player in game.players.items():
                    cards_text = " | ".join(player.cards)
                    private_msg = f"🃏 کارت‌های شما: {cards_text}\n💰 سکه‌ها: {player.coins}"
                    await send_message(int(uid), private_msg)
            
            # ============================================
            # اکشن درآمد (سریع - تایمر رو کنسل میکنه)
            # ============================================
            elif data == "action_income":
                game = get_game(chat_id)
                if not game or game.state != "PLAYING":
                    await answer_callback_query(cq_id, "❌ بازی در جریان نیست!", True)
                    return {"status": "ok"}
                
                if not is_player_turn(game, user["id"]):
                    await answer_callback_query(cq_id, "⚠️ نوبت شما نیست!", True)
                    return {"status": "ok"}
                
                # NEW: کنسل کردن تایمر نوبت قبلی
                if game.timer_task_id:
                    cancel_timer.delay(game.timer_task_id)
                
                game.add_coins(user["id"], 1)
                current_player = game.players[str(user["id"])]
                
                await answer_callback_query(cq_id, f"✅ ۱ سکه دریافت کردید! (مجموع: {current_player.coins}💰)")
                
                game.next_turn()
                save_game(chat_id, game)
                
                winner = game.check_winner()
                if winner:
                    text_msg = f"🏆 {winner.name} برنده بازی شد! 🎉\n\n🎭 بازی به پایان رسید."
                    await edit_message_text(chat_id, message_id, text_msg, None)
                    delete_game(chat_id)
                    return {"status": "ok"}
                
                # NEW: شروع تایمر نوبت جدید
                next_player = game.get_current_player()
                task_id = await start_turn_timer(chat_id, next_player.user_id, game.turn_timer)
                game.timer_task_id = task_id
                save_game(chat_id, game)
                
                text_msg = get_game_status_text(game)
                await edit_message_text(chat_id, message_id, text_msg, get_main_menu_keyboard())
            
            # ============================================
            # اکشن کمک خارجی (با چالش)
            # ============================================
            elif data == "action_foreign_aid":
                game = get_game(chat_id)
                if not game or game.state != "PLAYING":
                    await answer_callback_query(cq_id, "❌ بازی در جریان نیست!", True)
                    return {"status": "ok"}
                
                if not is_player_turn(game, user["id"]):
                    await answer_callback_query(cq_id, "⚠️ نوبت شما نیست!", True)
                    return {"status": "ok"}
                
                # NEW: کنسل تایمر نوبت
                if game.timer_task_id:
                    cancel_timer.delay(game.timer_task_id)
                
                game.state = "CHALLENGING"
                game.current_action = "foreign_aid"
                game.actor_id = user["id"]
                save_game(chat_id, game)
                
                # NEW: شروع تایمر چالش
                task_id = await start_challenge_timer_handler(chat_id, "foreign_aid", user["id"], None, game.challenge_timer)
                game.timer_task_id = task_id
                save_game(chat_id, game)
                
                text_msg = f"🌐 {game.players[str(user['id'])].name} درخواست کمک خارجی کرد! (+۲ سکه)\n⚠️ {game.challenge_timer} ثانیه فرصت چالش!"
                reply_markup = {
                    "inline_keyboard": [
                        [{"text": "⚠️ چالش!", "callback_data": f"challenge_foreign_aid_{user['id']}"}],
                        [{"text": "⏭️ رد کردن", "callback_data": "pass_challenge"}]
                    ]
                }
                await edit_message_text(chat_id, message_id, text_msg, reply_markup)
                await answer_callback_query(cq_id, f"⏳ {game.challenge_timer} ثانیه فرصت چالش...")
            
            # ============================================
            # NEW: رد کردن چالش (pass)
            # ============================================
            elif data == "pass_challenge":
                game = get_game(chat_id)
                if not game or game.state != "CHALLENGING":
                    await answer_callback_query(cq_id, "❌ بازی در حال چالش نیست!", True)
                    return {"status": "ok"}
                
                # همه میتونن pass کنن - فقط برمیگرده به حالت عادی و تایمر ادامه داره
                await answer_callback_query(cq_id, "⏭️ منتظر پایان تایمر...")
            
            # ============================================
            # بقیه اکشن‌ها مشابه قبلی (کودتا، ترور، دزدی، سفیر)
            # ... (کدهای قبلی بدون تغییر - فقط تایمر نوبت و چالش بهشون اضافه بشه)
            # ============================================
            
            # ============================================
            # اکشن کودتا - انتخاب هدف
            # ============================================
            elif data == "action_coup":
                game = get_game(chat_id)
                if not game or game.state != "PLAYING":
                    await answer_callback_query(cq_id, "❌ بازی در جریان نیست!", True)
                    return {"status": "ok"}
                
                if not is_player_turn(game, user["id"]):
                    await answer_callback_query(cq_id, "⚠️ نوبت شما نیست!", True)
                    return {"status": "ok"}
                
                player = game.players[str(user["id"])]
                if player.coins < 7:
                    await answer_callback_query(cq_id, f"❌ سکه کافی ندارید! (۷ سکه لازم است، شما: {player.coins}💰)", True)
                    return {"status": "ok"}
                
                alive_players = [p for uid, p in game.players.items() if p.is_alive and p.user_id != user["id"]]
                
                if not alive_players:
                    await answer_callback_query(cq_id, "❌ بازیکن زنده‌ای برای کودتا نیست!", True)
                    return {"status": "ok"}
                
                target_buttons = []
                for p in alive_players:
                    target_buttons.append([{"text": f"💀 {p.name}", "callback_data": f"coup_target_{p.user_id}"}])
                target_buttons.append([{"text": "🔙 انصراف", "callback_data": "action_cancel"}])
                
                await answer_callback_query(cq_id, "🎯 بازیکن مورد نظر را انتخاب کنید:")
                
                text_msg = f"💀 کودتا!\n\nانتخاب هدف (۷ سکه هزینه دارد):"
                reply_markup = {"inline_keyboard": target_buttons}
                await edit_message_text(chat_id, message_id, text_msg, reply_markup)
            
            elif data.startswith("coup_target_"):
                game = get_game(chat_id)
                if not game or game.state != "PLAYING":
                    await answer_callback_query(cq_id, "❌ بازی در جریان نیست!", True)
                    return {"status": "ok"}
                
                target_id = int(data.replace("coup_target_", ""))
                
                if not game.remove_coins(user["id"], 7):
                    await answer_callback_query(cq_id, "❌ سکه کافی ندارید!", True)
                    return {"status": "ok"}
                
                target = game.players[str(target_id)]
                lost_card = target.cards.pop() if target.cards else "هیچ"
                target.dead_cards.append(lost_card)
                
                if not target.cards:
                    target.is_alive = False
                    await answer_callback_query(cq_id, f"💀 {target.name} با کودتا حذف شد! (کارت سوخته: {lost_card})")
                else:
                    await answer_callback_query(cq_id, f"💀 یک کارت {target.name} سوزانده شد! ({lost_card})")
                
                # NEW: کنسل تایمر نوبت
                if game.timer_task_id:
                    cancel_timer.delay(game.timer_task_id)
                
                winner = game.check_winner()
                if winner:
                    game.next_turn()
                    save_game(chat_id, game)
                    text_msg = f"🏆 {winner.name} برنده بازی شد! 🎉\n\n🎭 بازی به پایان رسید."
                    await edit_message_text(chat_id, message_id, text_msg, None)
                    delete_game(chat_id)
                    return {"status": "ok"}
                
                game.next_turn()
                save_game(chat_id, game)
                
                next_player = game.get_current_player()
                task_id = await start_turn_timer(chat_id, next_player.user_id, game.turn_timer)
                game.timer_task_id = task_id
                save_game(chat_id, game)
                
                text_msg = get_game_status_text(game)
                await edit_message_text(chat_id, message_id, text_msg, get_main_menu_keyboard())
            
            # ============================================
            # منوی شخصیت‌ها
            # ============================================
            elif data == "action_character":
                game = get_game(chat_id)
                if not game or game.state != "PLAYING":
                    await answer_callback_query(cq_id, "❌ بازی در جریان نیست!", True)
                    return {"status": "ok"}
                
                if not is_player_turn(game, user["id"]):
                    await answer_callback_query(cq_id, "⚠️ نوبت شما نیست!", True)
                    return {"status": "ok"}
                
                text_msg = f"🎯 اقدامات شخصیت‌ها:\n\nیک شخصیت را انتخاب کنید:"
                reply_markup = {
                    "inline_keyboard": [
                        [{"text": "👑 Duke - مالیات (۳ سکه)", "callback_data": "char_duke"}],
                        [{"text": "🗡️ Assassin - ترور (۳ سکه)", "callback_data": "char_assassin"}],
                        [{"text": "🏴‍☠️ Captain - دزدی (۲ سکه)", "callback_data": "char_captain"}],
                        [{"text": "🔄 Ambassador - تعویض کارت", "callback_data": "char_ambassador"}],
                        [{"text": "🔙 بازگشت", "callback_data": "action_cancel"}]
                    ]
                }
                
                await edit_message_text(chat_id, message_id, text_msg, reply_markup)
                await answer_callback_query(cq_id, "🎯 منوی شخصیت‌ها")
            
            # ============================================
            # Duke - با چالش
            # ============================================
            elif data == "char_duke":
                game = get_game(chat_id)
                if not game or game.state != "PLAYING":
                    await answer_callback_query(cq_id, "❌ بازی در جریان نیست!", True)
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
                
                text_msg = f"👑 {game.players[str(user['id'])].name} ادعای Duke کرد! (مالیات ۳ سکه)\n⚠️ {game.challenge_timer} ثانیه فرصت چالش!"
                reply_markup = {
                    "inline_keyboard": [
                        [{"text": "⚠️ چالش!", "callback_data": f"challenge_duke_{user['id']}"}],
                        [{"text": "⏭️ رد کردن", "callback_data": "pass_challenge"}]
                    ]
                }
                await edit_message_text(chat_id, message_id, text_msg, reply_markup)
                await answer_callback_query(cq_id, f"⏳ {game.challenge_timer} ثانیه فرصت چالش...")
            
            # ============================================
            # Ambassador - سریع
            # ============================================
            elif data == "char_ambassador":
                game = get_game(chat_id)
                if not game or game.state != "PLAYING":
                    await answer_callback_query(cq_id, "❌ بازی در جریان نیست!", True)
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
                
                cards_text = " | ".join(player.cards)
                private_msg = f"🔄 کارت‌های جدید شما: {cards_text}\n💰 سکه‌ها: {player.coins}\n\nکارت‌های قبلی: {' | '.join(old_cards)}"
                await send_message(int(user["id"]), private_msg)
                
                await answer_callback_query(cq_id, f"🔄 کارت‌های شما تعویض شد!")
                
                game.next_turn()
                save_game(chat_id, game)
                
                next_player = game.get_current_player()
                task_id = await start_turn_timer(chat_id, next_player.user_id, game.turn_timer)
                game.timer_task_id = task_id
                save_game(chat_id, game)
                
                text_msg = get_game_status_text(game)
                await edit_message_text(chat_id, message_id, text_msg, get_main_menu_keyboard())
            
            # ============================================
            # چالش کردن
            # ============================================
            elif data.startswith("challenge_"):
                game = get_game(chat_id)
                if not game or game.state != "CHALLENGING":
                    await answer_callback_query(cq_id, "❌ بازی در حال چالش نیست!", True)
                    return {"status": "ok"}
                
                if str(user["id"]) not in game.players:
                    await answer_callback_query(cq_id, "❌ شما در این بازی نیستید!", True)
                    return {"status": "ok"}
                
                if user["id"] == game.actor_id:
                    await answer_callback_query(cq_id, "❌ نمیتوانید خودتان را چالش کنید!", True)
                    return {"status": "ok"}
                
                if not game.players[str(user["id"])].is_alive:
                    await answer_callback_query(cq_id, "❌ بازیکنان حذف شده نمیتوانند چالش کنند!", True)
                    return {"status": "ok"}
                
                # کنسل تایمر چالش
                if game.timer_task_id:
                    cancel_timer.delay(game.timer_task_id)
                
                actor = game.players[str(game.actor_id)]
                challenger = game.players[str(user["id"])]
                
                action = data.replace("challenge_", "").rsplit("_", 1)[0]
                
                has_character = False
                if action == "duke" and "Duke" in actor.cards:
                    has_character = True
                elif action == "assassin" and "Assassin" in actor.cards:
                    has_character = True
                elif action == "captain" and "Captain" in actor.cards:
                    has_character = True
                
                if has_character:
                    lost_card = challenger.cards.pop() if challenger.cards else "هیچ"
                    challenger.dead_cards.append(lost_card)
                    if not challenger.cards:
                        challenger.is_alive = False
                    await answer_callback_query(cq_id, f"❌ چالش ناموفق! {actor.name} واقعاً {action} داشت.\nشما یک کارت از دست دادید: {lost_card}")
                else:
                    lost_card = actor.cards.pop() if actor.cards else "هیچ"
                    actor.dead_cards.append(lost_card)
                    if not actor.cards:
                        actor.is_alive = False
                    await answer_callback_query(cq_id, f"✅ چالش موفق! {actor.name} بلوف میزد.\nاو یک کارت از دست داد: {lost_card}")
                
                game.state = "PLAYING"
                game.current_action = None
                game.actor_id = None
                game.next_turn()
                save_game(chat_id, game)
                
                winner = game.check_winner()
                if winner:
                    text_msg = f"🏆 {winner.name} برنده بازی شد! 🎉\n\n🎭 بازی به پایان رسید."
                    await edit_message_text(chat_id, message_id, text_msg, None)
                    delete_game(chat_id)
                    return {"status": "ok"}
                
                next_player = game.get_current_player()
                task_id = await start_turn_timer(chat_id, next_player.user_id, game.turn_timer)
                game.timer_task_id = task_id
                save_game(chat_id, game)
                
                text_msg = get_game_status_text(game)
                await edit_message_text(chat_id, message_id, text_msg, get_main_menu_keyboard())
            
            # ============================================
            # Assassin - انتخاب هدف
            # ============================================
            elif data == "char_assassin":
                game = get_game(chat_id)
                if not game or game.state != "PLAYING":
                    await answer_callback_query(cq_id, "❌ بازی در جریان نیست!", True)
                    return {"status": "ok"}
                
                if not is_player_turn(game, user["id"]):
                    await answer_callback_query(cq_id, "⚠️ نوبت شما نیست!", True)
                    return {"status": "ok"}
                
                player = game.players[str(user["id"])]
                if player.coins < 3:
                    await answer_callback_query(cq_id, f"❌ سکه کافی ندارید! (۳ سکه لازم است، شما: {player.coins}💰)", True)
                    return {"status": "ok"}
                
                alive_players = [p for uid, p in game.players.items() if p.is_alive and p.user_id != user["id"]]
                
                if not alive_players:
                    await answer_callback_query(cq_id, "❌ بازیکن زنده‌ای برای ترور نیست!", True)
                    return {"status": "ok"}
                
                target_buttons = []
                for p in alive_players:
                    target_buttons.append([{"text": f"🗡️ {p.name}", "callback_data": f"assassinate_target_{p.user_id}"}])
                target_buttons.append([{"text": "🔙 انصراف", "callback_data": "action_cancel"}])
                
                await answer_callback_query(cq_id, "🎯 بازیکن مورد نظر را انتخاب کنید:")
                
                text_msg = f"🗡️ ترور!\n\nانتخاب هدف (۳ سکه هزینه دارد):"
                reply_markup = {"inline_keyboard": target_buttons}
                await edit_message_text(chat_id, message_id, text_msg, reply_markup)
            
            elif data.startswith("assassinate_target_"):
                game = get_game(chat_id)
                if not game or game.state != "PLAYING":
                    await answer_callback_query(cq_id, "❌ بازی در جریان نیست!", True)
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
                
                text_msg = f"🗡️ {game.players[str(user['id'])].name} میخواهد {game.players[str(target_id)].name} را ترور کند! (۳ سکه)\n⚠️ {game.challenge_timer} ثانیه فرصت چالش!"
                reply_markup = {
                    "inline_keyboard": [
                        [{"text": "⚠️ چالش!", "callback_data": f"challenge_assassin_{user['id']}"}],
                        [{"text": "⏭️ رد کردن", "callback_data": "pass_challenge"}]
                    ]
                }
                await edit_message_text(chat_id, message_id, text_msg, reply_markup)
                await answer_callback_query(cq_id, f"⏳ {game.challenge_timer} ثانیه فرصت چالش...")
            
            # ============================================
            # Captain - انتخاب هدف
            # ============================================
            elif data == "char_captain":
                game = get_game(chat_id)
                if not game or game.state != "PLAYING":
                    await answer_callback_query(cq_id, "❌ بازی در جریان نیست!", True)
                    return {"status": "ok"}
                
                if not is_player_turn(game, user["id"]):
                    await answer_callback_query(cq_id, "⚠️ نوبت شما نیست!", True)
                    return {"status": "ok"}
                
                alive_players = [p for uid, p in game.players.items() if p.is_alive and p.user_id != user["id"] and p.coins > 0]
                
                if not alive_players:
                    await answer_callback_query(cq_id, "❌ بازیکنی با سکه برای دزدی نیست!", True)
                    return {"status": "ok"}
                
                target_buttons = []
                for p in alive_players:
                    steal_amount = min(2, p.coins)
                    target_buttons.append([{"text": f"🏴‍☠️ {p.name} ({p.coins}💰)", "callback_data": f"steal_target_{p.user_id}"}])
                target_buttons.append([{"text": "🔙 انصراف", "callback_data": "action_cancel"}])
                
                await answer_callback_query(cq_id, "🎯 بازیکن مورد نظر را برای دزدی انتخاب کنید:")
                
                text_msg = f"🏴‍☠️ دزدی!\n\nانتخاب هدف (حداکثر ۲ سکه):"
                reply_markup = {"inline_keyboard": target_buttons}
                await edit_message_text(chat_id, message_id, text_msg, reply_markup)
            
            elif data.startswith("steal_target_"):
                game = get_game(chat_id)
                if not game or game.state != "PLAYING":
                    await answer_callback_query(cq_id, "❌ بازی در جریان نیست!", True)
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
                
                text_msg = f"🏴‍☠️ {game.players[str(user['id'])].name} میخواهد از {game.players[str(target_id)].name} دزدی کند! (۲ سکه)\n⚠️ {game.challenge_timer} ثانیه فرصت چالش!"
                reply_markup = {
                    "inline_keyboard": [
                        [{"text": "⚠️ چالش!", "callback_data": f"challenge_captain_{user['id']}"}],
                        [{"text": "⏭️ رد کردن", "callback_data": "pass_challenge"}]
                    ]
                }
                await edit_message_text(chat_id, message_id, text_msg, reply_markup)
                await answer_callback_query(cq_id, f"⏳ {game.challenge_timer} ثانیه فرصت چالش...")
            
            # ============================================
            # انصراف
            # ============================================
            elif data == "action_cancel":
                game = get_game(chat_id)
                if not game:
                    await answer_callback_query(cq_id, "❌ بازی در جریان نیست!", True)
                    return {"status": "ok"}
                
                if game.state == "CHALLENGING":
                    game.state = "PLAYING"
                    game.current_action = None
                    game.actor_id = None
                    game.target_id = None
                    save_game(chat_id, game)
                
                text_msg = get_game_status_text(game)
                await edit_message_text(chat_id, message_id, text_msg, get_main_menu_keyboard())
                await answer_callback_query(cq_id, "🔙 بازگشت به منوی اصلی")
        
        return {"status": "ok"}
    
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return {"status": "error", "message": str(e)}