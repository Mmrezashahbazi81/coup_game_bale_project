# test_webhook.py
import httpx

WEBHOOK_URL = "https://coup-bot-tclub.liara.run/webhook"

# تست GET (ساده)
def test_root():
    with httpx.Client() as client:
        r = client.get("https://coup-bot-tclub.liara.run/")
        print("Root:", r.json())
        return r.json()

# تست webhook (ساده)
def test_webhook():
    payload = {
        "message": {
            "chat": {"id": 123},
            "text": "/newgame",
            "from": {"id": 789, "first_name": "تستر"}
        }
    }
    with httpx.Client() as client:
        r = client.post(WEBHOOK_URL, json=payload)
        print("Webhook Status:", r.status_code)
        print("Webhook Response:", r.json())
        return r.json()

# اجرا
if __name__ == "__main__":
    print("Testing root...")
    test_root()
    print("\nTesting webhook...")
    test_webhook()