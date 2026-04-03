"""SnapSpread — Streamlitエントリーポイント"""

import sys
import os
# Streamlitはスクリプトのディレクトリをsys.pathに追加するため、
# プロジェクトルートを明示的に追加してパッケージインポートを解決する
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import streamlit as st
from PIL import Image
import io
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

from app import config
from app.parser import parse_format, build_row
from app.llm import extract_data, extract_data_freeform, LLMConnectionError, LLMParseError
from app.sheets import append_to_sheet, insert_title_row, get_first_row, SheetsConnectionError


@st.cache_data(show_spinner=False)
def _cached_extract_data(image_bytes: bytes, fields: tuple[str, ...]) -> list[dict]:
    """LLM抽出結果をメモリキャッシュ（同一画像×同一フィールドは再計算しない）"""
    return extract_data(image_bytes, list(fields))


@st.cache_data(show_spinner=False)
def _cached_extract_freeform(image_bytes: bytes) -> list[dict]:
    """フリーフォームLLM抽出結果をメモリキャッシュ"""
    return extract_data_freeform(image_bytes)


def _get_exif_datetime(image_bytes: bytes) -> datetime | None:
    """JPEG EXIFから撮影日時を取得する。取得できない場合はNoneを返す"""
    try:
        img = Image.open(io.BytesIO(image_bytes))
        exif_data = img._getexif()  # type: ignore[attr-defined]
        if exif_data:
            # 36867: DateTimeOriginal / 306: DateTime
            dt_str = exif_data.get(36867) or exif_data.get(306)
            if dt_str:
                return datetime.strptime(str(dt_str), "%Y:%m:%d %H:%M:%S")
    except Exception:
        pass
    return None


def _idx_to_col(idx: int) -> str:
    """0始まりのインデックスを列アルファベットに変換（0→A, 25→Z, 26→AA, …）"""
    result = ""
    n = idx + 1
    while n:
        n, rem = divmod(n - 1, 26)
        result = chr(65 + rem) + result
    return result


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
# ① 画像アップロード（複数対応）
# ───────────────────────────────────────
st.subheader("① 画像をアップロード")
uploaded_files = st.file_uploader(
    "JPEG / PNG のみ対応（HEICは非対応）・複数ファイル選択可",
    type=["jpg", "jpeg", "png"],
    accept_multiple_files=True,
)

valid_files = []
if uploaded_files:
    for f in uploaded_files:
        if f.name.lower().endswith((".heic", ".gif", ".webp")):
            st.error(
                f"**{f.name}** は非対応形式です（HEIC / GIF / WEBP）。"
                "iPhoneの場合は 設定 > カメラ > フォーマット を「互換性優先」にしてJPEGで撮影してください。"
            )
        else:
            valid_files.append(f)

    if valid_files:
        cols = st.columns(min(len(valid_files), 3))
        for i, f in enumerate(valid_files):
            with cols[i % 3]:
                st.image(f, caption=f.name, use_container_width=True)

# ───────────────────────────────────────
# ② 抽出フォーマット入力（任意）
# ───────────────────────────────────────
st.subheader("② 抽出フォーマットを入力（任意）")
st.caption(
    "例: A列: 品名, B列: 数量, C列: 単価, D列: 金額\n"
    "明細が複数行ある場合はそれぞれ1行として登録されます。\n"
    "※ スプレッドシートにタイトル行が設定済みの場合は空欄でも自動読み込みします"
)

if "format_text" not in st.session_state:
    st.session_state["format_text"] = ""

format_text = st.text_area(
    "フォーマット",
    height=100,
    placeholder="A列: 品名, B列: 数量, C列: 単価, D列: 金額",
    label_visibility="collapsed",
    key="format_text",
)

# ───────────────────────────────────────
# ③ 登録順の選択
# ───────────────────────────────────────
st.subheader("③ 登録順を選択")

_sort_options = ["アップロード順", "撮影日順（EXIF）"]

# フォーマット入力済みの場合、LLM抽出列によるソートを追加
_col_sort_candidates: list[tuple[str, str]] = []  # [(col_letter, field_name), ...]
if format_text.strip():
    try:
        _fmt_preview = parse_format(format_text)
        sys_cols = set(_fmt_preview.get("system_fields", {}).keys())
        for col, field in sorted(
            _fmt_preview["columns"].items(), key=lambda x: (len(x[0]), x[0])
        ):
            if col not in sys_cols:
                _col_sort_candidates.append((col, field))
        if _col_sort_candidates:
            _sort_options.append("列の値で並び替え")
    except ValueError:
        pass

sort_order = st.selectbox("並び順", _sort_options, label_visibility="collapsed")

sort_col_key: str | None = None  # 並び替え基準の列アルファベット
if sort_order == "列の値で並び替え" and _col_sort_candidates:
    col_labels = [f"{col}列: {field}" for col, field in _col_sort_candidates]
    selected_label = st.selectbox("並び替え基準の列", col_labels)
    sort_col_key = selected_label.split("列:")[0].strip()

# ───────────────────────────────────────
# ④ 実行ボタン
# ───────────────────────────────────────
st.subheader("④ 実行")
run_button = st.button("📤 抽出してスプレッドシートに記録", use_container_width=True, type="primary")

if run_button:
    # ── 画像バリデーション ──
    if not valid_files:
        st.error("画像をアップロードしてください。")
        st.stop()

    # ── スプレッドシートのタイトル行を確認 ──
    with st.spinner("スプレッドシートのタイトル行を確認中..."):
        try:
            sheet_first_row = get_first_row()
        except SheetsConnectionError as e:
            st.error(str(e))
            st.stop()

    sheet_titles = [(i, v) for i, v in enumerate(sheet_first_row) if v.strip()]
    has_sheet_titles = len(sheet_titles) > 0

    # ── フォーマットの決定 ──
    freeform_mode = False
    format_info = None
    llm_fields: list[str] = []

    if format_text.strip():
        try:
            format_info = parse_format(format_text)
        except ValueError as e:
            st.error(str(e))
            st.stop()
        llm_fields = format_info["llm_fields"]
    elif has_sheet_titles:
        use_format_text = ", ".join(
            f"{_idx_to_col(i)}列: {v}" for i, v in sheet_titles
        )
        st.info(f"スプレッドシートのタイトルからフォーマットを自動検出しました: {use_format_text}")
        try:
            format_info = parse_format(use_format_text)
        except ValueError as e:
            st.error(str(e))
            st.stop()
        llm_fields = format_info["llm_fields"]
    else:
        # フリーフォームモード: LLMが項目を自動判定
        freeform_mode = True
        st.info("フォーマット未指定のため、LLMが画像から自動的に項目を抽出します。")

    # ── 各画像を処理（並列） ──
    # records: {"file_name", "exif_dt", "llm_result", "row_data"} のリスト
    # 1画像に複数明細がある場合は複数レコードになる
    records: list[dict] = []
    total = len(valid_files)
    progress = st.progress(0, text="画像を解析中...")

    # ── リアルタイム経過時間タイマー ──
    _start_time = time.time()
    _timer_placeholder = st.empty()
    _stop_event = threading.Event()

    # メインスレッドのコンテキストをここで取得してからスレッドに渡す
    try:
        from streamlit.runtime.scriptrunner import add_script_run_ctx, get_script_run_ctx
        _main_ctx = get_script_run_ctx()
    except Exception:
        _main_ctx = None

    def _timer_worker():
        if _main_ctx is not None:
            try:
                add_script_run_ctx(threading.current_thread(), _main_ctx)
            except Exception:
                pass
        while not _stop_event.is_set():
            elapsed = time.time() - _start_time
            mins, secs = divmod(int(elapsed), 60)
            try:
                _timer_placeholder.markdown(f"⏱ 経過時間: **{mins:02d}:{secs:02d}**", unsafe_allow_html=False)
            except Exception:
                break
            time.sleep(0.5)

    _timer_thread = threading.Thread(target=_timer_worker, daemon=True)
    _timer_thread.start()

    # ファイルの読み込みはメインスレッドで事前に行う（ファイルオブジェクトはスレッドセーフでないため）
    file_data = [(f.name, f.getvalue()) for f in valid_files]

    def _process_one(file_name: str, image_bytes: bytes) -> tuple:
        """1枚の画像をLLMで解析して結果を返す"""
        exif_dt = _get_exif_datetime(image_bytes)
        if freeform_mode:
            llm_results = _cached_extract_freeform(image_bytes)
        else:
            llm_results = _cached_extract_data(image_bytes, tuple(llm_fields))
        return file_name, exif_dt, llm_results

    completed_count = 0
    errors: list[str] = []

    with ThreadPoolExecutor(max_workers=config.LLM_MAX_WORKERS) as executor:
        future_to_name = {
            executor.submit(_process_one, name, data): name
            for name, data in file_data
        }
        for future in as_completed(future_to_name):
            completed_count += 1
            progress.progress(
                completed_count / total,
                text=f"解析中 ({completed_count}/{total}): {future_to_name[future]}",
            )
            try:
                file_name, exif_dt, llm_results = future.result()
                for llm_result in llm_results:
                    if freeform_mode:
                        records.append({
                            "file_name": file_name,
                            "exif_dt": exif_dt,
                            "llm_result": llm_result,
                        })
                    else:
                        records.append({
                            "file_name": file_name,
                            "exif_dt": exif_dt,
                            "row_data": build_row(llm_result, format_info),
                        })
            except (LLMConnectionError, LLMParseError) as e:
                errors.append(f"**{future_to_name[future]}**: {e}")

    progress.progress(1.0, text="解析完了")

    # ── タイマー停止・最終経過時間を表示 ──
    _stop_event.set()
    _timer_thread.join(timeout=1)
    _elapsed_total = time.time() - _start_time
    _mins_total, _secs_total = divmod(int(_elapsed_total), 60)
    _timer_placeholder.markdown(f"⏱ 解析完了: **{_mins_total:02d}:{_secs_total:02d}**")

    # ── エラーがあれば表示して中断 ──
    if errors:
        for err in errors:
            st.error(err)
        st.stop()

    # ── フリーフォームモード: 全レコードのキーを収集して列を動的生成 ──
    if freeform_mode:
        all_keys: list[str] = []
        for r in records:
            for k in r["llm_result"].keys():
                if k not in all_keys:
                    all_keys.append(k)
        columns = {_idx_to_col(i): k for i, k in enumerate(all_keys)}
        for r in records:
            r["row_data"] = [str(r["llm_result"].get(k, "")) for k in all_keys]
        format_info = {"columns": columns, "llm_fields": all_keys, "system_fields": {}}
    else:
        columns = format_info["columns"]

    # ── 並び替え ──
    sorted_cols = sorted(columns.keys(), key=lambda c: (len(c), c))

    if sort_order == "撮影日順（EXIF）":
        no_exif = [r for r in records if r["exif_dt"] is None]
        if no_exif:
            names = ", ".join(dict.fromkeys(r["file_name"] for r in no_exif))
            st.warning(f"以下の画像にEXIF撮影日時がないため、末尾に配置します: {names}")
        records.sort(key=lambda r: r["exif_dt"] or datetime.max)

    elif sort_order == "列の値で並び替え" and sort_col_key:
        sort_idx = sorted_cols.index(sort_col_key) if sort_col_key in sorted_cols else 0

        def _col_sort_key(r: dict):
            val = r["row_data"][sort_idx] if sort_idx < len(r["row_data"]) else ""
            try:
                return (0, float(val), "")
            except (ValueError, TypeError):
                return (1, 0.0, str(val))

        records.sort(key=_col_sort_key)

    # ── 抽出結果プレビュー ──
    st.subheader("⑤ 抽出結果")
    headers = [f"{col}列 ({columns[col]})" for col in sorted_cols]
    preview_rows = []
    for r in records:
        row = dict(zip(headers, r["row_data"]))
        row["画像ファイル"] = r["file_name"]
        preview_rows.append(row)
    st.dataframe(preview_rows, use_container_width=True)

    # ── スプレッドシートへの書き込み ──
    with st.spinner(f"スプレッドシートに {len(records)} 行を書き込み中..."):
        try:
            # タイトル行がなかった場合は先頭に挿入
            if not has_sheet_titles:
                title_row = [columns[col] for col in sorted_cols]
                insert_title_row(title_row)
                st.info(f"タイトル行を追加しました: {title_row}")

            for r in records:
                append_to_sheet(r["row_data"])

            st.success(f"{len(records)} 件をスプレッドシートへ追記しました！")
        except SheetsConnectionError as e:
            st.error(str(e))
