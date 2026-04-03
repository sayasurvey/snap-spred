# 02. システムアーキテクチャ

## 1. システム構成図

```
[ホストOS (macOS)]
│
├── Ollama（ネイティブインストール, Apple Metal GPU利用）
│     port:11434
│
└── Docker Compose
      │
      └── ┌──────────────────────────────────────────┐
          │  app (Streamlit)                          │
          │  port:8501                                │
          │                                          │
          │  HTTP (host.docker.internal:11434)        │
          │  ──────────────────────────────▶ Ollama   │
          │                                          │
          │  gspread (HTTPS)                         │
          │  ──────────────────────────────▶         │
          └──────────────────────────────────────────┘
                                            │
                                            ▼
                               ┌─────────────────────┐
                               │  Google Sheets API   │
                               │  (スプレッドシート)    │
                               └─────────────────────┘
```

> **補足**: OllamaはApple Metal GPU（M系チップ）を利用するためネイティブインストール。Dockerコンテナは`host.docker.internal`経由でホストのOllamaにアクセスする。

## 2. 技術スタック

| レイヤー | 技術 | 選定理由 |
|---------|------|---------|
| Web UI | Streamlit | Python単体でモバイル対応UIを構築可能。カメラ入力・ファイルアップロードが標準搭載 |
| ローカルLLM | Ollama | ローカルでVisionモデルをREST API経由で手軽に実行可能 |
| Visionモデル | qwen2.5vl:7b | 日本語OCR能力が高く、Ollamaで動作するVision対応7Bモデル |
| スプレッドシート | gspread + google-auth | Python向けGoogle Sheets APIの定番ライブラリ |
| コンテナ | Docker Compose | Streamlitのみ管理（OllamaはネイティブでApple Metal GPU利用） |

## 3. モジュール構成

### `app/main.py` — Streamlitエントリーポイント

- UIレイアウト（画像アップロード、フォーマット入力、実行ボタン、結果表示）
- 各モジュールの呼び出しを制御するオーケストレーション

### `app/llm.py` — Ollama API連携

- 画像をbase64エンコードしてOllama REST APIに送信
- システムプロンプトの組み立て（JSON出力制約を含む）
- レスポンスのJSONパース・バリデーション
- リトライロジック（不正JSON時）

### `app/sheets.py` — Googleスプレッドシート連携

- サービスアカウント認証
- スプレッドシートへの行追記（`append_row`）
- 接続エラーハンドリング

### `app/parser.py` — プロンプト解析・データ整形

- ユーザーが入力したフォーマット文字列（`A列: 商品名, B列: 価格...`）を解析
- 列名と項目名のマッピングを生成
- LLMの出力JSONをスプレッドシート用の行データ（リスト）に変換
- 「処理日」など、システム補完が必要な項目を埋める

### `app/config.py` — 設定管理

- 環境変数の読み込み（`.env`）
- デフォルト値の管理

## 4. Docker Compose構成

OllamaはApple Metal GPUを利用するためホストOS上でネイティブ実行する。Docker ComposeはStreamlitアプリのみを管理する。

```yaml
services:
  app:
    build:
      context: .
      platforms:
        - linux/arm64
    ports:
      - "8501:8501"
    env_file:
      - .env
    volumes:
      - ./credentials:/app/credentials:ro
      - ./app:/app/app:ro
    extra_hosts:
      # ホストマシン上のネイティブOllamaへアクセスするためのエントリ
      - "host.docker.internal:host-gateway"
    deploy:
      resources:
        limits:
          cpus: "2"
          memory: 2G
```

> **セットアップ手順**: Ollamaはホスト上で `brew install ollama && ollama pull qwen2.5vl:7b && ollama serve` で事前に起動しておく。Streamlitコンテナは `OLLAMA_BASE_URL=http://host.docker.internal:11434` 経由でホストのOllamaにアクセスする。

## 5. Dockerfile（Streamlitアプリ）

```dockerfile
FROM python:3.10-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app/ ./app/
EXPOSE 8501
CMD ["streamlit", "run", "app/main.py", "--server.address", "0.0.0.0"]
```
