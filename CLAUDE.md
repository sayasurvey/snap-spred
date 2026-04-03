# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## プロジェクト概要

**SnapSpread** — スマホで帳票（レシート、ラベル、請求書等）を撮影し、ローカルVision LLMで情報を抽出してGoogleスプレッドシートに自動入力するWebアプリ。

## 技術スタック

- **言語**: Python 3.10+
- **Web UI**: Streamlit（スマホブラウザ対応）
- **ローカルLLM**: Ollama（qwen2.5vl:7b モデル）
- **スプレッドシート連携**: gspread + google-auth（サービスアカウント方式）
- **コンテナ**: Docker Compose（Streamlitのみ管理）
- **LLM実行環境**: Ollamaはネイティブインストール（Apple Metal GPU利用のため）

## 開発コマンド

```bash
# 【初回セットアップ】Ollamaをネイティブインストール（Apple Metal GPU利用）
brew install ollama
ollama pull qwen2.5vl:7b

# Ollamaサーバー起動（バックグラウンド）
ollama serve

# 開発環境起動（Docker: Streamlitのみ）
docker compose up -d

# Streamlitアプリ単体起動（ローカル開発時）
streamlit run app/main.py

# 依存関係インストール
pip install -r requirements.txt
```

## プロジェクト構成

```
SnapSpread/
├── app/
│   ├── main.py              # Streamlitエントリーポイント
│   ├── llm.py               # Ollama API連携（画像→JSON抽出）
│   ├── sheets.py            # Googleスプレッドシート書き込み
│   ├── parser.py            # ユーザープロンプト解析・データ整形
│   └── config.py            # 設定管理
├── docker-compose.yml
├── Dockerfile
├── requirements.txt
├── .env.example             # 環境変数テンプレート
├── .spec-workflow/          # 仕様書
└── credentials/             # サービスアカウントJSON（.gitignore対象）
```

## 開発ルール

- ドキュメント・`.md`ファイル・コード内コメントは**日本語**で記述すること
- 環境構築は**Docker Compose**を使用すること
- サービスアカウントのJSONキーや`.env`ファイルは**絶対にコミットしない**こと
- LLMへのリクエストでは必ず**JSON形式での出力**を指示し、マークダウンや説明文を含めないよう制約すること
- LLMからのJSON応答は必ず**バリデーション**してからスプレッドシートに書き込むこと
- Streamlit UIはスマホでの操作を第一に考え、**シンプルで大きなタップターゲット**を意識すること

## 環境変数（.env）

```
SPREADSHEET_ID=対象スプレッドシートのID
GOOGLE_CREDENTIALS_PATH=credentials/service_account.json
OLLAMA_BASE_URL=http://host.docker.internal:11434
OLLAMA_MODEL=qwen2.5vl:7b
```

## 仕様書

詳細な仕様は `.spec-workflow/` ディレクトリを参照。
