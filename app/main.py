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
import psutil

import re

from app import config
from app.parser import parse_format, build_row
from app.llm import extract_data, extract_data_freeform, detect_document_tab, LLMConnectionError, LLMParseError
from app.sheets import append_to_sheet, insert_title_row, get_first_row, get_worksheet_titles, SheetsConnectionError


def _extract_spreadsheet_id(url_or_id: str) -> str:
    """スプレッドシートのURLまたはIDからIDを抽出する。
    URLの場合は /d/<ID>/ の部分を、IDのみの場合はそのまま返す。
    """
    url_or_id = url_or_id.strip()
    match = re.search(r"/spreadsheets/d/([a-zA-Z0-9_-]+)", url_or_id)
    if match:
        return match.group(1)
    return url_or_id


@st.cache_data(show_spinner=False)
def _cached_extract_data(image_bytes: bytes, fields: tuple[str, ...], model: str, read_timeout: int) -> tuple[list[dict], dict]:
    """LLM抽出結果をメモリキャッシュ（同一画像×同一フィールド×同一モデルは再計算しない）"""
    return extract_data(image_bytes, list(fields), model=model, read_timeout=read_timeout)


@st.cache_data(show_spinner=False)
def _cached_extract_freeform(image_bytes: bytes, model: str, read_timeout: int) -> tuple[list[dict], dict]:
    """フリーフォームLLM抽出結果をメモリキャッシュ"""
    return extract_data_freeform(image_bytes, model=model, read_timeout=read_timeout)


def _get_exif_datetime(image_bytes: bytes) -> datetime | None:
    """JPEG EXIFから撮影日時を取得する。取得できない場合はNoneを返す"""
    try:
        img = Image.open(io.BytesIO(image_bytes))
        exif_data = img.getexif()
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


def _save_current_settings() -> None:
    """session_state の現在値をユーザー設定ファイルに保存する"""
    config.save_user_settings({
        "spreadsheet_id": st.session_state.get("cfg_spreadsheet_id", config.SPREADSHEET_ID),
        "ollama_model": st.session_state.get("cfg_ollama_model", config.OLLAMA_MODEL),
        "read_timeout": int(st.session_state.get("cfg_read_timeout", config.OLLAMA_READ_TIMEOUT)),
        "max_workers": int(st.session_state.get("cfg_max_workers", config.LLM_MAX_WORKERS)),
        "skip_preview": st.session_state.get("cfg_skip_preview", True),
        "custom_presets": st.session_state.get("cfg_custom_presets", {}),
    })


# ページ設定（スマホ操作を優先したレイアウト）
st.set_page_config(
    page_title="SnapSpread",
    page_icon="📷",
    layout="centered",
    initial_sidebar_state="collapsed",
)

# 初回のみ設定ファイルから読み込んでsession_stateを初期化
if "_settings_loaded" not in st.session_state:
    _saved = config.load_user_settings()
    st.session_state["cfg_spreadsheet_id"] = _saved.get("spreadsheet_id", config.SPREADSHEET_ID)
    st.session_state["cfg_ollama_model"] = _saved.get("ollama_model", config.OLLAMA_MODEL)
    st.session_state["cfg_read_timeout"] = _saved.get("read_timeout", config.OLLAMA_READ_TIMEOUT)
    st.session_state["cfg_max_workers"] = _saved.get("max_workers", config.LLM_MAX_WORKERS)
    st.session_state["cfg_skip_preview"] = _saved.get("skip_preview", True)
    st.session_state["cfg_custom_presets"] = _saved.get("custom_presets", {})
    st.session_state["_settings_loaded"] = True

_skip_preview: bool = st.session_state.get("cfg_skip_preview", True)

# ───────────────────────────────────────
# ヘッダー（タイトル + 右上の設定ボタン）
# ───────────────────────────────────────
_title_col, _gear_col = st.columns([9, 1])
with _title_col:
    st.title("📷 SnapSpread")
    st.caption("帳票を撮影してGoogleスプレッドシートに自動入力")
with _gear_col:
    st.write("")
    st.write("")
    with st.popover("⚙️", use_container_width=True):
        st.subheader("⚙️ 設定")

        st.markdown("**LLMモデル**")
        st.text_input("Ollamaモデル", key="cfg_ollama_model",
                      help="例: qwen2.5vl:7b, llava:13b")
        st.number_input("タイムアウト（秒）", min_value=30, max_value=3600, step=30,
                        key="cfg_read_timeout",
                        help="LLMの応答待ち最大時間。画像枚数が多い場合は大きくしてください")

        st.markdown("**処理**")
        st.slider("並列処理数", min_value=1, max_value=10, key="cfg_max_workers",
                  help="同時に処理する画像数。多いほど高速ですがメモリを消費します")
        st.checkbox("プレビューを省略して即スプレッドシートに書き込む",
                    key="cfg_skip_preview",
                    help="ONにすると抽出後すぐに書き込みます。OFFにすると確認ボタンが表示されます")

        st.divider()

        st.markdown("**プリセット管理**")
        _custom_presets: dict = st.session_state.get("cfg_custom_presets", {})
        if _custom_presets:
            _del_preset = st.selectbox(
                "削除するプリセット",
                ["（選択してください）"] + list(_custom_presets.keys()),
                key="preset_to_delete",
            )
            if st.button("選択したプリセットを削除", use_container_width=True):
                if _del_preset != "（選択してください）":
                    _custom_presets.pop(_del_preset, None)
                    st.session_state["cfg_custom_presets"] = _custom_presets
                    # ウィジェットのキーを削除することでrerun後にデフォルト値にリセットされる
                    del st.session_state["preset_to_delete"]
                    st.rerun()
        else:
            st.caption("カスタムプリセットはありません")

        st.divider()

        if st.button("💾 設定を保存", use_container_width=True, type="primary"):
            _save_current_settings()
            st.rerun()

# ───────────────────────────────────────
# スプレッドシートURL入力
# ───────────────────────────────────────
st.text_input(
    "スプレッドシートURL",
    key="cfg_spreadsheet_id",
    placeholder="例: https://docs.google.com/spreadsheets/d/1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgVE2upms/edit",
    help="GoogleスプレッドシートのURLを貼り付けてください（IDのみの入力も可）",
)

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
    "例: A列: 品名, B列: 数量, C列: 単価, D列: 金額  \n"
    "明細が複数行ある場合はそれぞれ1行として登録されます。  \n"
    "※ スプレッドシートにタイトル行が設定済みの場合は空欄でも自動読み込みします。  \n"
    "※ 複数タブがある場合は帳票の種類に合わせて自動振り分けします。"
)

if "format_text" not in st.session_state:
    st.session_state["format_text"] = ""

# プリセット保存成功メッセージ
if "_preset_saved_name" in st.session_state:
    st.success(f"プリセット「{st.session_state.pop('_preset_saved_name')}」を保存しました")

# プリセット選択
_all_presets = {
    "（なし）": "",
    **config.DEFAULT_FORMAT_PRESETS,
    **st.session_state.get("cfg_custom_presets", {}),
}
_preset_sel_col, _preset_apply_col, _preset_save_name_col, _preset_save_col = st.columns([3, 1, 2, 1])
with _preset_sel_col:
    _selected_preset = st.selectbox("プリセット", list(_all_presets.keys()),
                                    label_visibility="collapsed", key="selected_preset")
with _preset_apply_col:
    if st.button("適用", use_container_width=True):
        st.session_state["format_text"] = _all_presets[_selected_preset]
        st.rerun()
with _preset_save_name_col:
    _preset_save_name = st.text_input("プリセット名", placeholder="名前を入力して保存",
                                      label_visibility="collapsed", key="preset_save_name")
with _preset_save_col:
    if st.button("保存", use_container_width=True, key="btn_save_preset"):
        _current_format = st.session_state.get("format_text", "").strip()
        if not _preset_save_name:
            st.warning("プリセット名を入力してください")
        elif not _current_format:
            st.warning("フォーマットを入力してからプリセットを保存してください")
        else:
            _custom = st.session_state.get("cfg_custom_presets", {})
            _custom[_preset_save_name] = _current_format
            st.session_state["cfg_custom_presets"] = _custom
            _save_current_settings()
            st.session_state["_preset_saved_name"] = _preset_save_name
            st.rerun()

# 列範囲ドロップダウンでテンプレートを生成
_col_letters = [chr(65 + i) for i in range(26)]  # A〜Z
_range_col1, _range_col2, _range_col3 = st.columns([2, 2, 3])
with _range_col1:
    _start_col = st.selectbox("開始列", _col_letters, index=0, key="start_col")
with _range_col2:
    _start_idx = _col_letters.index(_start_col)
    _end_col = st.selectbox("終了列", _col_letters, index=_start_idx, key="end_col")
with _range_col3:
    st.write("")  # ラベル分の余白を合わせる
    if st.button("テンプレートを生成", use_container_width=True):
        _end_idx = _col_letters.index(_end_col)
        if _end_idx >= _start_idx:
            st.session_state["format_text"] = "\n".join(
                f"{c}列:" for c in _col_letters[_start_idx:_end_idx + 1]
            )
        else:
            st.warning("終了列は開始列以降の列を選択してください。")

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


def _execute_write(state: dict) -> None:
    """スプレッドシートへの書き込みを実行する（SheetsConnectionError を呼び出し元へスロー）"""
    records = state["records"]
    columns = state["columns"]
    sorted_cols = state["sorted_cols"]
    multi_tab_mode = state["multi_tab_mode"]
    has_sheet_titles = state["has_sheet_titles"]
    worksheet_titles = state["worksheet_titles"]
    spreadsheet_id = state["spreadsheet_id"]

    if multi_tab_mode:
        tab_write_groups: dict[str, list] = {}
        for r in records:
            tab = r.get("target_tab") or worksheet_titles[0]
            tab_write_groups.setdefault(tab, []).append(r)

        for tab_name, tab_recs in tab_write_groups.items():
            try:
                tab_first_row = get_first_row(tab_name, spreadsheet_id)
            except SheetsConnectionError:
                tab_first_row = []
            has_tab_titles = any(v.strip() for v in tab_first_row)

            if not has_tab_titles:
                fi = tab_recs[0].get("tab_format_info")
                if fi:
                    _tab_sorted = sorted(fi["columns"].keys(), key=lambda c: (len(c), c))
                    title_row = [fi["columns"][col] for col in _tab_sorted]
                else:
                    dyn_cols = tab_recs[0].get("dynamic_columns", {})
                    _dyn_sorted = sorted(dyn_cols.keys(), key=lambda c: (len(c), c))
                    title_row = [dyn_cols[col] for col in _dyn_sorted]
                if title_row:
                    insert_title_row(title_row, tab_name, spreadsheet_id)
                    st.info(f"**{tab_name}** にタイトル行を追加しました。")

            for r in tab_recs:
                append_to_sheet(r["row_data"], tab_name, spreadsheet_id)
    else:
        if not has_sheet_titles:
            title_row = [columns[col] for col in sorted_cols]
            insert_title_row(title_row, spreadsheet_id=spreadsheet_id)
            st.info("タイトル行を追加しました:\n\n" + "  \n".join(title_row))

        for r in records:
            append_to_sheet(r["row_data"], spreadsheet_id=spreadsheet_id)

if run_button:
    # ── 画像バリデーション ──
    if not valid_files:
        st.error("画像をアップロードしてください。")
        st.stop()

    # セッションの設定値を取得
    _spreadsheet_id: str = _extract_spreadsheet_id(
        st.session_state.get("cfg_spreadsheet_id") or config.SPREADSHEET_ID
    )
    _ollama_model: str = st.session_state.get("cfg_ollama_model") or config.OLLAMA_MODEL
    _read_timeout: int = int(st.session_state.get("cfg_read_timeout") or config.OLLAMA_READ_TIMEOUT)
    _max_workers: int = int(st.session_state.get("cfg_max_workers") or config.LLM_MAX_WORKERS)

    # ── スプレッドシートのタブ情報を確認 ──
    with st.spinner("スプレッドシートの情報を確認中..."):
        try:
            worksheet_titles = get_worksheet_titles(_spreadsheet_id)
        except SheetsConnectionError as e:
            st.error(str(e))
            st.stop()

    # デフォルトシート（最初のタブ）のタイトル行を取得
    try:
        sheet_first_row = get_first_row(spreadsheet_id=_spreadsheet_id)
    except SheetsConnectionError as e:
        st.error(str(e))
        st.stop()

    sheet_titles = [(i, v) for i, v in enumerate(sheet_first_row) if v.strip()]
    has_sheet_titles = len(sheet_titles) > 0

    # 複数タブ かつ フォーマット未入力の場合: タブ自動振り分けモード
    multi_tab_mode = len(worksheet_titles) > 1 and not format_text.strip()

    # ── フォーマットの決定 ──
    freeform_mode = False
    format_info = None
    llm_fields: list[str] = []
    tab_formats: dict[str, dict | None] = {}  # multi_tab_mode用: タブ名 → フォーマット情報

    if format_text.strip():
        try:
            format_info = parse_format(format_text)
        except ValueError as e:
            st.error(str(e))
            st.stop()
        llm_fields = format_info["llm_fields"]
    elif multi_tab_mode:
        # 複数タブ自動振り分けモード: 全タブのフォーマットを事前取得
        st.info(
            f"スプレッドシートに **{len(worksheet_titles)}** 個のタブが検出されました: "
            + "、".join(f"「{t}」" for t in worksheet_titles)
            + "  \n帳票の種類に合わせて自動振り分けします。"
        )
        with st.spinner("各タブのフォーマットを確認中..."):
            for tab_title in worksheet_titles:
                try:
                    first_row = get_first_row(tab_title, _spreadsheet_id)
                    titles_in_tab = [(i, v) for i, v in enumerate(first_row) if v.strip()]
                    if titles_in_tab:
                        use_format_text = ", ".join(
                            f"{_idx_to_col(i)}列: {v}" for i, v in titles_in_tab
                        )
                        tab_formats[tab_title] = parse_format(use_format_text)
                    else:
                        tab_formats[tab_title] = None  # タイトル行なし→フリーフォーム
                except (SheetsConnectionError, ValueError):
                    tab_formats[tab_title] = None
    elif has_sheet_titles:
        use_format_text = ", ".join(
            f"{_idx_to_col(i)}列: {v}" for i, v in sheet_titles
        )
        detected_cols = "  \n".join(
            f"{_idx_to_col(i)}列: {v}" for i, v in sheet_titles
        )
        st.info(f"スプレッドシートのタイトルからフォーマットを自動検出しました:\n\n{detected_cols}")
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
    records: list[dict] = []
    total = len(valid_files)
    progress = st.progress(0, text="画像を解析中...")

    # ── リアルタイムメトリクス（経過時間・CPU・tokens/s） ──
    _start_time = time.time()
    _metrics_placeholder = st.empty()
    _stop_event = threading.Event()
    _metrics_lock = threading.Lock()
    _live_metrics = {"latest_tps": 0.0, "tps_list": []}

    # CPU使用率の初回呼び出し（0を返すダミー呼び出しで初期化）
    psutil.cpu_percent(interval=None)

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
            cpu = psutil.cpu_percent(interval=None)
            with _metrics_lock:
                latest_tps = _live_metrics["latest_tps"]
            try:
                tps_str = f"**{latest_tps:.1f} tok/s**" if latest_tps > 0 else "推論中..."
                _metrics_placeholder.markdown(
                    f"⏱ **{mins:02d}:{secs:02d}** &nbsp;|&nbsp; 💻 CPU: **{cpu:.0f}%** &nbsp;|&nbsp; ⚡ {tps_str}"
                )
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
        target_tab = None
        rec_format_info = None

        if multi_tab_mode:
            # タブ自動判定: LLMが画像の帳票種類と一致するタブを選択
            target_tab = detect_document_tab(image_bytes, worksheet_titles, model=_ollama_model, read_timeout=_read_timeout)
            if target_tab and tab_formats.get(target_tab):
                rec_format_info = tab_formats[target_tab]
                llm_results, metrics = _cached_extract_data(
                    image_bytes, tuple(rec_format_info["llm_fields"]), _ollama_model, _read_timeout
                )
            else:
                # タブ判定失敗 or タブにタイトル行なし → フリーフォーム
                llm_results, metrics = _cached_extract_freeform(image_bytes, _ollama_model, _read_timeout)
        elif freeform_mode:
            llm_results, metrics = _cached_extract_freeform(image_bytes, _ollama_model, _read_timeout)
        else:
            llm_results, metrics = _cached_extract_data(image_bytes, tuple(llm_fields), _ollama_model, _read_timeout)

        return file_name, exif_dt, llm_results, metrics, target_tab, rec_format_info

    completed_count = 0
    errors: list[str] = []

    with ThreadPoolExecutor(max_workers=_max_workers) as executor:
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
                file_name, exif_dt, llm_results, metrics, target_tab, rec_format_info = future.result()
                with _metrics_lock:
                    _live_metrics["latest_tps"] = metrics["tokens_per_sec"]
                    _live_metrics["tps_list"].append(metrics["tokens_per_sec"])
                for llm_result in llm_results:
                    if multi_tab_mode:
                        rec = {
                            "file_name": file_name,
                            "exif_dt": exif_dt,
                            "llm_result": llm_result,
                            "target_tab": target_tab,
                            "tab_format_info": rec_format_info,
                        }
                        if rec_format_info:
                            rec["row_data"] = build_row(llm_result, rec_format_info)
                        records.append(rec)
                    elif freeform_mode:
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

    # ── タイマー停止・最終メトリクスを表示 ──
    _stop_event.set()
    _timer_thread.join(timeout=1)
    _elapsed_total = time.time() - _start_time
    _mins_total, _secs_total = divmod(int(_elapsed_total), 60)
    with _metrics_lock:
        _tps_list = _live_metrics["tps_list"]
    _avg_tps = sum(_tps_list) / len(_tps_list) if _tps_list else 0.0
    _tps_summary = f" &nbsp;|&nbsp; ⚡ 平均 **{_avg_tps:.1f} tok/s**" if _avg_tps > 0 else ""
    _metrics_placeholder.markdown(
        f"✅ 解析完了: **{_mins_total:02d}:{_secs_total:02d}**{_tps_summary}"
    )

    # ── エラーがあれば表示して中断 ──
    if errors:
        for err in errors:
            st.error(err)
        st.stop()

    # ── 後処理: row_data の確定 ──
    if multi_tab_mode:
        # タブフォーマットなし（タブ判定失敗 or タイトル行なし）のレコードを処理
        tab_no_format_recs: dict[str, list] = {}
        for r in records:
            if "row_data" not in r:
                tab = r.get("target_tab") or worksheet_titles[0]
                r["target_tab"] = tab  # Noneの場合は先頭タブに割り当て
                tab_no_format_recs.setdefault(tab, []).append(r)

        for tab_name, recs in tab_no_format_recs.items():
            all_keys: list[str] = []
            for r in recs:
                for k in r["llm_result"].keys():
                    if k not in all_keys:
                        all_keys.append(k)
            dyn_columns = {_idx_to_col(i): k for i, k in enumerate(all_keys)}
            for r in recs:
                r["row_data"] = [str(r["llm_result"].get(k, "")) for k in all_keys]
                r["dynamic_columns"] = dyn_columns

    elif freeform_mode:
        # フリーフォームモード: 全レコードのキーを収集して列を動的生成
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

    # ── 並び替え（multi_tab_mode以外） ──
    if not multi_tab_mode:
        sorted_cols = sorted(columns.keys(), key=lambda c: (len(c), c))

        if sort_order == "撮影日順（EXIF）":
            no_exif = [r for r in records if r["exif_dt"] is None]
            if no_exif:
                names = ", ".join(dict.fromkeys(r["file_name"] for r in no_exif))
                st.warning(f"以下の画像にEXIF撮影日時がないため、末尾に配置します:\n\n{names}")
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

    # ── 抽出結果をsession_stateに保存（プレビュー確認フロー用）──
    _write_state = {
        "records": records,
        "columns": columns if not multi_tab_mode else {},
        "sorted_cols": sorted_cols if not multi_tab_mode else [],
        "multi_tab_mode": multi_tab_mode,
        "has_sheet_titles": has_sheet_titles,
        "worksheet_titles": worksheet_titles,
        "spreadsheet_id": _spreadsheet_id,
    }

    if _skip_preview:
        # プレビューをスキップして即時書き込み
        with st.spinner(f"スプレッドシートに {len(records)} 行を書き込み中..."):
            try:
                _execute_write(_write_state)
                st.success(f"{len(records)} 件をスプレッドシートへ追記しました！")
            except SheetsConnectionError as e:
                st.error(str(e))
    else:
        st.session_state["_pending_write"] = _write_state

# ───────────────────────────────────────
# ⑤ 抽出結果プレビュー＆書き込み確認
# ───────────────────────────────────────
if "write_success_msg" in st.session_state:
    st.success(st.session_state.pop("write_success_msg"))

if "_pending_write" in st.session_state:
    _pw = st.session_state["_pending_write"]
    _pw_records: list = _pw["records"]
    _pw_columns: dict = _pw["columns"]
    _pw_sorted_cols: list = _pw["sorted_cols"]
    _pw_multi_tab: bool = _pw["multi_tab_mode"]
    _pw_ws_titles: list = _pw["worksheet_titles"]

    st.subheader("⑤ 抽出結果")

    if _pw_multi_tab:
        tab_preview_groups: dict[str, list] = {}
        for r in _pw_records:
            tab = r.get("target_tab") or _pw_ws_titles[0]
            tab_preview_groups.setdefault(tab, []).append(r)

        for tab_name, tab_recs in tab_preview_groups.items():
            with st.expander(f"**{tab_name}** （{len(tab_recs)}件）", expanded=True):
                fi = tab_recs[0].get("tab_format_info")
                if fi:
                    tab_sorted_cols = sorted(fi["columns"].keys(), key=lambda c: (len(c), c))
                    headers = [f"{col}列 ({fi['columns'][col]})" for col in tab_sorted_cols]
                else:
                    dyn_cols = tab_recs[0].get("dynamic_columns", {})
                    tab_sorted_cols = sorted(dyn_cols.keys(), key=lambda c: (len(c), c))
                    headers = [f"{col}列 ({dyn_cols[col]})" for col in tab_sorted_cols]
                preview_rows = []
                for r in tab_recs:
                    row = dict(zip(headers, r["row_data"]))
                    row["画像ファイル"] = r["file_name"]
                    preview_rows.append(row)
                st.dataframe(preview_rows, use_container_width=True)
    else:
        headers = [f"{col}列 ({_pw_columns[col]})" for col in _pw_sorted_cols]
        preview_rows = []
        for r in _pw_records:
            row = dict(zip(headers, r["row_data"]))
            row["画像ファイル"] = r["file_name"]
            preview_rows.append(row)
        st.dataframe(preview_rows, use_container_width=True)

    _confirm_col, _cancel_col = st.columns(2)
    with _confirm_col:
        if st.button("✅ スプレッドシートに書き込む", type="primary", use_container_width=True):
            with st.spinner(f"スプレッドシートに {len(_pw_records)} 行を書き込み中..."):
                try:
                    _execute_write(_pw)
                    _n = len(_pw_records)
                    del st.session_state["_pending_write"]
                    st.session_state["write_success_msg"] = f"{_n} 件をスプレッドシートへ追記しました！"
                    st.rerun()
                except SheetsConnectionError as e:
                    st.error(str(e))
    with _cancel_col:
        if st.button("❌ 結果を破棄", use_container_width=True):
            del st.session_state["_pending_write"]
            st.rerun()
