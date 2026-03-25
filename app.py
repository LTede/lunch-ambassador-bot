import sys
import os

# Force all output to stdout for Render logging
sys.stderr = sys.stdout
print("=== APP STARTING ===", flush=True)

try:
    import random
    from datetime import datetime, timedelta

    from flask import Flask, request, abort
    from linebot.v3 import WebhookHandler
    from linebot.v3.messaging import (
        Configuration,
        ApiClient,
        MessagingApi,
        PushMessageRequest,
        ReplyMessageRequest,
        TextMessage,
    )
    from linebot.v3.webhooks import MessageEvent, TextMessageContent
    from linebot.v3.exceptions import InvalidSignatureError
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron import CronTrigger
    import pytz
    print("=== ALL IMPORTS OK ===", flush=True)
except Exception as e:
    print(f"=== IMPORT ERROR: {e} ===", flush=True)
    import traceback
    traceback.print_exc()
    sys.exit(1)

app = Flask(__name__)

CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET", "")
GROUP_ID = os.environ.get("LINE_GROUP_ID", "")

print(f"=== TOKEN set: {bool(CHANNEL_ACCESS_TOKEN)} ===", flush=True)
print(f"=== SECRET set: {bool(CHANNEL_SECRET)} ===", flush=True)

try:
    configuration = Configuration(access_token=CHANNEL_ACCESS_TOKEN)
    handler = WebhookHandler(CHANNEL_SECRET)
    print("=== LINE SDK init OK ===", flush=True)
except Exception as e:
    print(f"=== SDK INIT ERROR: {e} ===", flush=True)
    import traceback
    traceback.print_exc()
    sys.exit(1)

AMBASSADORS = [
    {"name": "박상웅", "category": "한식"},
    {"name": "안재영", "category": "일식"},
    {"name": "박지홍", "category": "중식"},
    {"name": "이태의", "category": "기타(양식, 아시안 등)"},
]

last_pick = None


def pick_ambassador():
    global last_pick
    candidates = [a for a in AMBASSADORS if a["name"] != last_pick]
    chosen = random.choice(candidates)
    last_pick = chosen["name"]
    return chosen


def get_weekday_kr():
    kst = pytz.timezone("Asia/Seoul")
    tomorrow = datetime.now(kst).replace(hour=0, minute=0) + timedelta(days=1)
    days = ["월", "화", "수", "목", "금", "토", "일"]
    return days[tomorrow.weekday()]


def build_message(chosen, weekday):
    return (
        f"📢 내일({weekday})의 점심 엠버서더 안내\n"
        f"━━━━━━━━━━━━━━━\n"
        f"🍽 담당: {chosen['name']}\n"
        f"🏷 카테고리: {chosen['category']}\n"
        f"━━━━━━━━━━━━━━━\n"
        f"\n"
        f"• {chosen['name']}님이 내일 점심 장소를 정해주세요.\n"
        f"• 불만 표시 시 → 불평한 사람이 사이드 추가 주문!\n"
        f"• 엠버서더의 선택은 절대적입니다. 🫡"
    )


def send_daily_message():
    if not GROUP_ID:
        print("[ERROR] GROUP_ID not set", flush=True)
        return
    chosen = pick_ambassador()
    weekday = get_weekday_kr()
    message = build_message(chosen, weekday)
    with ApiClient(configuration) as api_client:
        messaging_api = MessagingApi(api_client)
        messaging_api.push_message(
            PushMessageRequest(to=GROUP_ID, messages=[TextMessage(text=message)])
        )
    print(f"[OK] Sent - {chosen['name']} ({chosen['category']})", flush=True)


@app.route("/callback", methods=["POST"])
def callback():
    signature = request.headers.get("X-Line-Signature", "")
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return "OK"


@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    text = event.message.text.strip()
    with ApiClient(configuration) as api_client:
        messaging_api = MessagingApi(api_client)

        if text == "/그룹아이디":
            source = event.source
            reply_text = f"그룹 ID: {source.group_id}" if hasattr(source, "group_id") else "이 채팅방은 그룹이 아닙니다."
            messaging_api.reply_message(ReplyMessageRequest(reply_token=event.reply_token, messages=[TextMessage(text=reply_text)]))

        elif text == "/점심뽑기":
            chosen = pick_ambassador()
            weekday = get_weekday_kr()
            message = build_message(chosen, weekday)
            messaging_api.reply_message(ReplyMessageRequest(reply_token=event.reply_token, messages=[TextMessage(text=message)]))

        elif text == "/엠버서더":
            lines = ["📋 점심 엠버서더 목록", "━━━━━━━━━━━━━━━"]
            for a in AMBASSADORS:
                lines.append(f"• {a['name']} — {a['category']}")
            lines.append("━━━━━━━━━━━━━━━")
            lines.append("매일 오후 5시에 내일의 담당자가 랜덤 발표됩니다.")
            messaging_api.reply_message(ReplyMessageRequest(reply_token=event.reply_token, messages=[TextMessage(text="\n".join(lines))]))


@app.route("/", methods=["GET"])
def health():
    return "Lunch Ambassador Bot is running! 🍽"


print("=== Starting scheduler ===", flush=True)
scheduler = BackgroundScheduler()
scheduler.add_job(
    send_daily_message,
    CronTrigger(day_of_week="sun-thu", hour=8, minute=0, timezone="UTC"),
    id="daily_lunch_ambassador",
    replace_existing=True,
)
scheduler.start()
print("=== Scheduler started ===", flush=True)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"=== Starting Flask on port {port} ===", flush=True)
    app.run(host="0.0.0.0", port=port)
