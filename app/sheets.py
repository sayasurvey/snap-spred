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


def _get_sheet():
    """gspreadシートオブジェクトを返す（共通処理）"""
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
        return client.open_by_key(config.SPREADSHEET_ID).sheet1
    except gspread.exceptions.APIError as e:
        status = e.response.status_code if hasattr(e, "response") else "不明"
        raise SheetsConnectionError(
            f"スプレッドシートへの接続に失敗しました（HTTP {status}）: {e}"
        )
    except Exception as e:
        raise SheetsConnectionError(
            f"スプレッドシートへの接続に失敗しました: {e}"
        )


def get_first_row() -> list[str]:
    """
    スプレッドシートの1行目（タイトル行）を取得する。

    戻り値:
        ["商品名", "価格", "生産者", ...] — 空セルは空文字列

    例外:
        SheetsConnectionError: 認証・接続エラー
    """
    sheet = _get_sheet()
    try:
        return sheet.row_values(1)
    except Exception as e:
        raise SheetsConnectionError(
            f"スプレッドシートの読み取り中にエラーが発生しました。（{e}）"
        )


def insert_title_row(titles: list[str]) -> bool:
    """
    スプレッドシートの1行目にタイトル行を挿入する。
    既存のデータは2行目以降にシフトされる。

    引数:
        titles: タイトル文字列のリスト

    戻り値:
        True（成功時）

    例外:
        SheetsConnectionError: 認証・接続エラー
    """
    sheet = _get_sheet()
    try:
        sheet.insert_row(titles, index=1, value_input_option="USER_ENTERED")
    except gspread.exceptions.APIError as e:
        status = e.response.status_code if hasattr(e, "response") else "不明"
        if status == 403:
            raise SheetsConnectionError(
                "スプレッドシートへの書き込み権限がありません。共有設定を確認してください。"
            )
        raise SheetsConnectionError(
            f"タイトル行の挿入中にエラーが発生しました。（{e}）"
        )
    except Exception as e:
        raise SheetsConnectionError(
            f"タイトル行の挿入中にエラーが発生しました。（{e}）"
        )
    return True


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
    sheet = _get_sheet()
    try:
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
