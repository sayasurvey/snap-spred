"""SnapSpread — Streamlitエントリーポイント"""

import sys
import os
# Streamlitはスクリプトのディレクトリをsys.pathに追加するため、
# プロジェクトルートを明示的に追加してパッケージインポートを解決する
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import streamlit as st
from PIL import Image
import io

from app.parser import parse_format, build_row
from app.llm import extract_data, LLMConnectionError, LLMParseError
from app.sheets import append_to_sheet, SheetsConnectionError


# ページ設定（スマホ操作を優先したレイアウト）
st.set_page_config(
    page_title="SnapSpread",
    page_icon="📷",
    layout="centered",
    initial_sidebar_state="collapsed",
)

st.title("📷 SnapSpread")
st.caption("帳票を撮影してGoogleスプレッドシートに自動入力")

# ───────────────────────────────────────
# 画像アップロード
# ───────────────────────────────────────
st.subheader("① 画像をアップロード")
uploaded_file = st.file_uploader(
    "JPEG / PNG のみ対応（HEICは非対応）",
    type=["jpg", "jpeg", "png"],
    accept_multiple_files=False,
)

if uploaded_file is not None:
    # ファイル拡張子の簡易チェック
    filename = uploaded_file.name.lower()
    if filename.endswith((".heic", ".gif", ".webp")):
        st.error(
            "この形式は非対応です（HEIC / GIF / WEBP）。"
            "iPhoneの場合は 設定 > カメラ > フォーマット を「互換性優先」にしてJPEGで撮影してください。"
        )
        uploaded_file = None
    else:
        st.image(uploaded_file, caption="アップロード画像", use_container_width=True)

# ───────────────────────────────────────
# フォーマット入力
# ───────────────────────────────────────
st.subheader("② 抽出フォーマットを入力")
st.caption("例: A列: 商品名, B列: 価格, C列: 生産者, D列: 出荷日, E列: JANコード, F列: 処理日")

if "format_text" not in st.session_state:
    st.session_state["format_text"] = ""

format_text = st.text_area(
    "フォーマット",
    value=st.session_state["format_text"],
    height=100,
    placeholder="A列: 商品名, B列: 価格, C列: 生産者",
    label_visibility="collapsed",
)
st.session_state["format_text"] = format_text

# ───────────────────────────────────────
# 実行ボタン
# ───────────────────────────────────────
st.subheader("③ 実行")
run_button = st.button("📤 抽出してスプレッドシートに記録", use_container_width=True, type="primary")

if run_button:
    # バリデーション
    if uploaded_file is None:
        st.error("画像をアップロードしてください。")
        st.stop()

    if not format_text.strip():
        st.error("フォーマットを入力してください。")
        st.stop()

    # フォーマット解析
    try:
        format_info = parse_format(format_text)
    except ValueError as e:
        st.error(str(e))
        st.stop()

    llm_fields = format_info["llm_fields"]

    # 画像バイト列の取得
    image_bytes = uploaded_file.getvalue()

    # LLM抽出
    with st.spinner("画像を解析中...（数十秒かかる場合があります）"):
        try:
            llm_result = extract_data(image_bytes, llm_fields)
        except LLMConnectionError as e:
            st.error(str(e))
            st.stop()
        except LLMParseError as e:
            st.error(str(e))
            st.stop()

    # システム補完と行データ生成
    row_data = build_row(llm_result, format_info)

    # プレビュー表示
    st.subheader("④ 抽出結果")
    columns = format_info["columns"]
    sorted_cols = sorted(columns.keys(), key=lambda c: (len(c), c))
    preview = {f"{col}列 ({columns[col]})": row_data[i] for i, col in enumerate(sorted_cols)}
    st.table(preview)

    # スプレッドシート追記
    with st.spinner("スプレッドシートに書き込み中..."):
        try:
            append_to_sheet(row_data)
            st.success("スプレッドシートへの追記が完了しました！")
        except SheetsConnectionError as e:
            st.error(str(e))
