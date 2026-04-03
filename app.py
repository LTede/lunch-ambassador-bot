import sys
import os
import random
import hashlib
import threading
import time as _time
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

# ── 미션 & 금요일 스페셜 & 벌칙 풀 ──────────────────
MISSIONS = [
    "팀원 중 한 명이 안 먹어본 메뉴로! 🆕",
    "만원 이하로만! 💰",
    "걸어서 5분 이내! 🚶",
    "오늘은 국물 필수! 🍜",
    "메뉴 3개 후보 올려서 투표받기! 🗳",
    "새로운 가게 개척! 🗺",
    "매운맙 도전! 🌶",
    "포장 금지, 매장만! 🏪",
    "전원 같은 메뉴 주문! 🤝",
    "가성비 끝판왕으로! 👛",
]

FRIDAY_SPECIALS = [
    "꼴등 도착자가 음료 쏨! ☕",
    "엠버서더가 메뉴까지 지정! 🍽",
    "가장 먼저 다 먹는 사람이 다음 주 금요일 면제! ⚡",
    "블라인드 — 가게만 공개, 메뉴는 서프라이즈! 🎁",
    "엠버서더가 고른 곳 싫으면 불평러가 전체 쏨! 💸",
]

OBJECTION_FAIL_PENALTIES = [
    "내일 디저트 사오기 🍰",
    "오늘 점심값 엠버서더 몫까지 계산! 💳",
    "내일 커피 한 잔씩 돌리기 ☕",
    "내일 점시 예약 전화 담당 📞",
    "다음 점심 때 가장 먼저 도착하기! 🏃",
]

# ── 이의제기 상태 (인메모리) ──────────────────────
last_announcement = {
    "timestamp": None,
    "chosen": None,
    "weekday": None,
    "objection_used": False,
}

OBJECTION_WINDOW = 180  # 3분


# ── 핵심 로직 ────────────────────────────────
def get_weekly_ambassador(target_date=None):
    """월~목: 공평 로테이션 / 금: 랜덤 (결정적)"""
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


def get_daily_mission(target_date):
    """날짜 기반 결정적 미션 선택"""
    seed_str = f"mission-{target_date.isoformat()}"
    seed = int(hashlib.md5(seed_str.encode()).hexdigest(), 16)
    return random.Random(seed).choice(MISSIONS)


def get_friday_special(target_date):
    """날짜 기반 결정적 금요일 스페셜"""
    seed_str = f"friday-special-{target_date.isoformat()}"
    seed = int(hashlib.md5(seed_str.encode()).hexdigest(), 16)
    return random.Random(seed).choice(FRIDAY_SPECIALS)


def get_streak(target_date, chosen_name):
    """연속 출전 횟수 계산 (과거 평일을 거심러 올라감)"""
    streak = 0
    check = target_date
    for _ in range(20):
        check -= timedelta(days=1)
        while check.isoweekday() > 5:
            check -= timedelta(days=1)
        prev = get_weekly_ambassador(check)
        if prev["name"] == chosen_name:
            streak += 1
        else:
            break
    return streak


def get_weekday_kr(target_date=None):
    kst = pytz.timezone("Asia/Seoul")
    if target_date is None:
        target_date = datetime.now(kst).date() + timedelta(days=1)
    days = ["월", "화", "수", "목", "금", "토", "일"]
    return days[target_date.weekday()]


# ── 메시지 빌더 ──────────────────────────────
def build_result_message(chosen, weekday, target_date):
    """최종 발표 메시지"""
    streak = get_streak(target_date, chosen["name"])
    is_friday = target_date.isoweekday() == 5

    lines = [
        f"📢 내일({weekday})의 점심 엠버서더 안내",
        "━━━━━━━━━━━━━━━",
        f"🍽 담당: {chosen['name']}",
        f"🏷 카테고리: {chosen['category']}",
    ]

    if streak >= 2:
        lines.append(f"🔥 {streak + 1}연속 출전! 레전드!")
    elif streak == 1:
        lines.append("🔥 2연속 출전!")

    lines.append("━━━━━━━━━━━━━━━")

    mission = get_daily_mission(target_date)
    lines.append(f"🎯 오늘의 미션: {mission}")

    if is_friday:
        special = get_friday_special(target_date)
        lines.append(f"🎉 금요일 스페셜: {special}")

    lines.append("━━━━━━━━━━━━━━━")
    lines.append(f"• {chosen['name']}님이 내일 점심 장소를 정해주세요.")
    lines.append("• 불만 표시 시 → 불평러가 사이드 추가 주문!")
    lines.append("• 3분 이내 /이의 가능 (실패 시 벌칙!)")
    lines.append("• 엠버서더의 선택은 절대적입니다. 🫡")

    return "\n".join(lines)


# ── 룰렛 연출 (별도 스레드) ───────────────────────
def send_roulette_sequence():
    """3단계 룰렛 연출 → 이의제기 상태 업데이트"""
    if not GROUP_ID:
        print("[ERROR] GROUP_ID not set", flush=True)
        return

    kst = pytz.timezone("Asia/Seoul")
    target_date = datetime.now(kst).date() + timedelta(days=1)
    chosen = get_weekly_ambassador(target_date)
    weekday = get_weekday_kr(target_date)

    with ApiClient(configuration) as api_client:
        messaging_api = MessagingApi(api_client)

        # Step 1: 룰렛 시작
        names = " → ".join([a["name"] for a in AMBASSADORS])
        msg1 = f"🎰 점심 엠버서더 룰렛 돌아갑니다...\n\n{names}"
        messaging_api.push_message(
            PushMessageRequest(to=GROUP_ID, messages=[TextMessage(text=msg1)])
        )

        _time.sleep(2)

        # Step 2: 탈락 연출
        others = [a["name"] for a in AMBASSADORS if a["name"] != chosen["name"]]
        random.Random(target_date.isoformat()).shuffle(others)
        msg2 = (
            "🥁 두구두구두구...\n\n"
            f"❌ {others[0]} 탈락!\n"
            f"❌ {others[1]} 탈락!\n\n"
            f"🔥 {chosen['name']} vs {others[2]}..."
        )
        messaging_api.push_message(
            PushMessageRequest(to=GROUP_ID, messages=[TextMessage(text=msg2)])
        )

        _time.sleep(2)

        # Step 3: 최종 결과
        result = build_result_message(chosen, weekday, target_date)
        messaging_api.push_message(
            PushMessageRequest(to=GROUP_ID, messages=[TextMessage(text=result)])
        )

    # 이의제기 상태 갱신
    last_announcement["timestamp"] = datetime.now(kst)
    last_announcement["chosen"] = chosen
    last_announcement["weekday"] = weekday
    last_announcement["objection_used"] = False

    print(f"[OK] Roulette sent - {chosen['name']} ({chosen['category']})", flush=True)


def send_daily_message():
    """스케줄러 콜백 — 별도 스레드로 룰렛 실행"""
    threading.Thread(target=send_roulette_sequence, daemon=True).start()


# ── 주간 스케줄 메시지 ────────────────────────
def get_week_schedule_message(start_monday=None):
    kst = pytz.timezone("Asia/Seoul")
    today = datetime.now(kst).date()
    if start_monday is None:
        start_monday = today - timedelta(days=today.weekday())

    current_monday = today - timedelta(days=today.weekday())
    if start_monday == current_monday:
        title = "📅 이번 주 점심 엠버서더 스케줄"
    else:
        title = "📅 다음 주 점심 엠버서더 스케줄"

    lines = [title, "━━━━━━━━━━━━━━━"]
    day_names = ["월", "화", "수", "목", "금"]
    for i in range(5):
        d = start_monday + timedelta(days=i)
        chosen = get_weekly_ambassador(target_date=d)
        marker = " 👈 오늘" if d == today else ""
        lines.append(
            f"  {day_names[i]} ({d.month}/{d.day})"
            f" — {chosen['name']} [{chosen['category']}]{marker}"
        )
    lines.append("━━━━━━━━━━━━━━━")
    lines.append("월~목 공평 로테이션 | 금요일 랜덤")
    return "\n".join(lines)


# ── Flask 라우트 ──────────────────────────────
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
    kst = pytz.timezone("Asia/Seoul")

    with ApiClient(configuration) as api_client:
        messaging_api = MessagingApi(api_client)

        # ── /그룹아이디 ──
        if text == "/그룹아이디":
            source = event.source
            reply_text = (
                f"그룹 ID: {source.group_id}"
                if hasattr(source, "group_id")
                else "이 채팅k��은 그룹이 아닙니다."
            )
            messaging_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text=reply_text)],
                )
            )

        # ── /점심뽑기 (수동 룰렛) ──
        elif text == "/점쉬뽑기":
            messaging_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text="🎰 룰렛 돌릴니다!")],
                )
            )
            threading.Thread(target=send_roulette_sequence, daemon=True).start()

        # ── /이번주 ──
        elif text == "/이번주":
            message = get_week_schedule_message()
            messaging_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text=message)],
                )
            )

        # ── /다음주 (스포일러 방지) ──
        elif text == "/다음주":
            source = event.source
            if hasattr(source, "group_id"):
                messaging_api.reply_message(
                    ReplyMessageRequest(
                        reply_token=event.reply_token,
                        messages=[
                            TextMessage(
                                text=(
                                    "🔒 스포일러 방지!\n"
                                    "다음 주 스케줄은 DM으로 보내드립니다.\n"
                                    "(봇과 1:1 칌구 추가 필요)"
                                )
                            )
                        ],
                    )
                )
                if hasattr(source, "user_id") and source.user_id:
                    try:
                        next_mon = (
                            datetime.now(kst).date()
                            - timedelta(days=datetime.now(kst).date().weekday())
                            + timedelta(weeks=1)
                        )
                        schedule = get_week_schedule_message(start_monday=next_mon)
                        messaging_api.push_message(
                            PushMessageRequest(
                                to=source.user_id,
                                messages=[
                                    TextMessage(
                                        text=f"🤫 몰래 보여드립니다...\n\n{schedule}"
                                    )
                                ],
                            )
                        )
                    except Exception as e:
                        print(f"[WARN] DM 실패: {e}", flush=True)
            else:
                next_mon = (
                    datetime.now(kst).date()
                    - timedelta(days=datetime.now(kst).date().weekday())
                    + timedelta(weeks=1)
                )
                schedule = get_week_schedule_message(start_monday=next_mon)
                messaging_api.reply_message(
                    ReplyMessageRequest(
                        reply_token=event.reply_token,
                        messages=[TextMessage(text=schedule)],
                    )
                )

        # ── /이의 (이의제기 시스템) ──
        elif text == "/이의":
            now = datetime.now(kst)
            ann = last_announcement

            if ann["timestamp"] is None:
                reply = "❌ 아직 오늘의 발표가 없습니다."
            elif ann["objection_used"]:
                reply = "❌ 이미 이의제기가 사용되었습니다. 1회만 가능!"
            elif (now - ann["timestamp"]).total_seconds() > OBJECTION_WINDOW:
                reply = "⏰ 이의제기 시간이 지났습니다! (발표 후 3분 이내)"
            else:
                ann["objection_used"] = True
                seed_str = f"objection-{now.strftime('%Y%m%d%H%M%S')}"
                coin = random.Random(seed_str).random()

                if coin < 0.5:
                    original = ann["chosen"]
                    candidates = [
                        a for a in AMBASSADORS if a["name"] != original["name"]
                    ]
                    new_chosen = random.Random(seed_str + "-pick").choice(candidates)
                    ann["chosen"] = new_chosen
                    reply = (
                        "🎉 이의제기 성공!!\n\n"
                        f"❌ {original['name']} → ✅ {new_chosen['name']}"
                        f" ({new_chosen['category']})\n\n"
                        f"새 엠버서더 {new_chosen['name']}님,"
                        " 내일 점심 부탁드립니다!\n"
                        "(이의제기 대상자는 재의의 불가)"
                    )
                else:
                    penalty = random.Random(seed_str + "-penalty").choice(
                        OBJECTION_FAIL_PENALTIES
                    )
                    reply = (
                        "💥 이의제기 실패!!\n\n"
                        f"엠버서더는 그대로 {ann['chosen']['name']}님!\n"
                        f"🔨 이의제기자 벌칙: {penalty}"
                    )

            messaging_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text=reply)],
                )
            )

        # ── /엠버서더 (도움말) ──
        elif text == "/엠버서더":
            lines = [
                "📋 점심 엠버서더 시스템",
                "━━━━━━━━━━━━━━━",
            ]
            for a in AMBASSADORS:
                lines.append(f"• {a['name']} — {a['category']}")
            lines.append("━━━━━━━━━━━━━━━")
            lines.append("📌 명령어 목록")
            lines.append("  /이번주 — 이번 주 스케줄")
            lines.append("  /다음주 — 다음 주 스케줄 (DM)")
            lines.append("  /점쉬뽑기 — 수동 룰렛")
            lines.append("  /이의 — 이의제기 (발표 후 3분)")
            lines.append("━━━━━━━━━━━━━━━")
            lines.append("월~목 공평 로테이션 | 금요일 랜덤")
            lines.append("매일 오후 5시 룰렛 발표 🎰")
            messaging_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text="\n".join(lines))],
                )
            )


@app.route("/", methods=["GET"])
def health():
    return "Lunch Ambassador Bot is running! 🍽"


# ── 스케줄러 (일~목 UTC 08:00 = KST 17:00) ──────────
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
