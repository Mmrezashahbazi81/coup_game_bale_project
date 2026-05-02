from fastapi import FastAPI, Request
import httpx
from app.models import GameState, Player
from app.database import get_game, save_game, delete_game
from app.config import settings
import logging
import random  # NEW: برای انتخاب تصادفی کارت سفیر

logger = logging.getLogger(__name__)
logging.basicConfig(level=getattr(logging, settings.LOG_LEVEL))

app = FastAPI()

# ============================================
# توابع کمکی
# ============================================

async def send_message(chat_id: int, text: str, reply_markup: dict = None):
    """ارسال پیام به کاربر"""
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
    """ویرایش پیام موجود"""
    try:
        async with httpx.AsyncClient() as client:
            payload = {
                "chat_id": chat_id,
                "message_id": message_id,
                "text": text
            }
            if reply_markup:
                payload["reply_markup"] = reply_markup
            response = await client.post(settings.API_URL + "editMessageText", json=payload)
            logger.info(f"EDIT MSG: {response.status_code}")
            return response
    except Exception as e:
        logger.error(f"Error editing message: {e}")

async def answer_callback_query(callback_query_id: str, text: str, show_alert: bool = False):
    """پاسخ به callback query"""
    try:
        async with httpx.AsyncClient() as client:
            payload = {
                "callback_query_id": callback_query_id,
                "text": text,
                "show_alert": show_alert
            }
            response = await client.post(settings.API_URL + "answerCallbackQuery", json=payload)
            logger.info(f"CALLBACK: {response.status_code}")
            return response
    except Exception as e:
        logger.error(f"Error answering callback: {e}")

def is_player_turn(game, user_id):
    """چک میکنه آیا نوبت این بازیکنه یا نه"""
    if not game or game.state != "PLAYING":
        return False
    current_player = game.get_current_player()
    return current_player.user_id == user_id

def get_main_menu_keyboard():
    """برگردوندن منوی اصلی اکشن‌ها"""
    return {
        "inline_keyboard": [
            [{"text": "💰 درآمد (+۱ سکه)", "callback_data": "action_income"}],
            [{"text": "🌐 کمک خارجی (+۲ سکه)", "callback_data": "action_foreign_aid"}],
            [{"text": "💀 کودتا (۷ سکه)", "callback_data": "action_coup"}],
            [{"text": "🎯 اقدامات شخصیت‌ها", "callback_data": "action_character"}]
        ]
    }

def get_game_status_text(game):
    """ساخت متن وضعیت فعلی بازی"""
    next_player = game.get_current_player()
    players_status = "\n".join([
        f"{'🟢' if game.players[str(uid)].is_alive else '💀'} {game.players[str(uid)].name}: {game.players[str(uid)].coins}💰"
        for uid in game.player_order
    ])
    return f"🎮 بازی در جریان است!\n\n👥 وضعیت:\n{players_status}\n\n🔹 نوبت: {next_player.name}"

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
        
        # هندل کردن پیام‌های متنی
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
                        [{"text": "▶️ شروع بازی", "callback_data": "start"}]
                    ]
                }
                text_msg = f"🎭 بازی جدید ایجاد شد!\n\n👥 بازیکنان:\n1. {user.get('first_name', 'ناشناس')}"
                await send_message(chat_id, text_msg, reply_markup)
                logger.info(f"New game created in chat {chat_id}")
        
        # هندل کردن دکمه‌های شیشه‌ای
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
                    text_msg = f"🎭 بازی جدید ایجاد شد!\n\n👥 بازیکنان:\n{players_list}\n\nحداقل ۳ نفر برای شروع نیاز است."
                    reply_markup = {
                        "inline_keyboard": [
                            [{"text": "🎮 ورود به بازی", "callback_data": "join"}],
                            [{"text": "▶️ شروع بازی", "callback_data": "start"}]
                        ]
                    }
                    await edit_message_text(chat_id, message_id, text_msg, reply_markup)
            
            # ============================================
            # دکمه شروع بازی
            # ============================================
            elif data == "start":
                game = get_game(chat_id)
                if not game or game.state != "LOBBY":
                    await answer_callback_query(cq_id, "❌ بازی در حال عضوگیری نیست!", True)
                    return {"status": "ok"}
                
                if not game.can_start():
                    await answer_callback_query(
                        cq_id, 
                        f"⚠️ حداقل ۳ بازیکن لازم است! (فعلاً {len(game.players)} نفر)", 
                        True
                    )
                    return {"status": "ok"}
                
                game.state = "PLAYING"
                game.deal_cards()
                game.set_player_order()
                save_game(chat_id, game)
                
                current = game.get_current_player()
                players_list = "\n".join([f"{i+1}. {game.players[str(uid)].name}" for i, uid in enumerate(game.player_order)])
                text_msg = f"🎮 بازی شروع شد!\n\n👥 ترتیب بازیکنان:\n{players_list}\n\n🃏 کارت‌ها توزیع شد!\n💰 هر بازیکن ۲ سکه دارد.\n\n🔹 نوبت: {current.name}"
                
                await edit_message_text(chat_id, message_id, text_msg, get_main_menu_keyboard())
                
                for uid, player in game.players.items():
                    cards_text = " | ".join(player.cards)
                    private_msg = f"🃏 کارت‌های شما: {cards_text}\n💰 سکه‌ها: {player.coins}"
                    await send_message(int(uid), private_msg)
            
            # ============================================
            # اکشن درآمد
            # ============================================
            elif data == "action_income":
                game = get_game(chat_id)
                if not game or game.state != "PLAYING":
                    await answer_callback_query(cq_id, "❌ بازی در جریان نیست!", True)
                    return {"status": "ok"}
                
                if not is_player_turn(game, user["id"]):
                    await answer_callback_query(cq_id, "⚠️ نوبت شما نیست!", True)
                    return {"status": "ok"}
                
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
                
                text_msg = get_game_status_text(game)
                await edit_message_text(chat_id, message_id, text_msg, get_main_menu_keyboard())
            
            # ============================================
            # اکشن کمک خارجی
            # ============================================
            elif data == "action_foreign_aid":
                game = get_game(chat_id)
                if not game or game.state != "PLAYING":
                    await answer_callback_query(cq_id, "❌ بازی در جریان نیست!", True)
                    return {"status": "ok"}
                
                if not is_player_turn(game, user["id"]):
                    await answer_callback_query(cq_id, "⚠️ نوبت شما نیست!", True)
                    return {"status": "ok"}
                
                game.add_coins(user["id"], 2)
                current_player = game.players[str(user["id"])]
                
                await answer_callback_query(cq_id, f"✅ ۲ سکه دریافت کردید! (مجموع: {current_player.coins}💰)\n⚠️ این حرکت با Duke قابل چالش است.")
                
                game.next_turn()
                save_game(chat_id, game)
                
                winner = game.check_winner()
                if winner:
                    text_msg = f"🏆 {winner.name} برنده بازی شد! 🎉\n\n🎭 بازی به پایان رسید."
                    await edit_message_text(chat_id, message_id, text_msg, None)
                    delete_game(chat_id)
                    return {"status": "ok"}
                
                text_msg = get_game_status_text(game)
                await edit_message_text(chat_id, message_id, text_msg, get_main_menu_keyboard())
            
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
                
                alive_players = [
                    p for uid, p in game.players.items() 
                    if p.is_alive and p.user_id != user["id"]
                ]
                
                if not alive_players:
                    await answer_callback_query(cq_id, "❌ بازیکن زنده‌ای برای کودتا نیست!", True)
                    return {"status": "ok"}
                
                target_buttons = []
                for p in alive_players:
                    target_buttons.append([{
                        "text": f"💀 {p.name}",
                        "callback_data": f"coup_target_{p.user_id}"
                    }])
                target_buttons.append([{"text": "🔙 انصراف", "callback_data": "action_cancel"}])
                
                await answer_callback_query(cq_id, "🎯 بازیکن مورد نظر را انتخاب کنید:")
                
                text_msg = f"💀 کودتا!\n\nانتخاب هدف (۷ سکه هزینه دارد):"
                reply_markup = {"inline_keyboard": target_buttons}
                
                await edit_message_text(chat_id, message_id, text_msg, reply_markup)
            
            # ============================================
            # اجرای کودتا روی هدف
            # ============================================
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
                
                text_msg = get_game_status_text(game)
                await edit_message_text(chat_id, message_id, text_msg, get_main_menu_keyboard())
            
            # ============================================
            # NEW: اجرای ترور روی هدف (برای Assassin)
            # ============================================
            elif data.startswith("assassinate_target_"):
                game = get_game(chat_id)
                if not game or game.state != "PLAYING":
                    await answer_callback_query(cq_id, "❌ بازی در جریان نیست!", True)
                    return {"status": "ok"}
                
                target_id = int(data.replace("assassinate_target_", ""))
                
                if not game.remove_coins(user["id"], 3):
                    await answer_callback_query(cq_id, "❌ سکه کافی ندارید!", True)
                    return {"status": "ok"}
                
                target = game.players[str(target_id)]
                lost_card = target.cards.pop() if target.cards else "هیچ"
                target.dead_cards.append(lost_card)
                
                if not target.cards:
                    target.is_alive = False
                    await answer_callback_query(cq_id, f"🗡️ {target.name} ترور شد! (کارت سوخته: {lost_card})")
                else:
                    await answer_callback_query(cq_id, f"🗡️ یک کارت {target.name} سوزانده شد! ({lost_card})")
                
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
                
                text_msg = get_game_status_text(game)
                await edit_message_text(chat_id, message_id, text_msg, get_main_menu_keyboard())
            
            # ============================================
            # NEW: اجرای دزدی روی هدف (برای Captain)
            # ============================================
            elif data.startswith("steal_target_"):
                game = get_game(chat_id)
                if not game or game.state != "PLAYING":
                    await answer_callback_query(cq_id, "❌ بازی در جریان نیست!", True)
                    return {"status": "ok"}
                
                target_id = int(data.replace("steal_target_", ""))
                target = game.players[str(target_id)]
                
                # دزدیدن ۲ سکه (یا هرچقدر که هدف داره)
                steal_amount = min(2, target.coins)
                game.remove_coins(target_id, steal_amount)
                game.add_coins(user["id"], steal_amount)
                
                await answer_callback_query(cq_id, f"🏴‍☠️ {steal_amount} سکه از {target.name} دزدیدید!")
                
                game.next_turn()
                save_game(chat_id, game)
                
                winner = game.check_winner()
                if winner:
                    text_msg = f"🏆 {winner.name} برنده بازی شد! 🎉\n\n🎭 بازی به پایان رسید."
                    await edit_message_text(chat_id, message_id, text_msg, None)
                    delete_game(chat_id)
                    return {"status": "ok"}
                
                text_msg = get_game_status_text(game)
                await edit_message_text(chat_id, message_id, text_msg, get_main_menu_keyboard())
            
            # ============================================
            # انصراف از انتخاب هدف
            # ============================================
            elif data == "action_cancel":
                game = get_game(chat_id)
                if not game or game.state != "PLAYING":
                    await answer_callback_query(cq_id, "❌ بازی در جریان نیست!", True)
                    return {"status": "ok"}
                
                text_msg = get_game_status_text(game)
                await edit_message_text(chat_id, message_id, text_msg, get_main_menu_keyboard())
                await answer_callback_query(cq_id, "🔙 بازگشت به منوی اصلی")
            
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
                        [{"text": "🛡️ Contessa - جلوگیری از ترور", "callback_data": "char_contessa"}],
                        [{"text": "🏴‍☠️ Captain - دزدی (۲ سکه)", "callback_data": "char_captain"}],
                        [{"text": "🔄 Ambassador - تعویض کارت", "callback_data": "char_ambassador"}],
                        [{"text": "🔙 بازگشت", "callback_data": "action_cancel"}]
                    ]
                }
                
                await edit_message_text(chat_id, message_id, text_msg, reply_markup)
                await answer_callback_query(cq_id, "🎯 منوی شخصیت‌ها")
            
            # ============================================
            # اکشن Duke - مالیات
            # ============================================
            elif data == "char_duke":
                game = get_game(chat_id)
                if not game or game.state != "PLAYING":
                    await answer_callback_query(cq_id, "❌ بازی در جریان نیست!", True)
                    return {"status": "ok"}
                
                if not is_player_turn(game, user["id"]):
                    await answer_callback_query(cq_id, "⚠️ نوبت شما نیست!", True)
                    return {"status": "ok"}
                
                game.add_coins(user["id"], 3)
                current_player = game.players[str(user["id"])]
                
                await answer_callback_query(cq_id, f"👑 ۳ سکه مالیات گرفتید! (مجموع: {current_player.coins}💰)\n⚠️ قابل چالش!")
                
                game.next_turn()
                save_game(chat_id, game)
                
                winner = game.check_winner()
                if winner:
                    text_msg = f"🏆 {winner.name} برنده بازی شد! 🎉\n\n🎭 بازی به پایان رسید."
                    await edit_message_text(chat_id, message_id, text_msg, None)
                    delete_game(chat_id)
                    return {"status": "ok"}
                
                text_msg = get_game_status_text(game)
                await edit_message_text(chat_id, message_id, text_msg, get_main_menu_keyboard())
            
            # ============================================
            # NEW: اکشن Assassin - انتخاب هدف ترور
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
                
                alive_players = [
                    p for uid, p in game.players.items() 
                    if p.is_alive and p.user_id != user["id"]
                ]
                
                if not alive_players:
                    await answer_callback_query(cq_id, "❌ بازیکن زنده‌ای برای ترور نیست!", True)
                    return {"status": "ok"}
                
                target_buttons = []
                for p in alive_players:
                    target_buttons.append([{
                        "text": f"🗡️ {p.name}",
                        "callback_data": f"assassinate_target_{p.user_id}"
                    }])
                target_buttons.append([{"text": "🔙 انصراف", "callback_data": "action_cancel"}])
                
                await answer_callback_query(cq_id, "🎯 بازیکن مورد نظر را انتخاب کنید:")
                
                text_msg = f"🗡️ ترور!\n\nانتخاب هدف (۳ سکه هزینه دارد):"
                reply_markup = {"inline_keyboard": target_buttons}
                
                await edit_message_text(chat_id, message_id, text_msg, reply_markup)
            
            # ============================================
            # NEW: Contessa (فعلاً فقط در Response استفاده میشه)
            # ============================================
            elif data == "char_contessa":
                await answer_callback_query(
                    cq_id, 
                    "🛡️ Contessa فقط برای بلاک کردن ترور استفاده میشود.\nمنتظر بمانید تا کسی شما را ترور کند.", 
                    True
                )
            
            # ============================================
            # NEW: اکشن Captain - انتخاب هدف دزدی
            # ============================================
            elif data == "char_captain":
                game = get_game(chat_id)
                if not game or game.state != "PLAYING":
                    await answer_callback_query(cq_id, "❌ بازی در جریان نیست!", True)
                    return {"status": "ok"}
                
                if not is_player_turn(game, user["id"]):
                    await answer_callback_query(cq_id, "⚠️ نوبت شما نیست!", True)
                    return {"status": "ok"}
                
                alive_players = [
                    p for uid, p in game.players.items() 
                    if p.is_alive and p.user_id != user["id"] and p.coins > 0
                ]
                
                if not alive_players:
                    await answer_callback_query(cq_id, "❌ بازیکنی با سکه برای دزدی نیست!", True)
                    return {"status": "ok"}
                
                target_buttons = []
                for p in alive_players:
                    steal_amount = min(2, p.coins)
                    target_buttons.append([{
                        "text": f"🏴‍☠️ {p.name} ({p.coins}💰)",
                        "callback_data": f"steal_target_{p.user_id}"
                    }])
                target_buttons.append([{"text": "🔙 انصراف", "callback_data": "action_cancel"}])
                
                await answer_callback_query(cq_id, "🎯 بازیکن مورد نظر را برای دزدی انتخاب کنید:")
                
                text_msg = f"🏴‍☠️ دزدی!\n\nانتخاب هدف (حداکثر ۲ سکه):"
                reply_markup = {"inline_keyboard": target_buttons}
                
                await edit_message_text(chat_id, message_id, text_msg, reply_markup)
            
            # ============================================
            # NEW: اکشن Ambassador - تعویض کارت
            # ============================================
            elif data == "char_ambassador":
                game = get_game(chat_id)
                if not game or game.state != "PLAYING":
                    await answer_callback_query(cq_id, "❌ بازی در جریان نیست!", True)
                    return {"status": "ok"}
                
                if not is_player_turn(game, user["id"]):
                    await answer_callback_query(cq_id, "⚠️ نوبت شما نیست!", True)
                    return {"status": "ok"}
                
                player = game.players[str(user["id"])]
                
                # NEW: تعویض کارت‌ها - برشتن کارت‌ها به دک، برداشتن ۲ کارت جدید
                old_cards = player.cards.copy()
                
                # برگشت کارت‌های قدیمی به دک
                for card in old_cards:
                    game.deck.append(card)
                
                # برداشتن ۲ کارت جدید
                random.shuffle(game.deck)
                player.cards = [game.deck.pop(), game.deck.pop()]
                
                save_game(chat_id, game)
                
                # NEW: ارسال پیام خصوصی با کارت‌های جدید
                cards_text = " | ".join(player.cards)
                private_msg = f"🔄 کارت‌های جدید شما: {cards_text}\n💰 سکه‌ها: {player.coins}\n\nکارت‌های قبلی: {' | '.join(old_cards)}"
                await send_message(int(user["id"]), private_msg)
                
                await answer_callback_query(cq_id, f"🔄 کارت‌های شما تعویض شد! کارت‌های جدید را در پیام خصوصی ببینید.")
                
                game.next_turn()
                save_game(chat_id, game)
                
                text_msg = get_game_status_text(game)
                await edit_message_text(chat_id, message_id, text_msg, get_main_menu_keyboard())
        
        return {"status": "ok"}
    
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return {"status": "error", "message": str(e)}