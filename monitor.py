
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
