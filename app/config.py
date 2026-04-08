"""設定管理モジュール — 環境変数の読み込みとデフォルト値の管理"""

import os
import json
from dotenv import load_dotenv

load_dotenv()

SPREADSHEET_ID: str = os.getenv("SPREADSHEET_ID", "")
GOOGLE_CREDENTIALS_PATH: str = os.getenv(
    "GOOGLE_CREDENTIALS_PATH", "credentials/service_account.json"
)
OLLAMA_BASE_URL: str = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL: str = os.getenv("OLLAMA_MODEL", "qwen2.5vl:7b")

# HTTPタイムアウト設定
OLLAMA_CONNECT_TIMEOUT: int = 10   # 接続タイムアウト（秒）
OLLAMA_READ_TIMEOUT: int = 1200     # 読み取りタイムアウト（秒）

# 画像リサイズ設定
IMAGE_MAX_SIDE: int = 800
IMAGE_JPEG_QUALITY: int = 85

# LLMリトライ設定
LLM_MAX_RETRIES: int = 2

# 並列処理設定（OLLAMA_NUM_PARALLELと合わせること）
LLM_MAX_WORKERS: int = int(os.getenv("LLM_MAX_WORKERS", "3"))

# ユーザー設定ファイルのパス（Docker環境では data/ ディレクトリをボリュームマウントすること）
USER_SETTINGS_PATH: str = os.getenv(
    "USER_SETTINGS_PATH",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "user_settings.json"),
)

# 組み込みフォーマットプリセット
DEFAULT_FORMAT_PRESETS: dict[str, str] = {
    "レシート（品名・数量・単価・金額）": "A列: 品名, B列: 数量, C列: 単価, D列: 金額",
    "請求書（品目・数量・単価・金額）": "A列: 品目, B列: 数量, C列: 単価, D列: 金額",
    "納品書（品名・数量・単価）": "A列: 品名, B列: 数量, C列: 単価",
    "ラベル（商品名・賞味期限・価格）": "A列: 商品名, B列: 賞味期限, C列: 価格",
}


def load_user_settings() -> dict:
    """ユーザー設定をJSONファイルから読み込む。ファイルがない場合は空辞書を返す"""
    try:
        with open(USER_SETTINGS_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_user_settings(settings: dict) -> None:
    """ユーザー設定をJSONファイルに保存する"""
    os.makedirs(os.path.dirname(USER_SETTINGS_PATH), exist_ok=True)
    with open(USER_SETTINGS_PATH, "w", encoding="utf-8") as f:
        json.dump(settings, f, ensure_ascii=False, indent=2)
