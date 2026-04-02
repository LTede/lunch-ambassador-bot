import sys
import os
import random
import hashlib
from datetime import datetime, timedelta

from flask import Flask, request, abort
from linebot.v3 import WebhookHandler
from linebot.v3.messaging import (
    Configuration, ApiClient, MessagingApi,
    PushMessageRequest, ReplyMessageRequest, TextMessage,
)
from linebot.v3.webhooks import MessageEvent, TextMessageContent
from linebot.v3.exceptions import InvalidSignatureError
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
import pytz

app = Flask(__name__)

CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET", "")
GROUP_ID = os.environ.get("LINE_GROUP_ID", "")

configuration = Configuration(access_token=CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(CHANNEL_SECRET)

AMBASSADORS = [
    {"name": "박상웅", "category": "한식"},
    {"name": "안재영", "category": "일식"},
    {"name": "박지홍", "category": "중식"},
    {"name": "이태의", "category": "기타(양식, 아시안 등)"},
]


def get_weekly_ambassador(target_date=None):
    kst = pytz.timezone("Asia/Seoul")
    if target_date is None:
        target_date = datetime.now(kst).date() + timedelta(days=1)

    year, week_num, _ = target_date.isocalendar()
    seed_str = f"lunch-ambassador-{year}-W{week_num}"
    seed = int(hashlib.md5(seed_str.encode()).hexdigest(), 16)

    rng = random.Random(seed)
    week_order = list(AMBASSADORS)
    rng.shuffle(week_order)

    weekday = target_date.isoweekday()
    if 1 <= weekday <= 4:
        return week_order[weekday - 1]
    else:
        return rng.choice(week_order)


def get_weekday_kr(target_date=None):
    kst = pytz.timezone("Asia/Seoul")
    if target_date is None:
        target_date = datetime.now(kst).date() + timedelta(days=1)
    days = ["월", "화", "수", "목", "금", "토", "일"]
    return days[target_date.weekday()]

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


def get_week_schedule_message():
    kst = pytz.timezone("Asia/Seoul")
    today = datetime.now(kst).date()
    monday = today - timedelta(days=today.weekday())

    lines = ["📅 이번 주 점심 엠버서더 스케줄", "━━━━━━━━━━━━━━━"]
    day_names = ["월", "화", "수", "목", "금"]
    for i in range(5):
        d = monday + timedelta(days=i)
        chosen = get_weekly_ambassador(target_date=d)
        marker = " 👈 오늘" if d == today else ""
        lines.append(f"  {day_names[i]} ({d.month}/{d.day}) — {chosen['name']} [{chosen['category']}]{marker}")
    lines.append("━━━━━━━━━━━━━━━")
    lines.append("월~목 공평 로테이션 | 금요일 랜덤")
    return "\n".join(lines)

def send_daily_message():
    if not GROUP_ID:
        print("[ERROR] GROUP_ID not set", flush=True)
        return
    chosen = get_weekly_ambassador()
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
            reply_text = (
                f"그룹 ID: {source.group_id}"
                if hasattr(source, "group_id")
                else "이 채팅방은 그룹이 아닙니다."
            )
            messaging_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text=reply_text)],
                )
            )

        elif text == "/점심뽑기":
            chosen = get_weekly_ambassador()
            weekday = get_weekday_kr()
            message = build_message(chosen, weekday)
            messaging_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text=message)],
                )
            )

        elif text == "/이번주":
            message = get_week_schedule_message()
            messaging_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text=message)],
                )
            )

        elif text == "/엠버서더":
            lines = [
                "📋 점심 엠버서더 목록",
                "━━━━━━━━━━━━━━━",
            ]
            for a in AMBASSADORS:
                lines.append(f"• {a['name']} — {a['category']}")
            lines.append("━━━━━━━━━━━━━━━")
            lines.append("월~목 공평 로테이션 | 금요일 랜덤")
            lines.append("매일 오후 5시에 내일의 담당자가 발표됩니다.")
            messaging_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text="\n".join(lines))],
                )
            )


@app.route("/", methods=["GET"])
def health():
    return "Lunch Ambassador Bot is running! 🍽"


scheduler = BackgroundScheduler()
scheduler.add_job(
    send_daily_message,
    CronTrigger(day_of_week="sun,mon,tue,wed,thu", hour=8, minute=0, timezone="UTC"),
    id="daily_lunch_ambassador",
    replace_existing=True,
)
scheduler.start()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
