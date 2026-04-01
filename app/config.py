"""設定管理モジュール — 環境変数の読み込みとデフォルト値の管理"""

import os
from dotenv import load_dotenv

load_dotenv()

SPREADSHEET_ID: str = os.getenv("SPREADSHEET_ID", "")
GOOGLE_CREDENTIALS_PATH: str = os.getenv(
    "GOOGLE_CREDENTIALS_PATH", "credentials/service_account.json"
)
OLLAMA_BASE_URL: str = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL: str = os.getenv("OLLAMA_MODEL", "qwen2.5-vl")

# HTTPタイムアウト設定
OLLAMA_CONNECT_TIMEOUT: int = 10   # 接続タイムアウト（秒）
OLLAMA_READ_TIMEOUT: int = 120     # 読み取りタイムアウト（秒）

# 画像リサイズ設定
IMAGE_MAX_SIDE: int = 1280
IMAGE_JPEG_QUALITY: int = 85

# LLMリトライ設定
LLM_MAX_RETRIES: int = 2
