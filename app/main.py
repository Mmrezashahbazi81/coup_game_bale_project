# app/main.py
from fastapi import FastAPI, Request
import httpx
from app.models import GameState, Player
from app.database import get_game, save_game
from app.config import settings
import logging

# تنظیمات ساده لاگ - فقط یه خط!
logger = logging.getLogger(__name__)
logging.basicConfig(level=getattr(logging, settings.LOG_LEVEL))

app = FastAPI()

# توکن ربات بله
#BOT_TOKEN = "117*******:zULX****"
#API_URL = f"https://tapi.bale.ai/bot{BOT_TOKEN}/"

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

@app.get("/")  # اضافه کردن endpoint تست
async def root():
    return {"status": "Bot is running!"}

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
                
                # ساخت بازی جدید
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
                
                # ارسال پیام لابی
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
                    
                    # آپدیت لیست بازیکنان
                    players_list = "\n".join([f"{i+1}. {p.name}" for i, p in enumerate(game.players.values())])
                    text_msg = f"🎭 بازی جدید ایجاد شد!\n\n👥 بازیکنان:\n{players_list}"
                    reply_markup = {
                        "inline_keyboard": [
                            [{"text": "🎮 ورود به بازی", "callback_data": "join"}],
                            [{"text": "▶️ شروع بازی", "callback_data": "start"}]
                        ]
                    }
                    await edit_message_text(chat_id, message_id, text_msg, reply_markup)
        
        return {"status": "ok"}
    
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return {"status": "error", "message": str(e)}