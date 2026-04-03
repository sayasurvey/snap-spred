"""Ollama API連携モジュール — 画像からのデータ抽出"""

import base64
import io
import json
import re

import requests
from PIL import Image

from app import config


class LLMConnectionError(Exception):
    """Ollamaへの接続・タイムアウトエラー"""


class LLMParseError(Exception):
    """JSONパースが最終的に失敗した場合のエラー"""


_SYSTEM_PROMPT = """あなたは優秀なデータ入力アシスタントです。
添付された画像から、ユーザーが要求する項目を正確に読み取り抽出してください。

【絶対ルール】
1. 出力は必ずJSON配列のみとすること
2. 書類に同種の明細・品目が複数ある場合は、各項目を配列の1要素として出力すること
3. 項目が1つのみの場合も必ず配列で返すこと（例: [{"商品名": "キャベツ"}]）
4. マークダウン記法（```json 等）は使用しないこと
5. 説明文、前置き、後書きは一切含めないこと
6. 画像から読み取れない項目は値を null とすること
7. 数値は文字列として出力すること（例: "380"）
8. 日本語テキストはそのまま日本語で出力すること"""


def extract_data_freeform(image_bytes: bytes, model: str = None) -> list[dict]:
    """
    フォーマット未指定時のフリーフォーム抽出。
    LLMが画像から読み取れる主要な情報を自由に返す。

    戻り値:
        [{"項目名": "値", ...}, ...]

    例外:
        LLMConnectionError / LLMParseError
    """
    return extract_data(image_bytes, fields=[], model=model)


def extract_data(image_bytes: bytes, fields: list[str], model: str = None) -> list[dict]:
    """
    画像からフィールドを抽出してJSON辞書のリストで返す。
    1つの書類に複数の明細行がある場合は複数要素のリストになる。

    引数:
        image_bytes: 画像のバイナリデータ（JPEG / PNG）
        fields: ["品名", "数量", "単価", ...]
        model: Ollamaモデル名（省略時はconfig.pyのデフォルト）

    戻り値:
        [{"品名": "商品A", "数量": "1", ...}, {"品名": "商品B", ...}, ...]

    例外:
        LLMConnectionError: 接続できない・タイムアウトした場合
        LLMParseError: JSONパースが最終的に失敗した場合
    """
    model = model or config.OLLAMA_MODEL
    resized = _resize_image(image_bytes)
    b64_image = base64.b64encode(resized).decode("utf-8")

    for attempt in range(config.LLM_MAX_RETRIES + 1):
        user_prompt = _build_user_prompt(fields, retry=attempt > 0)
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": user_prompt,
                    "images": [b64_image],
                },
            ],
            "stream": False,
            "options": {"temperature": 0.1},
        }

        try:
            response = requests.post(
                f"{config.OLLAMA_BASE_URL}/api/chat",
                json=payload,
                timeout=(config.OLLAMA_CONNECT_TIMEOUT, config.OLLAMA_READ_TIMEOUT),
            )
            response.raise_for_status()
        except requests.exceptions.ConnectionError:
            raise LLMConnectionError(
                "LLMサーバーに接続できません。Ollamaが起動しているか確認してください。"
            )
        except requests.exceptions.Timeout:
            raise LLMConnectionError(
                "処理がタイムアウトしました。再試行するか、より軽量なモデルへの切り替えを検討してください。"
            )
        except requests.exceptions.HTTPError as e:
            status = e.response.status_code if e.response is not None else None
            if status == 404:
                raise LLMConnectionError(
                    f"モデル '{model}' が見つかりません。"
                    "モデルがまだダウンロード中の可能性があります。"
                    f"ターミナルで `docker compose logs -f ollama-init` を確認してください。"
                )
            raise LLMConnectionError(f"LLMサーバーからエラーが返されました（HTTP {status}）: {e}")
        except requests.exceptions.RequestException as e:
            raise LLMConnectionError(f"LLMサーバーとの通信中にエラーが発生しました: {e}")

        content = response.json()["message"]["content"]
        result = _extract_json_list(content)
        if result is not None:
            return result

    raise LLMParseError(
        "データの読み取りに失敗しました。画像を変えて再試行してください。"
    )


def _build_user_prompt(fields: list[str], retry: bool = False) -> str:
    """ユーザープロンプトを組み立てる"""
    retry_note = "\n※必ずJSON配列のみを返してください。説明文は不要です。" if retry else ""

    if not fields:
        # フリーフォームモード：LLMが項目を自由に決定
        return f"""この画像に記載されている主要な情報をすべて抽出して、JSON配列形式で返してください。{retry_note}

明細が複数行ある場合は各行を配列の1要素として出力してください。
キー名は日本語で、画像から読み取れる実際の項目名を使用してください。

【出力例（レシートの場合）】
[{{"品名": "キャベツ", "数量": "1", "金額": "198"}}, {{"品名": "豆腐", "数量": "2", "金額": "158"}}]"""

    fields_text = "\n".join(f"- {f}" for f in fields)
    item = "{" + ", ".join(f'"{f}": "値"' for f in fields) + "}"
    example_single = f"[{item}]"
    example_multi = f"[{item}, {item}]"

    return f"""以下の項目を画像から抽出して、JSON配列形式で返してください。{retry_note}

【抽出項目】
{fields_text}

【出力例（明細が1件の場合）】
{example_single}

【出力例（明細が複数件の場合）】
{example_multi}"""


def _extract_json_list(text: str) -> list[dict] | None:
    """テキストからJSON配列または辞書を抽出し、list[dict]形式で返す。失敗した場合はNone"""

    def _normalize(obj) -> list[dict] | None:
        if isinstance(obj, list) and all(isinstance(i, dict) for i in obj):
            return obj
        if isinstance(obj, dict):
            return [obj]
        return None

    # 1. そのままパース
    try:
        result = _normalize(json.loads(text.strip()))
        if result is not None:
            return result
    except json.JSONDecodeError:
        pass

    # 2. [...] ブロックを抽出して再試行
    match = re.search(r"\[.*\]", text, re.DOTALL)
    if match:
        try:
            result = _normalize(json.loads(match.group()))
            if result is not None:
                return result
        except json.JSONDecodeError:
            pass

    # 3. {...} ブロックを抽出して再試行（配列でない応答への対応）
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            result = _normalize(json.loads(match.group()))
            if result is not None:
                return result
        except json.JSONDecodeError:
            pass

    return None


def _resize_image(image_bytes: bytes, max_side: int = None) -> bytes:
    """
    画像を長辺max_side px以内にリサイズしてJPEGバイト列で返す。

    引数:
        image_bytes: 元画像のバイナリデータ
        max_side: リサイズ上限（デフォルトはconfig.IMAGE_MAX_SIDE）

    戻り値:
        リサイズ後のJPEGバイト列（品質85）
    """
    max_side = max_side or config.IMAGE_MAX_SIDE
    img = Image.open(io.BytesIO(image_bytes))

    # EXIF回転を考慮
    try:
        from PIL import ImageOps
        img = ImageOps.exif_transpose(img)
    except Exception:
        pass

    # RGBAやパレットモードはRGBに変換（JPEG非対応）
    if img.mode in ("RGBA", "P"):
        img = img.convert("RGB")

    w, h = img.size
    if max(w, h) > max_side:
        scale = max_side / max(w, h)
        img = img.resize((int(w * scale), int(h * scale)), Image.BILINEAR)

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=config.IMAGE_JPEG_QUALITY)
    return buf.getvalue()
