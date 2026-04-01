# 02. システムアーキテクチャ

## 1. システム構成図

```
┌─────────────────────────────────────────────────────┐
│  Docker Compose                                      │
│                                                      │
│  ┌──────────────┐       ┌─────────────────────────┐ │
│  │  Streamlit    │──────▶│  Ollama                 │ │
│  │  (app)        │ HTTP  │  (qwen2.5-vl)           │ │
│  │  port:8501    │◀──────│  port:11434             │ │
│  └──────┬───────┘       └─────────────────────────┘ │
│         │                                            │
└─────────┼────────────────────────────────────────────┘
          │ gspread (HTTPS)
          ▼
┌─────────────────────┐
│  Google Sheets API   │
│  (スプレッドシート)    │
└─────────────────────┘
```

## 2. 技術スタック

| レイヤー | 技術 | 選定理由 |
|---------|------|---------|
| Web UI | Streamlit | Python単体でモバイル対応UIを構築可能。カメラ入力・ファイルアップロードが標準搭載 |
| ローカルLLM | Ollama | ローカルでVisionモデルをREST API経由で手軽に実行可能 |
| Visionモデル | qwen2.5-vl | 日本語OCR能力が高く、Ollamaで動作するVision対応モデル |
| スプレッドシート | gspread + google-auth | Python向けGoogle Sheets APIの定番ライブラリ |
| コンテナ | Docker Compose | Ollama + Streamlitを一括管理、環境差異を排除 |

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

```yaml
services:
  ollama:
    image: ollama/ollama
    ports:
      - "11434:11434"
    volumes:
      - ollama_data:/root/.ollama
    # GPU利用時は deploy.resources.reservations.devices を追加
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:11434"]
      interval: 10s
      timeout: 5s
      retries: 5
      start_period: 10s

  ollama-init:
    image: ollama/ollama
    depends_on:
      ollama:
        condition: service_healthy
    volumes:
      - ollama_data:/root/.ollama
    entrypoint: >
      sh -c "ollama pull ${OLLAMA_MODEL:-qwen2.5-vl}"
    environment:
      - OLLAMA_HOST=http://ollama:11434
    restart: "no"

  app:
    build: .
    ports:
      - "8501:8501"
    env_file:
      - .env
    volumes:
      - ./credentials:/app/credentials:ro
    depends_on:
      ollama:
        condition: service_healthy

volumes:
  ollama_data:
```

> **注意**: `ollama-init` サービスは初回起動時にモデルを自動でpullする。モデルのサイズは数GB程度のため、初回の `docker compose up` はダウンロード完了まで数分〜十数分かかる。`ollama_data` ボリュームにキャッシュされるため、2回目以降は不要。

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
