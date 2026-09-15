import json
import os
import smtplib
from datetime import datetime, timezone, timedelta
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
TARGETS_FILE = "targets.json"

JST = timezone(timedelta(hours=9))


# ============================================================
# 環境変数
# ============================================================

PEOPLE = os.environ.get("TOYOKO_PEOPLE", "1")
ROOMS = os.environ.get("TOYOKO_ROOMS", "1")
SMOKING = os.environ.get("TOYOKO_SMOKING", "noSmoking")

GMAIL_SENDER = os.environ["GMAIL_SENDER"]
GMAIL_PASSWORD = os.environ["GMAIL_PASSWORD"]
GMAIL_RECEIVER = os.environ["GMAIL_RECEIVER"]


# ============================================================
# 現在時刻
# ============================================================

def now_jst():
    return datetime.now(JST)


# ============================================================
# targets.json 読み込み
# ============================================================

def load_targets():

    if not os.path.exists(TARGETS_FILE):
        raise Exception(
            f"{TARGETS_FILE} が見つかりません"
        )

    try:
        with open(
            TARGETS_FILE,
            "r",
            encoding="utf-8"
        ) as f:

            targets = json.load(f)

    except Exception as error:

        raise Exception(
            f"{TARGETS_FILE} の読み込みに失敗しました：{error}"
        )

    if not isinstance(targets, list) or not targets:
        raise Exception(
            f"{TARGETS_FILE} に監視対象がありません"
        )

    return targets


# ============================================================
# URL作成
# ============================================================

def make_url(target):

    hotel_id = target["hotel_id"]
    checkin = target["checkin"]
    checkout = target["checkout"]

    return (
        "https://www.toyoko-inn.com/"
        "search/result/room_plan/"
        f"?hotel={hotel_id}"
        f"&people={PEOPLE}"
        f"&room={ROOMS}"
        f"&smoking={SMOKING}"
        f"&start={checkin}"
        f"&end={checkout}"
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

        with open(
            STATE_FILE,
            "r",
            encoding="utf-8"
        ) as f:

            return json.load(f)

    except Exception:

        return {}


def save_state(state):

    with open(
        STATE_FILE,
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            state,
            f,
            ensure_ascii=False,
            indent=4
        )


# ============================================================
# 状態保存用キー
# ============================================================

def make_state_key(target):

    return (
        f"{target['hotel_id']}_"
        f"{target['checkin']}_"
        f"{target['checkout']}"
    )


# ============================================================
# 空室チェック
# ============================================================

def check_hotel(driver, target):

    hotel_name = target["hotel_name"]
    hotel_id = target["hotel_id"]
    checkin = target["checkin"]
    checkout = target["checkout"]

    url = make_url(target)

    print()
    print("=" * 60)
    print(f"ホテル：{hotel_name}")
    print(f"ホテル番号：{hotel_id}")
    print(f"宿泊日：{checkin} ～ {checkout}")
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
        raise Exception(
            "部屋情報を取得できませんでした"
        )

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

            print(
                f"部屋情報の取得エラー：{error}"
            )

    vacancy = any(
        room_status.values()
    )

    return vacancy, room_status


# ============================================================
# コンソール表示
# ============================================================

def print_status(target, room_status):

    print()

    for room, available in room_status.items():

        mark = "○" if available else "×"

        print(
            f"{mark}　{room}"
        )

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

def create_mail_body(
    results,
    mail_type
):

    lines = []

    lines.append(
        "東横INN 空室監視"
    )

    lines.append(
        "実行環境：GitHub Actions"
    )

    lines.append(
        f"確認日時：{now_jst():%Y-%m-%d %H:%M:%S}"
    )

    lines.append("")

    if mail_type == "startup":

        lines.append(
            "【監視開始時の空室状況】"
        )

    elif mail_type == "daily":

        lines.append(
            "【毎朝7:00 定期監視】"
        )

    elif mail_type == "vacancy":

        lines.append(
            "【空室が発生しました】"
        )

    lines.append("")

    for target, vacancy, room_status in results:

        lines.append(
            f"【{target['hotel_name']}】"
        )

        lines.append(
            f"宿泊日：{target['checkin']} ～ "
            f"{target['checkout']}"
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

            lines.append(
                "空室なし"
            )

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
        print(
            "📧 メールを送信しています..."
        )

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

        print(
            "✅ メールを送信しました"
        )

        return True

    except Exception as error:

        print(
            "❌ メール送信に失敗しました"
        )

        print(error)

        return False


# ============================================================
# メイン
# ============================================================

def main():

    print()
    print("=" * 60)
    print(
        "東横INN 空室監視ツール"
    )
    print("=" * 60)

    current_time = now_jst()

    print()
    print(
        f"現在時刻：{current_time:%Y-%m-%d %H:%M:%S}"
    )

    # ========================================================
    # targets.json
    # ========================================================

    targets = load_targets()

    print(
        f"監視対象数：{len(targets)}"
    )

    print(
        f"人数：{PEOPLE}"
    )

    print(
        f"部屋数：{ROOMS}"
    )

    print()

    # ========================================================
    # 前回状態
    # ========================================================

    previous_state = load_state()

    first_run = not os.path.exists(
        STATE_FILE
    )

    today = current_time.strftime(
        "%Y-%m-%d"
    )

    # ========================================================
    # Chrome
    # ========================================================

    options = Options()

    options.add_argument(
        "--headless"
    )

    options.add_argument(
        "--no-sandbox"
    )

    options.add_argument(
        "--disable-dev-shm-usage"
    )

    options.add_argument(
        "--disable-gpu"
    )

    options.add_argument(
        "--window-size=1280,900"
    )

    driver = webdriver.Chrome(
        options=options
    )

    results = []
    changed_results = []

    try:

        # ====================================================
        # targets.json の各対象をチェック
        # ====================================================

        for target in targets:

            try:

                vacancy, room_status = check_hotel(
                    driver,
                    target
                )

                print_status(
                    target,
                    room_status
                )

                results.append(
                    (
                        target,
                        vacancy,
                        room_status
                    )
                )

                # --------------------------------------------
                # ホテル＋日付で状態を管理
                # --------------------------------------------

                state_key = make_state_key(
                    target
                )

                print(
                    f"状態キー：{state_key}"
                )

                # --------------------------------------------
                # 前回状態と比較
                # --------------------------------------------

                if state_key in previous_state:

                    previous = previous_state[
                        state_key
                    ]

                    # ========================================
                    # 空室なし → 空室あり
                    # ========================================

                    if (
                        previous is False
                        and vacancy is True
                    ):

                        print(
                            "🟢 空室が発生しました！"
                        )

                        changed_results.append(
                            (
                                target,
                                vacancy,
                                room_status
                            )
                        )

                    # ========================================
                    # 空室あり → 空室なし
                    # ========================================

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

                # --------------------------------------------
                # 現在状態を保存
                # --------------------------------------------

                previous_state[
                    state_key
                ] = vacancy

            except Exception as error:

                print(
                    f"❌ {target['hotel_name']} "
                    f"{target['checkin']}～"
                    f"{target['checkout']} "
                    "チェックエラー"
                )

                print(error)

        # ====================================================
        # 起動時メール
        # ====================================================

        if first_run and results:

            print()
            print(
                "📢 初回起動メールを送信します"
            )

            body = create_mail_body(
                results,
                "startup"
            )

            if send_mail(
                "【東横INN・GitHub】監視開始",
                body
            ):

                previous_state[
                    "startup_mail_sent"
                ] = True

        # ====================================================
        # 毎朝7:00メール
        # ====================================================

        last_daily_mail = previous_state.get(
            "last_daily_mail"
        )

        is_7am = (
            current_time.hour == 7
        )

        if (
            is_7am
            and last_daily_mail != today
            and results
        ):

            print()
            print(
                "📢 毎朝7:00の定期メールを送信します"
            )

            body = create_mail_body(
                results,
                "daily"
            )

            if send_mail(
                "【東横INN・GitHub】毎朝7:00監視確認",
                body
            ):

                previous_state[
                    "last_daily_mail"
                ] = today

        # ====================================================
        # 空室発生メール
        # ====================================================

        if changed_results:

            print()
            print(
                "📢 空室発生メールを送信します"
            )

            body = create_mail_body(
                changed_results,
                "vacancy"
            )

            send_mail(
                "【東横INN・GitHub】空室が発生しました！",
                body
            )

        # ====================================================
        # 状態保存
        # ====================================================

        save_state(
            previous_state
        )

        print()
        print(
            "💾 state.json を保存しました"
        )

    finally:

        driver.quit()

        print()
        print(
            "Chromeを終了しました。"
        )

        print()


# ============================================================
# 起動
# ============================================================

if __name__ == "__main__":
    main()
