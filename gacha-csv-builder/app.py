#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
みんなのトレカ ガチャ登録CSVビルダー（共有Webツール / Streamlit）
=================================================================
メンバー全員が使える共有ツール。ローカル不要・URL共有で動く。

流れ:
  ① ガチャ設計シート(.xlsx/.csv)をアップロード
  ② 確定分は自動でCSVへ / 同名で絵柄が複数ある賞はサムネを見て1枚選ぶ /
     保管庫に無い賞は画像URLかパレットを指定
  ③ 完成した管理画面インポートCSV(A〜L)をダウンロード

照合ロジックは build_import_csv.py を共有（CLIと同一）。選んだ結果は設計行に
注入して再計算するので、選ぶそばから「要選択」→「確定」に移る。
"""
import csv
import io
from pathlib import Path

import streamlit as st

import build_import_csv as B
import palette_lookup
from auth import check_password

HERE = Path(__file__).parent
MASTER_CSV = HERE / "master_db_dopa.csv"
PALETTE_CSVS = [HERE / "palette_pseudo.csv", HERE / "palette_extra.csv"]
LOGO = HERE / "assets" / "logo.png"
ICON = HERE / "assets" / "icon.png"

st.set_page_config(
    page_title="ガチャ登録CSVビルダー",
    page_icon=str(ICON) if ICON.exists() else None,
    layout="wide")

# 共通パスワード認証（Streamlit Secrets の app_password）。未設定ならスキップ=開発モード
if not check_password():
    st.stop()


@st.cache_data(show_spinner=False)
def load_master():
    return B.read_csv_dict(str(MASTER_CSV))


@st.cache_resource(show_spinner=False)
def load_palette():
    return palette_lookup.load_palette(*[str(p) for p in PALETTE_CSVS if p.exists()])


def parse_design(uploaded):
    """アップロードされた設計を design_rows(list[dict]) に変換。"""
    if uploaded.name.lower().endswith(".xlsx"):
        tmp = HERE / "_uploaded_design.xlsx"
        tmp.write_bytes(uploaded.getbuffer())
        try:
            return B.read_design_xlsx(str(tmp), "設計入力")
        finally:
            tmp.unlink(missing_ok=True)
    text = uploaded.getvalue().decode("utf-8-sig")
    return list(csv.DictReader(io.StringIO(text)))


def palette_options(palette):
    """パレット全キー → (ラベル, key, 画像URL) の一覧（未保管の手動割当用）。"""
    opts = []
    for key, r in palette.get("by_key", {}).items():
        label = f'{r.get("種別","")}｜{r.get("pt/種別詳細","") or key}'
        opts.append((label, key, r.get("画像URL", "")))
    return sorted(opts)


# ---------------- UI ----------------
if LOGO.exists():
    lc1, lc2 = st.columns([1, 3])
    with lc1:
        st.image(str(LOGO), use_container_width=True)
st.title("ガチャ登録CSVビルダー")
st.caption("設計シートをアップ → 画像を自動照合 → 迷う分だけ画像で選ぶ → 管理画面インポートCSVをダウンロード")

master_rows = load_master()
palette = load_palette()
pal_opts = palette_options(palette)

with st.sidebar:
    st.subheader("使い方")
    st.markdown(
        "1. ガチャ設計シート(.xlsx/.csv)をアップ\n"
        "2. **要選択**は候補画像から正しい絵柄を選ぶ\n"
        "3. **要追加**は画像URL or パレットを指定\n"
        "4. 下部の **CSVをダウンロード**\n\n"
        f"保管庫カード数: **{len(master_rows):,}**　演出パレット: **{len(pal_opts)}**"
    )
    st.info("『要選択』で選んだ絵柄の型番を設計シートの型番列に書いておくと、次回から自動確定します。")

uploaded = st.file_uploader("ガチャ設計シート（.xlsx / .csv）", type=["xlsx", "csv"])
if not uploaded:
    st.stop()

try:
    design_rows = parse_design(uploaded)
except Exception as e:
    st.error(f"設計シートの読み込みに失敗: {e}")
    st.stop()

if not design_rows:
    st.warning("賞品テーブルが空です。設計シートの内容を確認してください。")
    st.stop()

# セッションの選択状態（row番号→注入する値）
picks = st.session_state.setdefault("picks", {})       # row → 型番（要選択の確定）
manual = st.session_state.setdefault("manual", {})     # row → {"演出キー":..} or {"画像URL上書き":..}

# 選択を設計行に注入してから再計算（選ぶそばから確定に移る）
inject = [dict(d) for d in design_rows]
for i, d in enumerate(inject, start=2):
    if i in picks and picks[i]:
        d["型番"] = picks[i]
    if i in manual:
        d.update(manual[i])

out_rows, unmatched, warnings, ambiguous = B.build(
    master_rows, inject, B.DEFAULT_HEADERS, {}, palette)

c1, c2, c3 = st.columns(3)
c1.metric("確定（CSV出力）", len(out_rows))
c2.metric("要選択（画像で選ぶ）", len(ambiguous))
c3.metric("要追加（保管庫に無し）", len(unmatched))

st.divider()

# ===== 要選択：同名で絵柄が複数 =====
if ambiguous:
    st.subheader("画像を選ぶ（同名で絵柄が複数）")
    for a in ambiguous:
        row = a["row"]
        st.markdown(f"**{a['ランク']}　{a['設計上の名前']}**　"
                    f"<span style='color:#888'>還元{a['還元pt']}pt・{len(a['候補'])}候補</span>",
                    unsafe_allow_html=True)
        cols = st.columns(min(len(a["候補"]), 6))
        labels = []
        for idx, c in enumerate(a["候補"]):
            with cols[idx % len(cols)]:
                if c["画像URL"]:
                    st.image(c["画像URL"], use_container_width=True)
                st.caption(f'{c["型番"]}｜{c["レアリティ"]}')
            labels.append(f'{c["型番"]}｜{c["レアリティ"]}｜{c["カード名"]}')
        choice = st.radio(
            "この賞の絵柄を選択", ["（未選択）"] + labels,
            key=f"radio_{row}", horizontal=True, label_visibility="collapsed")
        if choice != "（未選択）":
            picks[row] = a["候補"][labels.index(choice)]["型番"]
        else:
            picks.pop(row, None)
        st.divider()

# ===== 要追加：保管庫に無い =====
if unmatched:
    st.subheader("保管庫に無い賞（画像を指定）")
    st.caption("画像URLを直接貼るか、演出パレットから選んでください。空欄のままだとCSVには出力されません。")
    pal_labels = ["（パレットから選ばない）"] + [o[0] for o in pal_opts]
    for u in unmatched:
        row = u["row"]
        st.markdown(f"**{u['設計上の名前']}**　<span style='color:#888'>{u['種別']}</span>",
                    unsafe_allow_html=True)
        cc1, cc2 = st.columns([3, 2])
        url = cc1.text_input("画像URL", key=f"url_{row}",
                             value=manual.get(row, {}).get("画像URL上書き", ""),
                             placeholder="https://minnano-toreka.com/wp-content/uploads/…")
        sel = cc2.selectbox("または演出パレット", pal_labels, key=f"pal_{row}")
        entry = {}
        if url.strip():
            entry["画像URL上書き"] = url.strip()
        elif sel != "（パレットから選ばない）":
            entry["演出キー"] = pal_opts[pal_labels.index(sel) - 1][1]
            preview = pal_opts[pal_labels.index(sel) - 1][2]
            if preview:
                cc2.image(preview, width=90)
        if entry:
            manual[row] = entry
        else:
            manual.pop(row, None)
        st.divider()

# ===== 確定プレビュー & ダウンロード =====
st.subheader("確定してCSVに出力される賞")
if out_rows:
    # サムネ付きプレビュー（Title / Redeem / 画像 / Rank）
    view = []
    for r in out_rows:
        view.append({"ランク": r[10], "カード名": r[1], "還元pt": r[4],
                     "在庫": r[7], "画像": r[5]})
    st.dataframe(
        view, use_container_width=True, hide_index=True,
        column_config={"画像": st.column_config.ImageColumn("画像", width="small")})
else:
    st.info("まだ確定した賞がありません。上で画像を選ぶ／指定すると増えていきます。")

buf = io.StringIO()
w = csv.writer(buf)
w.writerow(B.DEFAULT_HEADERS)
w.writerows(out_rows)
csv_bytes = ("﻿" + buf.getvalue()).encode("utf-8")  # BOM付き（管理画面/Excel互換）

fname = Path(uploaded.name).stem + "_import.csv"
st.download_button(
    f"管理画面インポートCSVをダウンロード（{len(out_rows)}件）",
    data=csv_bytes, file_name=fname, mime="text/csv",
    type="primary", disabled=(len(out_rows) == 0))

remaining = len(ambiguous) + len(unmatched)
if remaining:
    st.warning(f"未解決 {remaining}件（要選択{len(ambiguous)}・要追加{len(unmatched)}）は"
               f"まだCSVに含まれていません。上で解決すると自動で追加されます。")
else:
    st.success("全賞が確定しました。CSVをダウンロードして管理画面にインポートしてください。")

if warnings:
    with st.expander(f"警告 {len(warnings)}件（未入力の還元pt/在庫など）"):
        for wmsg in warnings:
            st.text("・" + wmsg)
