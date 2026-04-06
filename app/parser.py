"""プロンプト解析・データ整形モジュール"""

import re
from datetime import datetime


# システム補完キーワードと補完ロジック
_SYSTEM_FIELD_GENERATORS = {
    "処理日": lambda: datetime.now().strftime("%Y/%m/%d"),
    "登録日時": lambda: datetime.now().strftime("%Y/%m/%d %H:%M"),
}


def parse_format(format_text: str) -> dict:
    """
    フォーマット文字列を解析して列マッピングを返す。

    引数:
        format_text: "A列: 商品名, B列: 価格, ..."

    戻り値:
        {
            "columns": {"A": "商品名", "B": "価格", ...},
            "llm_fields": ["商品名", "価格", ...],
            "system_fields": {"F": ("処理日", "2026/04/01")}
        }

    例外:
        ValueError: フォーマット文字列が不正な場合
    """
    if not format_text or not format_text.strip():
        raise ValueError("フォーマットを入力してください")

    # "A列: 商品名" または "A: 商品名" の形式をサポート
    pattern = re.compile(r"([A-Za-z]+)列?\s*[:：]\s*(.+?)(?=\s*,\s*[A-Za-z]+列?\s*[:：]|$)")
    matches = pattern.findall(format_text)

    if not matches:
        raise ValueError(
            "フォーマットを正しく入力してください（例: A列: 商品名, B列: 価格）"
        )

    columns: dict[str, str] = {}
    llm_fields: list[str] = []
    system_fields: dict[str, tuple[str, str]] = {}

    for col_letter, field_name in matches:
        col = col_letter.upper()
        field = field_name.strip()
        columns[col] = field

        # システム補完対象かどうか判定
        matched_key = _match_system_key(field)
        if matched_key is not None:
            value = _SYSTEM_FIELD_GENERATORS[matched_key]()
            system_fields[col] = (field, value)
        else:
            llm_fields.append(field)

    return {
        "columns": columns,
        "llm_fields": llm_fields,
        "system_fields": system_fields,
    }


def _match_system_key(field_name: str) -> str | None:
    """フィールド名にシステム補完キーワードが含まれるか判定する"""
    for keyword in _SYSTEM_FIELD_GENERATORS:
        if keyword in field_name:
            return keyword
    return None


def build_row(llm_result: dict, format_info: dict) -> list:
    """
    LLMの抽出結果とシステム補完を統合し、列順のリストを返す。

    引数:
        llm_result: {"商品名": "キャベツ", "価格": "380", ...}
        format_info: parse_format() の戻り値

    戻り値:
        ["キャベツ", "380", "山下 太郎", "2018年1月1日", "2101001003801", "2026/04/01"]
    """
    columns = format_info["columns"]
    system_fields = format_info["system_fields"]

    # 列アルファベット順（A, B, C, ...）でソート
    sorted_cols = sorted(columns.keys(), key=_col_sort_key)

    row = []
    for col in sorted_cols:
        field = columns[col]
        if col in system_fields:
            # システム補完値を使用
            _, value = system_fields[col]
            row.append(value)
        else:
            # LLM抽出結果から取得（存在しない場合は空文字）
            value = llm_result.get(field)
            row.append("" if value is None else str(value))

    return row


def _col_sort_key(col: str) -> tuple:
    """列名をソートするためのキー（A < B < ... < Z < AA < AB < ...）"""
    col = col.upper()
    return (len(col), col)
