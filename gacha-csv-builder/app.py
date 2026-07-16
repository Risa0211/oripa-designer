#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
みんなのトレカ ガチャ登録CSVビルダー（共有Webツール / Streamlit）
=================================================================
3つのタブ:
  1. ガチャCSV作成 … 設計シート→自動照合→要選択はサムネで選ぶ→A〜L CSV
                    「保管庫に無し」は管理画面を検索して1クリックで移行(使う分だけ)
  2. 保管庫を見る   … 保管庫(自社WP)の画像を名前で部分一致検索して閲覧
  3. 画像を追加     … 新しい画像を保管庫にアップロード（追加のみ）

★安全設計: このツールは「足す・見る」だけ。削除/上書きの機能は持たない(wp_client参照)。
  書き込み認証(WP_USER/WP_APP_PASS)はサーバー側Secretsのみ・画面に出さない。
"""
import csv
import io
import os
from pathlib import Path

import streamlit as st

import build_import_csv as B
import palette_lookup
import storehouse as SH
import wp_client as WP

HERE = Path(__file__).parent
MASTER_CSV = HERE / "master_db_dopa.csv"
ADMIN_CSV = HERE / "card_db_export.csv"
PALETTE_CSVS = [HERE / "palette_pseudo.csv", HERE / "palette_extra.csv"]
LOGO = HERE / "assets" / "logo.png"
ICON = HERE / "assets" / "icon.png"

st.set_page_config(
    page_title="ガチャ登録CSVビルダー",
    page_icon=str(ICON) if ICON.exists() else None,
    layout="wide")


def wp_creds():
    """書き込み認証。Streamlit Secrets → 環境変数の順。無ければ('','')＝閲覧のみ。"""
    u = p = ""
    try:
        u = str(st.secrets.get("WP_USER", "")) or os.environ.get("WP_USER", "")
        p = str(st.secrets.get("WP_APP_PASS", "")) or os.environ.get("WP_APP_PASS", "")
    except Exception:
        u = os.environ.get("WP_USER", ""); p = os.environ.get("WP_APP_PASS", "")
    return u, p


@st.cache_data(show_spinner=False)
def load_master():
    return B.read_csv_dict(str(MASTER_CSV))


@st.cache_data(show_spinner="管理画面ダンプ読込中…")
def load_admin():
    return SH.load_admin(str(ADMIN_CSV))


@st.cache_resource(show_spinner=False)
def load_palette():
    return palette_lookup.load_palette(*[str(p) for p in PALETTE_CSVS if p.exists()])


def parse_design(uploaded):
    if uploaded.name.lower().endswith(".xlsx"):
        tmp = HERE / "_uploaded_design.xlsx"
        tmp.write_bytes(uploaded.getbuffer())
        try:
            return B.read_design_xlsx(str(tmp), "設計入力")
        finally:
            tmp.unlink(missing_ok=True)
    return list(csv.DictReader(io.StringIO(uploaded.getvalue().decode("utf-8-sig"))))


def palette_options(palette):
    opts = []
    for key, r in palette.get("by_key", {}).items():
        opts.append((f'{r.get("種別","")}｜{r.get("pt/種別詳細","") or key}', key, r.get("画像URL", "")))
    return sorted(opts)


# ---- 共有リソース ----
master_rows = load_master()
palette = load_palette()
pal_opts = palette_options(palette)
WP_USER, WP_PASS = wp_creds()
can_write = bool(WP_USER and WP_PASS)

# セッション状態
st.session_state.setdefault("picks", {})       # row → 型番（要選択の確定）
st.session_state.setdefault("manual", {})       # row → {"画像URL上書き"/"演出キー": ...}
st.session_state.setdefault("migrated", {})     # s3_url → wp_url（移行の重複防止）

# ---- 画像の全画面ボタンを無効化（クリックで拡大→戻れない問題の対策）----
st.markdown("""<style>
button[title="View fullscreen"], button[title="全画面表示"] { display: none !important; }
[data-testid="StyledFullScreenButton"] { display: none !important; }
div[data-testid="stImage"] img { border-radius: 6px; }
</style>""", unsafe_allow_html=True)

# ---- ヘッダ ----
if LOGO.exists():
    lc1, _ = st.columns([1, 3])
    with lc1:
        st.image(str(LOGO), use_container_width=True)
st.title("ガチャ登録CSVビルダー")
with st.sidebar:
    st.subheader("状態")
    st.markdown(f"保管庫(DOPA): **{len(master_rows):,}**　演出パレット: **{len(pal_opts)}**")
    st.markdown(("画像の追加/移行: **有効**" if can_write
                 else "画像の追加/移行: **停止中**（Secretsに WP_USER / WP_APP_PASS を設定すると有効）"))
    st.caption("このツールは『足す・見る』のみ。削除・上書きはできません。")


def resolve_image_url(row, src_url, filename, title):
    """選ばれた画像URLを保管庫URLに確定する。
    - 既に保管庫(WP)のURLならそのまま
    - 業者倉庫(S3)等ならWPへ移行(追加)して保管庫URLに。認証無しなら元URLをそのまま使う。"""
    if src_url.startswith(WP.WP_BASE):
        return src_url
    if src_url in st.session_state["migrated"]:
        return st.session_state["migrated"][src_url]
    if can_write:
        try:
            _, wp_url = WP.migrate_from_url(src_url, filename, title,
                                            user=WP_USER, app_pass=WP_PASS)
            st.session_state["migrated"][src_url] = wp_url
            return wp_url
        except Exception as e:
            st.warning(f"移行に失敗（元URLをそのまま使用）: {e}")
            return src_url
    return src_url  # 認証無し=閲覧のみ→元URLを直接使う（後で移行可）


def img_tag(url, radius=6):
    """全画面ボタンの付かないHTML画像（st.imageの拡大トラップを回避）。"""
    if not url:
        return ""
    return (f'<img src="{url}" loading="lazy" '
            f'style="width:100%;border-radius:{radius}px;display:block;'
            f'border:1px solid #eee">')


def show_img(url):
    st.markdown(img_tag(url), unsafe_allow_html=True)


tab_make, tab_view, tab_add = st.tabs(["① ガチャCSV作成", "② 保管庫を見る", "③ 画像を追加"])

# ============================================================ ① ガチャCSV作成
def render_make(uploaded):
    """設計シートアップ後の本体（tab内で呼ぶ。st.stopは使わずreturnで抜ける）。"""
    try:
        design_rows = parse_design(uploaded)
    except Exception as e:
        st.error(f"設計シートの読み込みに失敗: {e}")
        return
    if not design_rows:
        st.warning("賞品テーブルが空です。")
        return

    picks, manual = st.session_state["picks"], st.session_state["manual"]

    # 選択・手動指定を設計行に注入して再計算
    inject = [dict(d) for d in design_rows]
    for i, d in enumerate(inject, start=2):
        if picks.get(i):
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

    # ---- 要選択：同名で絵柄が複数 ----
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
                        show_img(c["画像URL"])
                    st.caption(f'{c["型番"]}｜{c["レアリティ"]}')
                labels.append(f'{c["型番"]}｜{c["レアリティ"]}｜{c["カード名"]}')
            choice = st.radio("絵柄を選択", ["（未選択）"] + labels,
                              key=f"radio_{row}", horizontal=True, label_visibility="collapsed")
            if choice != "（未選択）":
                picks[row] = a["候補"][labels.index(choice)]["型番"]
            else:
                picks.pop(row, None)
            st.divider()

    # ---- 要追加：保管庫に無し → 管理画面を検索して使う分だけ移行 ----
    if unmatched:
        st.subheader("保管庫に無い賞（管理画面から探して1クリックで移行）")
        st.caption("既定で賞品名を検索します。正しい画像の『これを使う』を押すと、その1枚だけ保管庫にコピーしてCSVに入ります。")
        admin_rows = load_admin()
        pal_labels = ["（パレットから選ばない）"] + [o[0] for o in pal_opts]
        for u in unmatched:
            row = u["row"]
            name = u["設計上の名前"]
            st.markdown(f"**{name}**　<span style='color:#888'>{u['種別']}</span>",
                        unsafe_allow_html=True)
            q = st.text_input("検索ワード（部分一致）", value=name, key=f"q_{row}")
            hits = SH.search_dopa(master_rows, q, limit=8) + SH.search_admin(admin_rows, q, limit=16)
            if hits:
                cols = st.columns(6)
                for idx, h in enumerate(hits):
                    with cols[idx % 6]:
                        if h["image_url"]:
                            show_img(h["image_url"])
                        src = "保管庫" if h["image_url"].startswith(WP.WP_BASE) else "管理画面"
                        st.caption(f'{h["title"][:22]}\n［{src}］')
                        if st.button("これを使う", key=f"use_{row}_{idx}"):
                            fn = SH.san_filename(h.get("kata", ""), name, f"a{idx}",
                                                 ext=os.path.splitext(h["image_url"])[1] or ".png")
                            url = resolve_image_url(row, h["image_url"], fn, name)
                            manual[row] = {"画像URL上書き": url}
                            st.rerun()
            else:
                st.caption("該当なし。検索ワードを短くするか、下で画像URL/パレットを指定してください。")
            with st.expander("手動で指定（画像URL / 演出パレット）"):
                mu = st.text_input("画像URLを直接指定", key=f"url_{row}",
                                   value=manual.get(row, {}).get("画像URL上書き", ""))
                sel = st.selectbox("または演出パレット", pal_labels, key=f"pal_{row}")
                if mu.strip():
                    manual[row] = {"画像URL上書き": mu.strip()}
                elif sel != "（パレットから選ばない）":
                    manual[row] = {"演出キー": pal_opts[pal_labels.index(sel) - 1][1]}
            st.divider()

    # ---- 確定プレビュー & ダウンロード ----
    st.subheader("確定してCSVに出力される賞")
    if out_rows:
        view = [{"ランク": r[10], "カード名": r[1], "還元pt": r[4], "在庫": r[7], "画像": r[5]}
                for r in out_rows]
        st.dataframe(view, use_container_width=True, hide_index=True,
                     column_config={"画像": st.column_config.ImageColumn("画像", width="small")})
    else:
        st.info("まだ確定した賞がありません。上で画像を選ぶ／指定すると増えます。")

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(B.DEFAULT_HEADERS)
    w.writerows(out_rows)
    csv_bytes = ("﻿" + buf.getvalue()).encode("utf-8")
    st.download_button(f"管理画面インポートCSVをダウンロード（{len(out_rows)}件）",
                       data=csv_bytes, file_name=Path(uploaded.name).stem + "_import.csv",
                       mime="text/csv", type="primary", disabled=(len(out_rows) == 0))

    remaining = len(ambiguous) + len(unmatched)
    if remaining:
        st.warning(f"未解決 {remaining}件（要選択{len(ambiguous)}・要追加{len(unmatched)}）はまだCSVに含まれていません。")
    else:
        st.success("全賞が確定しました。CSVをダウンロードして管理画面にインポートしてください。")
    if warnings:
        with st.expander(f"警告 {len(warnings)}件（未入力の還元pt/在庫など）"):
            for wmsg in warnings:
                st.text("・" + wmsg)


with tab_make:
    st.caption("設計シートをアップ → 自動照合 → 迷う分だけ画像で選ぶ → 管理画面インポートCSV")
    uploaded = st.file_uploader("ガチャ設計シート（.xlsx / .csv）", type=["xlsx", "csv"])
    if not uploaded:
        st.info("設計シートをアップロードすると照合が始まります。")
    else:
        render_make(uploaded)

# ============================================================ ② 保管庫を見る
with tab_view:
    st.caption("保管庫の画像を名前・型番で部分一致検索。各カードの下でカード名・型番・レアをコピーできます。")
    q = st.text_input("検索ワード（カード名 or 型番の一部）", key="view_q",
                      placeholder="例: リザードン / 066/060 / PSA10")
    if q:
        hits = SH.search_dopa(master_rows, q, limit=48)
        # 追加された新しい画像も拾えるよう保管庫(WP)を直接検索してマージ
        for m in WP.search_media(q, user=WP_USER or None, app_pass=WP_PASS or None, per_page=30):
            if m["url"] and not any(h["image_url"] == m["url"] for h in hits):
                hits.append({"name": m["title"], "rarity": "", "kata": "",
                             "image_url": m["url"], "title": m["title"], "source": "保管庫"})
        st.write(f"{len(hits)} 件")
        with st.expander("一括コピー（全件をまとめてコピー／Excel・スプレッドシートに貼れます）"):
            st.caption("下の右上のコピーボタンで全件コピー。列は カード名 / 型番 / レア（タブ区切り）")
            tsv = "カード名\t型番\tレア\n" + "\n".join(
                f'{h["name"]}\t{h["kata"]}\t{h["rarity"]}' for h in hits)
            st.code(tsv, language=None)
        cols = st.columns(4)
        for idx, h in enumerate(hits):
            with cols[idx % 4]:
                if h["image_url"]:
                    show_img(h["image_url"])
                st.markdown(f"**{h['name'][:30]}**　<span style='color:#999;font-size:11px'>[{h['source']}]</span>",
                            unsafe_allow_html=True)
                # 1行まとめ（カード名／型番／レア）をワンクリックでコピー
                st.code(f'{h["name"]}\t{h["kata"]}\t{h["rarity"]}', language=None)
                mc1, mc2 = st.columns(2)
                with mc1:
                    st.caption("型番")
                    st.code(h["kata"] or "—", language=None)
                with mc2:
                    st.caption("レア")
                    st.code(h["rarity"] or "—", language=None)
                st.divider()
    else:
        st.info("検索ワードを入れると保管庫の画像が表示されます。")

# ============================================================ ③ 画像を追加
with tab_add:
    st.caption("新しい画像を保管庫に追加します（追加のみ・削除や上書きはできません）。")
    if not can_write:
        st.warning("画像追加は停止中です。Streamlit の Settings → Secrets に "
                   "`WP_USER` と `WP_APP_PASS` を設定すると有効になります。")
    up_img = st.file_uploader("画像ファイル（png/jpg/webp）", type=["png", "jpg", "jpeg", "webp"],
                              key="add_img")
    ca1, ca2, ca3 = st.columns(3)
    a_name = ca1.text_input("カード名", key="add_name")
    a_kata = ca2.text_input("型番（任意）", key="add_kata")
    a_rar = ca3.text_input("レアリティ（任意）", key="add_rar")
    if up_img is not None:
        st.image(up_img, width=180)
    disabled = not (can_write and up_img is not None and a_name.strip())
    if st.button("保管庫に追加", type="primary", disabled=disabled):
        title = a_name.strip()
        if a_rar.strip():
            title += f"（{a_rar.strip()}）"
        if a_kata.strip():
            title += f"[{a_kata.strip()}]"
        ext = os.path.splitext(up_img.name)[1] or ".png"
        fn = SH.san_filename(a_kata.strip(), a_name.strip(), "add", ext=ext)
        try:
            _, url = WP.upload_media(fn, up_img.getvalue(), title,
                                     user=WP_USER, app_pass=WP_PASS)
            st.success("保管庫に追加しました。")
            st.code(url)
            st.image(url, width=180)
            st.caption("「② 保管庫を見る」で名前検索すると出てきます。")
        except Exception as e:
            st.error(f"追加に失敗: {e}")
