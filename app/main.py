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

from app import config
from app.parser import parse_format, build_row
from app.llm import extract_data, extract_data_freeform, detect_document_tab, LLMConnectionError, LLMParseError
from app.sheets import append_to_sheet, insert_title_row, get_first_row, get_worksheet_titles, SheetsConnectionError


@st.cache_data(show_spinner=False)
def _cached_extract_data(image_bytes: bytes, fields: tuple[str, ...]) -> tuple[list[dict], dict]:
    """LLM抽出結果をメモリキャッシュ（同一画像×同一フィールドは再計算しない）"""
    return extract_data(image_bytes, list(fields))


@st.cache_data(show_spinner=False)
def _cached_extract_freeform(image_bytes: bytes) -> tuple[list[dict], dict]:
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
    "例: A列: 品名, B列: 数量, C列: 単価, D列: 金額  \n"
    "明細が複数行ある場合はそれぞれ1行として登録されます。  \n"
    "※ スプレッドシートにタイトル行が設定済みの場合は空欄でも自動読み込みします。  \n"
    "※ 複数タブがある場合は帳票の種類に合わせて自動振り分けします。"
)

if "format_text" not in st.session_state:
    st.session_state["format_text"] = ""

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

if run_button:
    # ── 画像バリデーション ──
    if not valid_files:
        st.error("画像をアップロードしてください。")
        st.stop()

    # ── スプレッドシートのタブ情報を確認 ──
    with st.spinner("スプレッドシートの情報を確認中..."):
        try:
            worksheet_titles = get_worksheet_titles()
        except SheetsConnectionError as e:
            st.error(str(e))
            st.stop()

    # デフォルトシート（最初のタブ）のタイトル行を取得
    try:
        sheet_first_row = get_first_row()
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
                    first_row = get_first_row(tab_title)
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
            target_tab = detect_document_tab(image_bytes, worksheet_titles)
            if target_tab and tab_formats.get(target_tab):
                rec_format_info = tab_formats[target_tab]
                llm_results, metrics = _cached_extract_data(
                    image_bytes, tuple(rec_format_info["llm_fields"])
                )
            else:
                # タブ判定失敗 or タブにタイトル行なし → フリーフォーム
                llm_results, metrics = _cached_extract_freeform(image_bytes)
        elif freeform_mode:
            llm_results, metrics = _cached_extract_freeform(image_bytes)
        else:
            llm_results, metrics = _cached_extract_data(image_bytes, tuple(llm_fields))

        return file_name, exif_dt, llm_results, metrics, target_tab, rec_format_info

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

    # ── 抽出結果プレビュー ──
    st.subheader("⑤ 抽出結果")

    if multi_tab_mode:
        # タブ別にグループ化して表示
        tab_preview_groups: dict[str, list] = {}
        for r in records:
            tab = r.get("target_tab") or worksheet_titles[0]
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
            if multi_tab_mode:
                # タブ別にグループ化して書き込み
                tab_write_groups: dict[str, list] = {}
                for r in records:
                    tab = r.get("target_tab") or worksheet_titles[0]
                    tab_write_groups.setdefault(tab, []).append(r)

                for tab_name, tab_recs in tab_write_groups.items():
                    # タブのタイトル行が空の場合は挿入
                    try:
                        tab_first_row = get_first_row(tab_name)
                    except SheetsConnectionError:
                        tab_first_row = []
                    has_tab_titles = any(v.strip() for v in tab_first_row)

                    if not has_tab_titles:
                        fi = tab_recs[0].get("tab_format_info")
                        if fi:
                            tab_sorted_cols = sorted(fi["columns"].keys(), key=lambda c: (len(c), c))
                            title_row = [fi["columns"][col] for col in tab_sorted_cols]
                        else:
                            dyn_cols = tab_recs[0].get("dynamic_columns", {})
                            dyn_sorted_cols = sorted(dyn_cols.keys(), key=lambda c: (len(c), c))
                            title_row = [dyn_cols[col] for col in dyn_sorted_cols]
                        if title_row:
                            insert_title_row(title_row, tab_name)
                            st.info(f"**{tab_name}** にタイトル行を追加しました。")

                    for r in tab_recs:
                        append_to_sheet(r["row_data"], tab_name)
            else:
                # タイトル行がなかった場合は先頭に挿入
                if not has_sheet_titles:
                    title_row = [columns[col] for col in sorted_cols]
                    insert_title_row(title_row)
                    st.info("タイトル行を追加しました:\n\n" + "  \n".join(title_row))

                for r in records:
                    append_to_sheet(r["row_data"])

            st.success(f"{len(records)} 件をスプレッドシートへ追記しました！")
        except SheetsConnectionError as e:
            st.error(str(e))
