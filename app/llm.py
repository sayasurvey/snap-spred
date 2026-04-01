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
1. 出力は必ずJSONオブジェクトのみとすること
2. マークダウン記法（```json 等）は使用しないこと
3. 説明文、前置き、後書きは一切含めないこと
4. 画像から読み取れない項目は値を null とすること
5. 数値は文字列として出力すること（例: "380"）
6. 日本語テキストはそのまま日本語で出力すること"""


def extract_data(image_bytes: bytes, fields: list[str], model: str = None) -> dict:
    """
    画像からフィールドを抽出してJSON辞書で返す。

    引数:
        image_bytes: 画像のバイナリデータ（JPEG / PNG）
        fields: ["商品名", "価格", "生産者", ...]
        model: Ollamaモデル名（省略時はconfig.pyのデフォルト）

    戻り値:
        {"商品名": "キャベツ", "価格": "380", ...}

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
        result = _extract_json(content)
        if result is not None:
            return result

    raise LLMParseError(
        "データの読み取りに失敗しました。画像を変えて再試行してください。"
    )


def _build_user_prompt(fields: list[str], retry: bool = False) -> str:
    """ユーザープロンプトを組み立てる"""
    fields_text = "\n".join(f"- {f}" for f in fields)
    example = "{" + ", ".join(f'"{f}": "値"' for f in fields) + "}"
    retry_note = "\n※必ずJSONオブジェクトのみを返してください。説明文は不要です。" if retry else ""

    return f"""以下の項目を画像から抽出してJSON形式で返してください。{retry_note}

【抽出項目】
{fields_text}

【出力例】
{example}"""


def _extract_json(text: str) -> dict | None:
    """テキストからJSONを抽出してパースする。失敗した場合はNoneを返す"""
    # 1. そのままパースを試みる
    try:
        return json.loads(text.strip())
    except json.JSONDecodeError:
        pass

    # 2. 正規表現で {} ブロックを抽出して再試行
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
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
        img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=config.IMAGE_JPEG_QUALITY)
    return buf.getvalue()
