import json
import os
import smtplib
from datetime import datetime
from email.mime.text import MIMEText
from email.header import Header

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC


# ============================================================
# 基本設定
# ============================================================

STATE_FILE = "state.json"

HOTELS = [
    {
        "name": "東横INNソウル江南",
        "id": "00282"
    },
    {
        "name": "東横INNソウル東大門1",
        "id": "00208"
    },
    {
        "name": "東横INNソウル東大門2",
        "id": "00291"
    },
    {
        "name": "東横INNソウル永登浦",
        "id": "00311"
    }
]


CHECKIN = os.environ["TOYOKO_CHECKIN"]
CHECKOUT = os.environ["TOYOKO_CHECKOUT"]
PEOPLE = os.environ.get("TOYOKO_PEOPLE", "1")
ROOMS = os.environ.get("TOYOKO_ROOMS", "1")
SMOKING = os.environ.get("TOYOKO_SMOKING", "noSmoking")

GMAIL_SENDER = os.environ["GMAIL_SENDER"]
GMAIL_PASSWORD = os.environ["GMAIL_PASSWORD"]
GMAIL_RECEIVER = os.environ["GMAIL_RECEIVER"]


# ============================================================
# URL作成
# ============================================================

def make_url(hotel_id):

    return (
        "https://www.toyoko-inn.com/"
        "search/result/room_plan/"
        f"?hotel={hotel_id}"
        f"&people={PEOPLE}"
        f"&room={ROOMS}"
        f"&smoking={SMOKING}"
        f"&start={CHECKIN}"
        f"&end={CHECKOUT}"
        "&tab=roomType"
        "&sort=recommend"
    )


# ============================================================
# 前回状態
# ============================================================

def load_state():

    if not os.path.exists(STATE_FILE):
        return {}

    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)

    except Exception:
        return {}


def save_state(state):

    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(
            state,
            f,
            ensure_ascii=False,
            indent=4
        )


# ============================================================
# 空室チェック
# ============================================================

def check_hotel(driver, hotel):

    hotel_name = hotel["name"]
    hotel_id = hotel["id"]

    url = make_url(hotel_id)

    print()
    print("=" * 60)
    print(f"ホテル：{hotel_name}")
    print(f"ホテル番号：{hotel_id}")
    print("=" * 60)

    driver.get(url)

    wait = WebDriverWait(driver, 30)

    cards = wait.until(
        EC.presence_of_all_elements_located(
            (
                By.CSS_SELECTOR,
                "div.SearchResultRoomPlanParentCard_card-wrapper__nWL53"
            )
        )
    )

    if not cards:
        raise Exception("部屋情報を取得できませんでした")

    room_status = {}

    for card in cards:

        try:

            title = card.find_element(
                By.CSS_SELECTOR,
                "h2.SearchResultRoomPlanParentCard_title__9u3Zj"
            ).text.strip()

            no_result = card.find_elements(
                By.CSS_SELECTOR,
                "div.SearchResultRoomPlanParentCard_no-result__z__9v"
            )

            if no_result:

                room_status[title] = False

            else:

                plans = card.find_elements(
                    By.CSS_SELECTOR,
                    "div.SearchResultRoomPlanChildCard_card-wrapper__26ENX"
                )

                room_status[title] = len(plans) > 0

        except Exception as error:

            print(f"部屋情報の取得エラー：{error}")

    vacancy = any(room_status.values())

    return vacancy, room_status


# ============================================================
# コンソール表示
# ============================================================

def print_status(hotel, room_status):

    print()

    for room, available in room_status.items():

        mark = "○" if available else "×"

        print(f"{mark}　{room}")

    print()

    available_rooms = [
        room
        for room, available in room_status.items()
        if available
    ]

    if available_rooms:

        print(
            "🟢 空室あり："
            + "、".join(available_rooms)
        )

    else:

        print("🔴 空室なし")


# ============================================================
# メール本文
# ============================================================

def create_mail_body(results, startup=False):

    lines = []

    lines.append("東横INN 空室監視")
    lines.append("実行環境：GitHub Actions")
    lines.append(
        f"確認日時：{datetime.now():%Y-%m-%d %H:%M:%S}"
    )
    lines.append(f"チェックイン：{CHECKIN}")
    lines.append(f"チェックアウト：{CHECKOUT}")
    lines.append("")

    if startup:
        lines.append("【監視開始時の空室状況】")
    else:
        lines.append("【空室が発生しました】")

    lines.append("")

    for hotel, vacancy, room_status in results:

        lines.append(
            f"【{hotel['name']}】"
        )

        for room, available in room_status.items():

            mark = "○" if available else "×"

            lines.append(
                f"{mark}　{room}"
            )

        available_rooms = [
            room
            for room, available in room_status.items()
            if available
        ]

        if available_rooms:

            lines.append(
                "空室あり："
                + "、".join(available_rooms)
            )

        else:

            lines.append("空室なし")

        lines.append("")

    return "\n".join(lines)


# ============================================================
# Gmail送信
# ============================================================

def send_mail(subject, body):

    message = MIMEText(
        body,
        "plain",
        "utf-8"
    )

    message["Subject"] = Header(
        subject,
        "utf-8"
    )

    message["From"] = GMAIL_SENDER
    message["To"] = GMAIL_RECEIVER

    try:

        print()
        print("📧 メールを送信しています...")

        with smtplib.SMTP_SSL(
            "smtp.gmail.com",
            465
        ) as smtp:

            smtp.login(
                GMAIL_SENDER,
                GMAIL_PASSWORD
            )

            smtp.send_message(
                message
            )

        print("✅ メールを送信しました")

    except Exception as error:

        print("❌ メール送信に失敗しました")
        print(error)


# ============================================================
# メイン
# ============================================================

def main():

    print()
    print("=" * 60)
    print("東横INN 空室監視ツール")
    print("=" * 60)

    print()
    print(f"ホテル数：{len(HOTELS)}")
    print(f"宿泊日：{CHECKIN} ～ {CHECKOUT}")
    print(f"人数：{PEOPLE}")
    print(f"部屋数：{ROOMS}")
    print()

    # 前回状態
    previous_state = load_state()

    # state.jsonがなければ初回
    first_run = not os.path.exists(STATE_FILE)

    # --------------------------------------------------------
    # Chrome
    # --------------------------------------------------------

    options = Options()

    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1280,900")

    driver = webdriver.Chrome(
        options=options
    )

    results = []
    changed_results = []

    try:

        # ====================================================
        # ホテルチェック
        # ====================================================

        for hotel in HOTELS:

            try:

                vacancy, room_status = check_hotel(
                    driver,
                    hotel
                )

                print_status(
                    hotel,
                    room_status
                )

                results.append(
                    (
                        hotel,
                        vacancy,
                        room_status
                    )
                )

                hotel_id = hotel["id"]

                # --------------------------------------------
                # 前回状態と比較
                # --------------------------------------------

                if hotel_id in previous_state:

                    previous = previous_state[hotel_id]

                    # 空室なし → 空室あり
                    if (
                        previous is False
                        and vacancy is True
                    ):

                        print(
                            "🟢 空室が発生しました！"
                        )

                        changed_results.append(
                            (
                                hotel,
                                vacancy,
                                room_status
                            )
                        )

                    # 空室あり → 空室なし
                    elif (
                        previous is True
                        and vacancy is False
                    ):

                        print(
                            "🔴 空室がなくなりました。"
                        )

                    else:

                        print(
                            "状態に変化はありません。"
                        )

                else:

                    print(
                        "初回チェック：状態を記録します。"
                    )

                # 現在状態を保存
                previous_state[hotel_id] = vacancy

            except Exception as error:

                print(
                    f"❌ {hotel['name']} "
                    "チェックエラー"
                )

                print(error)

        # ====================================================
        # 状態保存
        # ====================================================

        save_state(previous_state)

        # ====================================================
        # 初回メール
        # ====================================================

        if first_run and results:

            body = create_mail_body(
                results,
                startup=True
            )

            send_mail(
                "【東横INN・GitHub】監視開始時の空室状況",
                body
            )

        # ====================================================
        # 空室発生メール
        # ====================================================

        if changed_results:

            body = create_mail_body(
                changed_results,
                startup=False
            )

            send_mail(
                "【東横INN・GitHub】空室が発生しました！",
                body
            )

    finally:

        driver.quit()

        print()
        print("Chromeを終了しました。")


# ============================================================
# 起動
# ============================================================

if __name__ == "__main__":
    main()
