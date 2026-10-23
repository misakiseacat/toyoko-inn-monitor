import json
import os
import smtplib
import html
import traceback
from datetime import datetime, timezone, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
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

PEOPLE = os.environ.get("TOYOKO_PEOPLE", "1")
ROOMS = os.environ.get("TOYOKO_ROOMS", "1")
SMOKING = os.environ.get("TOYOKO_SMOKING", "noSmoking")

GMAIL_SENDER = os.environ["GMAIL_SENDER"]
GMAIL_PASSWORD = os.environ["GMAIL_PASSWORD"]
GMAIL_RECEIVER = os.environ["GMAIL_RECEIVER"]

# "1" / "true" のどちらで渡ってきても有効になるようにしておく
# （ワークフロー側の書き方の違いに影響されないようにするため）
DEBUG_HTML = os.environ.get("TOYOKO_DEBUG_HTML", "").strip().lower() in (
    "1",
    "true",
)

JST = timezone(timedelta(hours=9))


# ============================================================
# 現在時刻
# ============================================================

def now_jst():
    return datetime.now(JST)


# ============================================================
# targets.json 読み込み・チェック
# ============================================================

def load_targets():
    if not os.path.exists(TARGETS_FILE):
        raise FileNotFoundError(
            f"{TARGETS_FILE} が見つかりません。"
        )

    with open(TARGETS_FILE, "r", encoding="utf-8") as f:
        targets = json.load(f)

    if not isinstance(targets, list) or not targets:
        raise ValueError(
            "targets.json に監視条件がありません。"
        )

    required = [
        "checkin",
        "checkout",
        "hotel_id",
        "hotel_name"
    ]

    state_keys = set()

    for i, target in enumerate(targets, start=1):

        for key in required:
            if not target.get(key):
                raise ValueError(
                    f"targets.json {i}行目：{key} がありません。"
                )

        try:
            checkin = datetime.strptime(
                target["checkin"], "%Y-%m-%d"
            )
            checkout = datetime.strptime(
                target["checkout"], "%Y-%m-%d"
            )
        except ValueError:
            raise ValueError(
                f"targets.json {i}行目：日付は YYYY-MM-DD 形式にしてください。"
            )

        if checkin >= checkout:
            raise ValueError(
                f"targets.json {i}行目：チェックアウト日が正しくありません。"
            )

        state_key = make_state_key(target)

        if state_key in state_keys:
            raise ValueError(
                f"targets.json に同じ監視条件が重複しています：{state_key}"
            )

        state_keys.add(state_key)

    return targets


# ============================================================
# URL作成
# ============================================================

def make_url(hotel_id, checkin, checkout):

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
# state.json
# ============================================================

def load_state():

    if not os.path.exists(STATE_FILE):
        print("state.json：ありません（初回実行）")
        return {}

    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            state = json.load(f)

        if not isinstance(state, dict):
            raise ValueError("state.json が辞書形式ではありません。")

        print(
            f"state.json：読み込み成功 "
            f"（{len(state)} 件）"
        )

        return state

    except Exception as error:
        # 壊れたstateを黙って初期化しない
        raise RuntimeError(
            f"state.json の読み込みに失敗しました：{error}"
        )


def save_state(state):

    temp_file = STATE_FILE + ".tmp"

    with open(temp_file, "w", encoding="utf-8") as f:
        json.dump(
            state,
            f,
            ensure_ascii=False,
            indent=4
        )

    os.replace(temp_file, STATE_FILE)

    print(
        f"state.json：保存成功 "
        f"（{len(state)} 件）"
    )


def make_state_key(target):

    return (
        f"{target['hotel_id']}_"
        f"{target['checkin']}_"
        f"{target['checkout']}"
    )


# ============================================================
# Seleniumセレクタ
#
# 東横INN側の末尾ハッシュが変わっても対応できるよう、
# class名の前半部分を使う
# ============================================================

PARENT_CARD_SELECTOR = (
    "div[class*='SearchResultRoomPlanParentCard_card-wrapper']"
)

ROOM_TITLE_SELECTOR = (
    "h2[class*='SearchResultRoomPlanParentCard_title']"
)

NO_RESULT_SELECTOR = (
    "div[class*='SearchResultRoomPlanParentCard_no-result']"
)

CHILD_PLAN_SELECTOR = (
    "div[class*='SearchResultRoomPlanChildCard_card-wrapper']"
)


# ============================================================
# カード内コンテンツの描画待ち
#
# 親カードが出現した直後は、内部の「空室なし」表示や
# 「プラン一覧」がまだ非同期（API経由）で描画されていないことがある。
#
# このサイトでは「空室なし」専用の表示が存在しないカードも多く、
# その場合は空室の有無に関わらず最初は「プランなし」に見える。
# 空きがある部屋ほど、プラン・価格情報の取得に時間がかかる
# 傾向があるため、カードごとに個別に待つと
#   ・満室カードの数だけ待ち時間が掛け算で増える
#   ・逆に待ち時間を短くすると、本当に空きがある部屋の
#     データ取得が間に合わず「プランなし」と誤判定する
# というジレンマが生じる。
#
# そのため、カードごとではなく「ページ内のどれか1枚でも
# プラン情報（または空室なし表示）が現れるまで」を
# 1回だけ待つ方式にする。これなら、空きがあるページでは
# 早期に条件が満たされて待機が打ち切られ、全滅（満室）の
# ページでも「1ホテルにつき最大1回分」のタイムアウトで済む。
# ============================================================

def wait_for_any_card_content(driver, card_containers, timeout=8):

    try:
        WebDriverWait(driver, timeout).until(
            lambda d: any(
                container.find_elements(By.CSS_SELECTOR, NO_RESULT_SELECTOR)
                or container.find_elements(By.CSS_SELECTOR, CHILD_PLAN_SELECTOR)
                for container in card_containers
            )
        )
    except Exception:
        # タイムアウトしても致命的エラーにはせず、
        # 呼び出し側の判定ロジック（プランなし＝空室なし）に委ねる。
        # ここで例外を投げると、本当に全滅（満室）だった場合に
        # チェックエラー扱いになってしまうため。
        pass


# ============================================================
# 空室チェック
# ============================================================

def check_hotel(driver, target):

    hotel_name = target["hotel_name"]
    hotel_id = target["hotel_id"]
    checkin = target["checkin"]
    checkout = target["checkout"]

    url = make_url(
        hotel_id,
        checkin,
        checkout
    )

    print()
    print("=" * 70)
    print(f"ホテル：{hotel_name}")
    print(f"ホテルID：{hotel_id}")
    print(f"宿泊日：{checkin} ～ {checkout}")
    print(f"URL：{url}")
    print("=" * 70)

    # 対象ホテルIDとURLのホテルIDが一致しているか確認
    expected = f"hotel={hotel_id}"

    if expected not in url:
        raise RuntimeError(
            f"ホテルIDとURLが一致しません。"
            f" hotel_id={hotel_id}, URL={url}"
        )

    driver.get(url)

    wait = WebDriverWait(driver, 30)

    # ページ読み込み完了を待つ
    wait.until(
        lambda d:
        d.execute_script("return document.readyState")
        == "complete"
    )

    # 部屋カードが出るまで待つ
    cards = wait.until(
        EC.presence_of_all_elements_located(
            (
                By.CSS_SELECTOR,
                PARENT_CARD_SELECTOR
            )
        )
    )

    if not cards:
        raise RuntimeError(
            "部屋カードを1件も取得できませんでした。"
        )

    print(f"部屋タイプカード数：{len(cards)}")

    # 実際のHTMLでは、
    # card-wrapper と child-list（プラン一覧／空室なし表示）が
    # 兄弟要素になっているため、card-wrapper の親要素を取得する。
    card_containers = [
        card.find_element(By.XPATH, "..")
        for card in cards
    ]

    # カードごとではなく、このホテルのページ全体で1回だけ、
    # どれか1つの親コンテナにプラン情報（または空室なし表示）が
    # 現れるまで待つ。
    wait_for_any_card_content(driver, card_containers)

    # ------------------------------------------------------------
    # デバッグ用：実際のHTML構造を確認するための一時的な仕組み。
    #
    # TOYOKO_DEBUG_HTML=1 のときだけ、このホテルのページの
    # HTML全体をファイルに保存する。セレクタが実際のサイト構造と
    # 合っているかどうかを目視確認するための応急措置であり、
    # 恒久的な機能ではない（原因特定後に削除して良い）。
    # ------------------------------------------------------------

    if DEBUG_HTML:

        safe_id = make_state_key(target).replace("/", "-")

        try:
            with open(
                f"debug_{safe_id}.html",
                "w",
                encoding="utf-8"
            ) as f:
                f.write(driver.page_source)

            print(f"デバッグHTML保存：debug_{safe_id}.html")

        except Exception as error:
            print(f"デバッグHTML保存失敗：{error}")

    room_status = {}

    for index, card in enumerate(cards, start=1):

        title_elements = card.find_elements(
            By.CSS_SELECTOR,
            ROOM_TITLE_SELECTOR
        )

        if not title_elements:
            raise RuntimeError(
                f"{index}番目の部屋カードから部屋名を取得できません。"
            )

        title = title_elements[0].text.strip()

        if not title:
            raise RuntimeError(
                f"{index}番目の部屋カードの部屋名が空です。"
            )

        # 実際のHTMLでは、空室なし表示とプラン一覧は
        # card-wrapper の中ではなく、その親コンテナ側にある。
        container = card.find_element(By.XPATH, "..")

        no_result = container.find_elements(
            By.CSS_SELECTOR,
            NO_RESULT_SELECTOR
        )

        if no_result:
            available = False
            reason = "空室なし表示"

        else:
            plans = container.find_elements(
                By.CSS_SELECTOR,
                CHILD_PLAN_SELECTOR
            )

            if plans:
                available = True
                reason = f"プラン{len(plans)}件"
            else:
                # 東横INN側のHTML変更などにより、
                # 「空室なし」専用の要素が存在しない場合がある。
                #
                # プランが存在しない場合は空室なしとして扱う。
                available = False
                reason = "プランなし"

        room_status[title] = available

        mark = "○" if available else "×"

        print(
            f"{mark} {title} "
            f"（{reason}）"
        )

    if not room_status:
        raise RuntimeError(
            "部屋状態を1件も取得できませんでした。"
        )

    vacancy = any(room_status.values())

    print()
    print(
        "判定結果："
        + ("🟢 空室あり" if vacancy else "🔴 空室なし")
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

    available_rooms = [
        room
        for room, available in room_status.items()
        if available
    ]

    if available_rooms:

        print()
        print(
            "🟢 空室あり："
            + "、".join(available_rooms)
        )

    else:

        print()
        print("🔴 空室なし")


# ============================================================
# メール本文
# ============================================================

def create_mail_body(
    results,
    mail_type,
    errors=None,
    history=None
):

    lines = []

    current_time = now_jst()

    lines.append("東横INN 空室監視")
    lines.append("実行環境：GitHub Actions")
    lines.append(
        f"確認日時：{current_time:%Y-%m-%d %H:%M:%S}"
    )
    lines.append(
        f"監視件数：{len(results)} 件"
    )
    lines.append("")

    if mail_type == "startup":
        lines.append("【監視開始時の空室状況】")

    elif mail_type == "daily":
        lines.append("【毎朝7:00 定期監視】")

    elif mail_type == "vacancy":
        lines.append("【空室が発生しました】")

    lines.append("")

    for target, vacancy, room_status in results:

        lines.append(
            f"【{target['hotel_name']}】"
        )

        lines.append(
            f"宿泊日："
            f"{target['checkin']} ～ "
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

            lines.append("空室なし")

        lines.append("")

    if errors:

        lines.append("【チェックエラー】")
        lines.append("")

        for error in errors:
            lines.append(
                f"・{error}"
            )

        lines.append("")

    lines.append(
        "※ ○＝空室あり、×＝空室なし"
    )

    if history:
        lines.append("")
        lines.append("【実行履歴（直近5回）】")
        for item in reversed(history[-5:]):
            lines.append(
                f"{item.get('time', '日時不明')}｜"
                f"{item.get('status', '結果不明')}｜"
                f"成功 {item.get('checked', 0)}件／"
                f"エラー {item.get('errors', 0)}件／"
                f"空室あり {item.get('vacancies', 0)}件"
            )

    return "\n".join(lines)


def create_mail_html(results, mail_type, errors=None, history=None):
    """ホテル名をクリック可能にしたHTMLメール本文を作成する。"""
    current_time = now_jst()
    titles = {
        "startup": "監視開始時の空室状況",
        "daily": "毎朝7:00 定期監視",
        "vacancy": "空室が発生しました",
    }
    parts = [
        "<html><body style='font-family:Arial,sans-serif;color:#222'>",
        "<h2>東横INN 空室監視</h2>",
        "<p>実行環境：GitHub Actions<br>",
        f"確認日時：{current_time:%Y-%m-%d %H:%M:%S}<br>",
        f"監視件数：{len(results)} 件</p>",
        f"<h3>{html.escape(titles.get(mail_type, '監視結果'))}</h3>",
    ]

    for target, vacancy, room_status in results:
        hotel_name = html.escape(str(target["hotel_name"]))
        url = html.escape(
            make_url(target["hotel_id"], target["checkin"], target["checkout"]),
            quote=True,
        )
        parts.append(
            f"<h3><a href=\"{url}\">{hotel_name}</a></h3>"
        )
        parts.append(
            f"<p>宿泊日：{html.escape(str(target['checkin']))} ～ "
            f"{html.escape(str(target['checkout']))}</p>"
        )
        parts.append("<ul>")
        for room, available in room_status.items():
            mark = "○" if available else "×"
            parts.append(
                f"<li>{mark}　{html.escape(str(room))}</li>"
            )
        parts.append("</ul>")
        available_rooms = [
            room for room, available in room_status.items() if available
        ]
        if available_rooms:
            parts.append(
                "<p><strong>空室あり：</strong>"
                + html.escape("、".join(available_rooms))
                + "</p>"
            )
        else:
            parts.append("<p>空室なし</p>")

    if errors:
        parts.append("<h3>チェックエラー</h3><ul>")
        for error in errors:
            parts.append(f"<li>{html.escape(str(error))}</li>")
        parts.append("</ul>")

    parts.append("<p>※ ○＝空室あり、×＝空室なし</p>")

    if history:
        parts.append("<h3>実行履歴（直近5回）</h3><table "
                     "border='1' cellpadding='6' cellspacing='0' "
                     "style='border-collapse:collapse'>")
        parts.append(
            "<tr><th>日時</th><th>結果</th><th>成功</th>"
            "<th>エラー</th><th>空室あり</th></tr>"
        )
        for item in reversed(history[-5:]):
            parts.append(
                "<tr>"
                f"<td>{html.escape(str(item.get('time', '日時不明')))}</td>"
                f"<td>{html.escape(str(item.get('status', '結果不明')))}</td>"
                f"<td>{item.get('checked', 0)}</td>"
                f"<td>{item.get('errors', 0)}</td>"
                f"<td>{item.get('vacancies', 0)}</td>"
                "</tr>"
            )
        parts.append("</table>")

    parts.append("</body></html>")
    return "".join(parts)


# ============================================================
# Gmail送信
# ============================================================

def send_mail(subject, body, html_body=None):

    if html_body:
        message = MIMEMultipart("alternative")
        message.attach(MIMEText(body, "plain", "utf-8"))
        message.attach(MIMEText(html_body, "html", "utf-8"))
    else:
        message = MIMEText(body, "plain", "utf-8")

    message["Subject"] = Header(
        subject,
        "utf-8"
    )

    message["From"] = GMAIL_SENDER
    message["To"] = GMAIL_RECEIVER

    try:

        print()
        print(
            f"📧 メール送信：{subject}"
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
            "✅ メール送信成功"
        )

        return True

    except Exception as error:

        print(
            "❌ メール送信失敗"
        )
        print(
            f"エラー：{error}"
        )

        return False


# ============================================================
# メイン
# ============================================================

def main():

    print()
    print("=" * 70)
    print("東横INN 空室監視ツール")
    print("=" * 70)

    current_time = now_jst()

    print(
        f"現在時刻："
        f"{current_time:%Y-%m-%d %H:%M:%S}"
    )

    print(
        f"人数：{PEOPLE}"
    )

    print(
        f"部屋数：{ROOMS}"
    )

    print(
        f"喫煙条件：{SMOKING}"
    )

    print(
        f"デバッグHTML保存モード："
        f"{DEBUG_HTML} "
        f"（環境変数の生値："
        f"{os.environ.get('TOYOKO_DEBUG_HTML')!r}）"
    )

    # --------------------------------------------------------
    # targets.json
    # --------------------------------------------------------

    targets = load_targets()

    print(
        f"監視件数：{len(targets)}"
    )

    for i, target in enumerate(
        targets,
        start=1
    ):

        print(
            f"{i}. "
            f"{target['hotel_name']} "
            f"{target['checkin']}～"
            f"{target['checkout']}"
        )

    # --------------------------------------------------------
    # state.json
    # --------------------------------------------------------

    previous_state = load_state()

    # 監視対象の状態がまだstate.jsonに存在するか確認
    target_state_keys = {
        make_state_key(target)
        for target in targets
    }

    first_run = not any(
        key in previous_state
        for key in target_state_keys
    )

    if first_run:
        print()
        print(
            "★ state.jsonに監視履歴がありません。"
        )
        print(
            "★ 今回は各条件の現在状態を初期登録します。"
        )

    results = []
    changed_results = []
    errors = []

    # --------------------------------------------------------
    # Chrome
    # --------------------------------------------------------

    options = Options()

    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument(
        "--disable-dev-shm-usage"
    )
    options.add_argument("--disable-gpu")
    options.add_argument(
        "--window-size=1280,900"
    )

    driver = webdriver.Chrome(
        options=options
    )

    try:

        # ====================================================
        # ホテルチェック
        # ====================================================

        for target in targets:

            state_key = make_state_key(
                target
            )

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
                # 前回状態
                # --------------------------------------------

                if state_key in previous_state:

                    previous = previous_state[
                        state_key
                    ]

                    print(
                        f"前回状態："
                        f"{'○' if previous else '×'}"
                    )

                    print(
                        f"今回状態："
                        f"{'○' if vacancy else '×'}"
                    )

                    # × → ○
                    if (
                        previous is False
                        and vacancy is True
                    ):

                        print(
                            "🟢 空室発生を検出しました！"
                        )

                        changed_results.append(
                            (
                                target,
                                vacancy,
                                room_status
                            )
                        )

                    # ○ → ×
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

                    # ------------------------------------------------
                    # この条件（ホテル×日程）は state.json に
                    # 履歴がない＝今回が初めてのチェック。
                    #
                    # 従来は「前回状態がないので通知しない」として
                    # 無条件にスキップしていたが、これだと
                    # 「targets.jsonに新しく条件を追加した時点で
                    #  すでに空室があった」場合に永久に通知されない
                    # バグがあった。
                    #
                    # そのため、初回チェックであっても
                    # 空室ありなら通知対象に含める。
                    # ただし全体の初回起動（first_run、つまり
                    # state.json自体が空の状態からの初回実行）は
                    # 別途「監視開始時の空室状況」メールで
                    # 全件まとめて通知するので、二重送信を避けるため
                    # そちらとは分けて扱う。
                    # ------------------------------------------------

                    print(
                        "初回チェック："
                        "この条件の履歴はまだありません。"
                    )

                    print(
                        f"初回状態："
                        f"{'○' if vacancy else '×'}"
                    )

                    if vacancy and not first_run:

                        print(
                            "🟢 新規追加条件で空室を検出しました！"
                        )

                        changed_results.append(
                            (
                                target,
                                vacancy,
                                room_status
                            )
                        )

                # ------------------------------------------------
                # 現在状態は一旦記録する。
                #
                # ただし「×→○」または「新規条件で空室あり」の場合は、
                # 空室メール送信成功後に確定する。
                # ------------------------------------------------

                pending_notification = (
                    state_key in previous_state
                    and previous_state[state_key] is False
                    and vacancy is True
                ) or (
                    state_key not in previous_state
                    and vacancy is True
                    and not first_run
                )

                if not pending_notification:
                    previous_state[state_key] = vacancy

            except Exception as error:

                # エラー時の画面を保存
                # GitHub ActionsでHTML変更を確認するため
                safe_id = make_state_key(target).replace("/", "-")

                # 失敗時はスクリーンショットだけでなくHTMLも保存する。
                # 部屋カード取得前に失敗すると、従来のDEBUG_HTML保存箇所に
                # 到達しないため、例外処理側で必ず保存を試みる。
                try:
                    screenshot_path = f"error_{safe_id}.png"
                    driver.save_screenshot(screenshot_path)
                    print(f"エラー画面保存：{screenshot_path}")
                except Exception as save_error:
                    print(f"エラー画面の保存失敗：{save_error!r}")

                try:
                    html_path = f"error_{safe_id}.html"
                    with open(html_path, "w", encoding="utf-8") as f:
                        f.write(driver.page_source)
                    print(f"エラー時HTML保存：{html_path}")
                except Exception as save_error:
                    print(f"エラー時HTMLの保存失敗：{save_error!r}")

                # Seleniumの例外は str(error) が空文字になることがあるため、
                # 型名・repr・スタックトレースを出力して原因を特定しやすくする。
                print(f"例外型：{type(error).__name__}")
                print(f"例外詳細：{error!r}")
                traceback.print_exc()

                message = (
                    f"{target['hotel_name']} "
                    f"（{target['checkin']}～"
                    f"{target['checkout']}）："
                    f"{type(error).__name__}: {error!r}"
                )

                print()
                print(
                    "❌ チェックエラー"
                )
                print(
                    message
                )

                errors.append(message)

        # ====================================================
        # チェック結果
        # ====================================================

        print()
        print("=" * 70)
        print("監視結果")
        print("=" * 70)

        print(
            f"成功：{len(results)} 件"
        )

        print(
            f"エラー：{len(errors)} 件"
        )

        print(
            f"空室発生：{len(changed_results)} 件"
        )

        # ====================================================
        # 実行履歴をstate.jsonに保存（直近5回分）
        # GitHub Actions側でstate.jsonがコミットされる前提
        # ====================================================

        run_history = previous_state.get("run_history", [])
        if not isinstance(run_history, list):
            run_history = []

        if not results:
            run_status = "失敗"
        elif errors:
            run_status = "一部エラー"
        else:
            run_status = "正常"

        run_history.append({
            "time": current_time.strftime("%Y-%m-%d %H:%M:%S"),
            "status": run_status,
            "checked": len(results),
            "errors": len(errors),
            "vacancies": sum(1 for _, vacancy, _ in results if vacancy),
        })
        run_history = run_history[-5:]
        previous_state["run_history"] = run_history

        # ====================================================
        # 初回メール
        # ====================================================

        if first_run and results:

            body = create_mail_body(
                results,
                "startup",
                errors,
                run_history
            )
            html_body = create_mail_html(
                results, "startup", errors, run_history
            )

            send_mail(
                "【東横INN・GitHub】監視開始時の空室状況",
                body,
                html_body
            )

            # 全体初回起動時は、この時点の状態をそのまま確定する
            for target, vacancy, room_status in results:
                previous_state[make_state_key(target)] = vacancy

        # ====================================================
        # 毎朝7:00メール
        # ====================================================

        today = current_time.strftime(
            "%Y-%m-%d"
        )

        last_daily_mail = previous_state.get(
            "last_daily_mail"
        )

        if (
            current_time.hour == 7
            and last_daily_mail != today
            and results
        ):

            print()
            print(
                "📢 毎朝7:00メールを送信します"
            )

            body = create_mail_body(
                results,
                "daily",
                errors,
                run_history
            )
            html_body = create_mail_html(
                results, "daily", errors, run_history
            )

            if send_mail(
                "【東横INN・GitHub】毎朝7:00監視確認",
                body,
                html_body
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
                "vacancy",
                errors,
                run_history
            )
            html_body = create_mail_html(
                changed_results, "vacancy", errors, run_history
            )

            mail_ok = send_mail(
                "【東横INN・GitHub】空室が発生しました！",
                body,
                html_body
            )

            if mail_ok:

                print(
                    "✅ 空室通知メール成功"
                )

                # メール成功後にだけ、
                # ×→○（または新規条件の初回空室）を
                # 現在状態として確定
                for target, vacancy, room_status in changed_results:

                    state_key = make_state_key(
                        target
                    )

                    previous_state[
                        state_key
                    ] = True

            else:

                print(
                    "⚠️ 空室通知メール失敗"
                )

                print(
                    "⚠️ 次回実行でも再通知できるよう、"
                    "空室ありの状態は未確定のままにします。"
                )

        # ====================================================
        # state.json保存
        # ====================================================

        save_state(
            previous_state
        )

        # ====================================================
        # 全件失敗ならGitHub Actionsも失敗にする
        # ====================================================

        if not results:

            raise RuntimeError(
                "すべての監視対象でチェックに失敗しました。"
            )

        if errors:

            print()
            print(
                "⚠️ 一部の監視対象でエラーが発生しています。"
            )

        print()
        print("=" * 70)
        print("今回の監視処理は完了しました。")
        print("=" * 70)

    finally:

        driver.quit()

        print()
        print(
            "Chromeを終了しました。"
        )


# ============================================================
# 起動
# ============================================================

if __name__ == "__main__":
    main()
