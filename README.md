# SnapSpread

スマホで帳票（レシート、ラベル、請求書等）を撮影し、ローカルVision LLMで情報を抽出してGoogleスプレッドシートに自動入力するWebアプリ。

## 特徴

- スマホカメラで帳票を撮影するだけでスプレッドシートに自動入力
- ローカルLLM（Ollama + qwen2.5-vl）で処理するため、データが外部に送信されない
- 抽出する列・項目をテキストで自由に指定できる

## 必要環境

- Docker / Docker Compose
- Googleサービスアカウント（Sheets API・Drive API有効化済み）

## セットアップ

### 1. 認証情報を配置

```bash
cp .env.example .env
# .env を編集して SPREADSHEET_ID を設定
# credentials/service_account.json にサービスアカウントのJSONキーを配置
```

### 2. 起動

```bash
docker compose up -d
```

初回起動時は `ollama-init` サービスがモデル（数GB）を自動でダウンロードします。完了まで数分〜十数分かかります。

```bash
# ダウンロード進捗を確認
docker compose logs -f ollama-init
```

### 3. アクセス

ブラウザ（スマホ可）で `http://localhost:8501` を開く。

## 使い方

1. 帳票画像をアップロード（またはカメラで撮影）
2. 抽出フォーマットを入力（例: `A列: 商品名, B列: 価格, C列: 生産者`）
3. 「実行」ボタンを押す
4. 抽出結果を確認してスプレッドシートに追記

## 仕様書

詳細は [`.spec-workflow/`](.spec-workflow/README.md) を参照。
