import os
import random
from datetime import datetime

from flask import Flask, request, abort
from linebot.v3 import WebhookHandler
from linebot.v3.messaging import (
    Configuration,
    ApiClient,
    MessagingApi,
    PushMessageRequest,
    TextMessage,
)
from linebot.v3.webhooks import MessageEvent, TextMessageContent
from linebot.v3.exceptions import InvalidSignatureError
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
import pytz

app = Flask(__name__)

# ===== 환경변수 설정 =====
CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET", "")
GROUP_ID = os.environ.get("LINE_GROUP_ID", "")

configuration = Configuration(access_token=CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(CHANNEL_SECRET)

# ===== 점심 엠버서더 데이터 =====
AMBASSADORS = [
    {"name": "박상웅", "category": "한식"},
    {"name": "안재영", "category": "일식"},
    {"name": "박지홍", "category": "중식"},
    {"name": "이태의", "category": "기타(양식, 아시안 등)"},
]

# 최근 당첨 기록 (연속 방지용)
last_pick = None


def pick_ambassador():
    """랜덤으로 엠버서더 1명 선정 (직전 당첨자 제외)"""
    global last_pick
    candidates = [a for a in AMBASSADORS if a["name"] != last_pick]
    chosen = random.choice(candidates)
    last_pick = chosen["name"]
    return chosen


def get_weekday_kr():
    """내일 요일 반환"""
    kst = pytz.timezone("Asia/Seoul")
    tomorrow = datetime.now(kst).replace(hour=0, minute=0) + __import__("datetime").timedelta(days=1)
    days = ["월", "화", "수", "목", "금", "토", "일"]
    return days[tomorrow.weekday()]


def send_daily_message():
    """매일 오후 5시(KST) 실행 — 내일의 점심 엠버서더 발표"""
    if not GROUP_ID:
        print("[ERROR] GROUP_ID가 설정되지 않았습니다.")
        return

    chosen = pick_ambassador()
    weekday = get_weekday_kr()

    message = (
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

    with ApiClient(configuration) as api_client:
        messaging_api = MessagingApi(api_client)
        messaging_api.push_message(
            PushMessageRequest(
                to=GROUP_ID,
                messages=[TextMessage(text=message)],
            )
        )
    print(f"[OK] 메시지 전송 완료 — {chosen['name']} ({chosen['category']})")


# ===== Webhook 엔드포인트 =====
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
    """그룹 채팅방 ID 확인용 + 수동 명령어"""
    text = event.message.text.strip()

    with ApiClient(configuration) as api_client:
        messaging_api = MessagingApi(api_client)

        # 그룹 ID 확인 명령어
        if text == "/그룹아이디":
            source = event.source
            if hasattr(source, "group_id"):
                reply_text = f"그룹 ID: {source.group_id}"
            else:
                reply_text = "이 채팅방은 그룹이 아닙니다."

            from linebot.v3.messaging import ReplyMessageRequest
            messaging_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text=reply_text)],
                )
            )

        # 수동 뽑기 명령어
        elif text == "/점심뽑기":
            chosen = pick_ambassador()
            weekday = get_weekday_kr()

            message = (
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

            from linebot.v3.messaging import ReplyMessageRequest
            messaging_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text=message)],
                )
            )

        # 현재 엠버서더 목록 확인
        elif text == "/엠버서더":
            lines = ["📋 점심 엠버서더 목록", "━━━━━━━━━━━━━━━"]
            for a in AMBASSADORS:
                lines.append(f"• {a['name']} — {a['category']}")
            lines.append("━━━━━━━━━━━━━━━")
            lines.append("매일 오후 5시에 내일의 담당자가 랜덤 발표됩니다.")

            from linebot.v3.messaging import ReplyMessageRequest
            messaging_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text="\n".join(lines))],
                )
            )


# ===== 헬스체크 =====
@app.route("/", methods=["GET"])
def health():
    return "Lunch Ambassador Bot is running! 🍽"


# ===== 스케줄러 시작 =====
scheduler = BackgroundScheduler()
scheduler.add_job(
    send_daily_message,
    CronTrigger(
        day_of_week="sun-thu",  # KST 월~금 오후5시 = UTC 08:00 (sun-thu)
        hour=8,
        minute=0,
        timezone="UTC",
    ),
    id="daily_lunch_ambassador",
    replace_existing=True,
)
scheduler.start()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
