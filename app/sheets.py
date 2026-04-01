"""Googleスプレッドシート連携モジュール"""

import gspread
from google.oauth2.service_account import Credentials

from app import config


_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


class SheetsConnectionError(Exception):
    """スプレッドシートへの接続・認証エラー"""


def append_to_sheet(row_data: list) -> bool:
    """
    スプレッドシートの最終行にデータを追記する。

    引数:
        row_data: 列順のデータリスト

    戻り値:
        True（成功時）

    例外:
        SheetsConnectionError: 認証・接続エラー
    """
    try:
        creds = Credentials.from_service_account_file(
            config.GOOGLE_CREDENTIALS_PATH, scopes=_SCOPES
        )
    except FileNotFoundError:
        raise SheetsConnectionError(
            "サービスアカウントのJSONファイルが見つかりません。"
            f"パス: {config.GOOGLE_CREDENTIALS_PATH}"
        )
    except Exception as e:
        raise SheetsConnectionError(
            f"スプレッドシートへの認証に失敗しました。サービスアカウントの設定を確認してください。（{e}）"
        )

    try:
        client = gspread.authorize(creds)
        sheet = client.open_by_key(config.SPREADSHEET_ID).sheet1
        sheet.append_row(row_data, value_input_option="USER_ENTERED")
    except gspread.exceptions.APIError as e:
        status = e.response.status_code if hasattr(e, "response") else "不明"
        if status == 403:
            raise SheetsConnectionError(
                "スプレッドシートへの書き込み権限がありません。共有設定を確認してください。"
            )
        raise SheetsConnectionError(
            f"スプレッドシートへの書き込み中にエラーが発生しました。（{e}）"
        )
    except Exception as e:
        raise SheetsConnectionError(
            f"スプレッドシートへの書き込み中にエラーが発生しました。（{e}）"
        )

    return True
