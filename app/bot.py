import telebot

# توکن ربات بله خود را اینجا قرار دهید (در آینده بهتر است از متغیر محیطی .env بخوانیم)
TOKEN = "1177639724:zULXwUtgR21Pk-NhYDaG3GBUKImJ_vCdR5Y"

# تغییر آدرس پیش‌فرض تلگرام به پیام‌رسان بله
telebot.apihelper.API_URL = "https://tapi.bale.ai/bot{0}/{1}"

# ساخت نمونه (instance) از ربات
bot = telebot.TeleBot(TOKEN)
