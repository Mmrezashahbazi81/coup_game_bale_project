from celery import Celery
import time

# اتصال به ردیس کانتینر (چون در یک شبکه داکر نیستند و با سوپروایزر ران می‌شوند، روی لوکال‌هاست در دسترس است)
celery_app = Celery(
    "coup_worker",
    broker="redis://127.0.0.1:6379/0",
    backend="redis://127.0.0.1:6379/0"
)

# تنظیمات برای رفع اخطار دپریکیت شدن
celery_app.conf.broker_connection_retry_on_startup = True

# یک تسک نمونه برای تست تایمر (بعدا تایمرهای بازی مثل چالش و ... اینجا پیاده می‌شوند)
@celery_app.task(name="timer_task")
def run_timer(game_id: str, action: str, duration: int):
    print(f"[{game_id}] Timer started for '{action}' ({duration} seconds)...")
    time.sleep(duration)
    # اینجا در آینده کدی می‌نویسیم که بررسی کند آیا بازیکن واکنشی نشان داده یا نه
    # و اگر نه، اکشن دیفالت اجرا شود.
    print(f"[{game_id}] Timer finished for '{action}'!")
    return f"Action {action} on {game_id} completed."
