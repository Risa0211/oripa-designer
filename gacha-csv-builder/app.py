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

import build_import_csv as B   # redeploy marker 2026-07-23b（モジュール再読込のためフル再起動を促す）
import palette_lookup
import storehouse as SH
import wp_client as WP
import wp_admin as WPA
import gacha_api as G   # WPプラグイン経由（Xserver国外IP制限を回避してアップ/検索/編集/削除）
import snkrdunk_price as SP   # スニダンの直近取引価格/相場を型番で引く（画像を選ぶ画面に併記）
from auth import check_password

HERE = Path(__file__).parent
MASTER_CSV = HERE / "master_db_dopa.csv"
ONEPIECE_CSV = HERE / "master_db_onepiece.csv"   # DOPAワンピ由来（自社WP保管）
ADMIN_CSV = HERE / "card_db_export.csv"
ADDED_CSV = HERE / "master_db_added.csv"   # 手動で保管庫に足したぶん（カードラッシュ/公式/スニダン等）
# 原簿に結合する追加マスター。1箇所で持たないと片方だけ足して不整合になる。
EXTRA_MASTERS = (ONEPIECE_CSV, HERE / "master_db_pokemoncard_owned.csv",
                 HERE / "master_db_admin.csv")
SNK_PRICE_CSV = HERE / "snkrdunk_prices.csv"     # 価格インデックスの軽量版（毎朝GitHub Actionsが更新）
PALETTE_CSVS = [HERE / "palette_pseudo.csv", HERE / "palette_extra.csv"]
LOGO = HERE / "assets" / "logo.png"
ICON = HERE / "assets" / "icon.png"

st.set_page_config(
    page_title="ガチャ登録CSVビルダー",
    page_icon=str(ICON) if ICON.exists() else None,
    layout="wide")

# ログイン（Secretsに app_password がある時のみ有効。無ければ素通り＝開発モード）
if not check_password():
    st.stop()


def wp_creds():
    """書き込み認証。Streamlit Secrets → 環境変数の順。無ければ('','')＝閲覧のみ。"""
    u = p = ""
    try:
        u = str(st.secrets.get("WP_USER", "")) or os.environ.get("WP_USER", "")
        p = str(st.secrets.get("WP_APP_PASS", "")) or os.environ.get("WP_APP_PASS", "")
    except Exception:
        u = os.environ.get("WP_USER", ""); p = os.environ.get("WP_APP_PASS", "")
    return u, p


PRIZE_MIN_VALUE = 1000   # 還元pt(≒価値)がこれ以下の管理画面カードは事前ロードしない（賞にならない＝要追加で都度検索は可能）


def csv_sig(*paths):
    """★キャッシュ用のファイル署名（更新時刻＋サイズ）。
    Streamlit Cloud の「Updated app!」はプロセスを再起動しないホットリロードで、
    キャッシュのキーは**関数のコード**から作られる。そのため app.py が変わらず
    CSVだけ差し替わった時（毎朝の価格自動コミット等）にキャッシュが無効化されず、
    古いデータを読み続ける。署名を引数に渡してファイルが変わったら読み直させる。

    ★大きな表（原簿4千件/管理画面2.6万件/価格6千件）のローダーは cache_data ではなく
    cache_resource を使う。cache_data は**呼ぶたびに中身を丸ごとコピー**して返すため、
    1回の再実行で何十回も呼ぶ本ツールでは待ち時間の大きな割合を占めていた。
    これらは読むだけ（書き換えない）のでコピー不要。"""
    sig = []
    for p in paths:
        try:
            s = Path(p).stat()
            sig.append((str(p), s.st_mtime_ns, s.st_size))
        except OSError:
            sig.append((str(p), 0, 0))
    return tuple(sig)


@st.cache_resource(show_spinner=False)
def _load_prize_values(sig):
    """card_db_export の還元pt(≒カード価値)。id→pt。低額カードを事前ロードから外す判定に使う。"""
    vals = {}
    if ADMIN_CSV.exists():
        for r in B.read_csv_dict(str(ADMIN_CSV)):
            try:
                vals[str(r.get("id", ""))] = int((r.get("redemption_points") or "0").replace(",", ""))
            except Exception:
                pass
    return vals


def load_prize_values():
    return _load_prize_values(csv_sig(ADMIN_CSV))


def _prize_worthy(row, values, min_value=PRIZE_MIN_VALUE):
    """管理画面由来の行を価値でふるう。DOPAは常に残す。値が引けない行も安全側で残す。
    画像URL末尾の a{id}（例 089-063-ex-a25989.webp → id 25989）で card_db_export の還元ptを引く。"""
    import re as _re
    src = B.get(row, "source") or ""
    if src.startswith("DOPA") or src.startswith("公式") or src.startswith("カードラッシュ"):
        return True   # 綺麗ソース（DOPA/公式/カードラッシュ）は常に残す
    m = _re.search(r"a(\d+)\.\w+$", B.get(row, "画像URL", "image_url", "image") or "")
    if not m:
        return True
    v = values.get(m.group(1))
    return True if v is None else v > min_value


@st.cache_resource(show_spinner=False)
def _load_master(sig):
    """①照合用のカード原簿（DOPA綺麗 ＋ DOPAに無い『賞になりうる』管理画面カード）。
    軽量化: (1)DOPAに綺麗版があるカードの管理画面(粗い)重複は載せない (2)還元pt≤¥1,000の
    低額カード（オリパ賞にならない）は事前ロードしない（要追加で都度検索は可能＝カバレッジ維持）。
    保管庫の画像自体は一切消さない。最後に同一カード（型番＋名前）はDOPA優先で1件に集約。"""
    # ADDED_CSV を先に読む＝同点(どちらも綺麗ソース)なら手で足した方が残る（低画質の自動取込を上書きするため）
    rows = (B.read_csv_dict(str(ADDED_CSV)) if ADDED_CSV.exists() else [])
    rows += B.read_csv_dict(str(MASTER_CSV))
    for extra in EXTRA_MASTERS:
        if extra.exists():
            rows = rows + B.read_csv_dict(str(extra))
    vals = load_prize_values()
    rows = [r for r in rows if _prize_worthy(r, vals)]
    return B.dedupe_master_rows(B.drop_admin_dupes_of_clean(rows))


def load_master():
    return _load_master(csv_sig(MASTER_CSV, *EXTRA_MASTERS, ADDED_CSV, ADMIN_CSV))


@st.cache_resource(show_spinner="管理画面ダンプ読込中…")
def _load_admin(sig):
    return SH.load_admin(str(ADMIN_CSV))


def load_admin():
    return _load_admin(csv_sig(ADMIN_CSV))


@st.cache_resource(show_spinner=False)
def _load_admin_cards(sig):
    """★自動照合の予備原簿＝管理画面ダンプ(2.6万件)を原簿の形にしたもの。
    綺麗ソース(DOPA/公式/カードラッシュ)に無くても、**型番とカード名の両方が一致**すれば
    管理画面の画像を自動で当てる。これが無いと『管理画面に正しいカードがあるのに
    保管庫に無し扱いになり、名前だけ一致する別型番の絵柄が前に出る』（実運用の指摘）。"""
    return SH.admin_card_rows(SH.load_admin(str(ADMIN_CSV)))


def load_admin_cards():
    return _load_admin_cards(csv_sig(ADMIN_CSV))


@st.cache_resource(show_spinner=False)
def _load_categories(sig):
    """管理画面に実在するカードフォルダー名（G Categoryの有効値）を件数順で返す。
    CSVインポートはこの名前と完全一致しないと弾かれるため、選択式にして事故を防ぐ。"""
    from collections import Counter
    rows = SH.load_admin(str(ADMIN_CSV))
    c = Counter((r.get("category_name") or "").strip() for r in rows)
    bad = {"", "未登録", "未設", "未", "ログインボーナス", "ログインボーナス（新規）"}
    opts = [name for name, _ in c.most_common() if name and name not in bad]
    # 「未登録」を先頭に（分からない賞の安全な選択肢）
    return ["未登録"] + opts


def load_categories():
    return _load_categories(csv_sig(ADMIN_CSV))


@st.cache_resource(show_spinner=False)
def _load_snk_prices(sig):
    """スニダン価格（型番→直近取引価格/相場）。約580KBの軽量CSVなので初期読込は一瞬。
    ファイルが無ければ空＝価格表示だけOFFになり、他の機能は今まで通り動く。
    戻り値 (型番→行リスト, 基準日=一番新しい更新日)。"""
    prices = SP.load(str(SNK_PRICE_CSV))
    days = {r.get("updated", "") for rows in prices.values() for r in rows}
    return prices, max((d for d in days if d), default="")


def load_snk_prices():
    return _load_snk_prices(csv_sig(SNK_PRICE_CSV))[0]


def snk_updated_on():
    """価格データの基準日。画面に出して鮮度が分かるようにする。"""
    return _load_snk_prices(csv_sig(SNK_PRICE_CSV))[1]


@st.cache_resource(show_spinner=False)
def load_palette():
    return palette_lookup.load_palette(*[str(p) for p in PALETTE_CSVS if p.exists()])


@st.cache_resource(show_spinner=False)
def _load_store(sig):
    """保管庫マスター（DOPA綺麗 ＋ DOPAに無い『賞になりうる』管理画面カード）。カード名/型番/URL/媒体id付き。
    DOPA重複＋還元pt≤¥1,000の低額カードはツールに載せない＝軽量化（保管庫の画像は消さない）。
    その後、同一カード（型番＋名前）の残り重複もDOPA優先で1件に集約。"""
    # ADDED_CSV を先に読む＝同点(どちらも綺麗ソース)なら手で足した方が残る（低画質の自動取込を上書きするため）
    rows = (B.read_csv_dict(str(ADDED_CSV)) if ADDED_CSV.exists() else [])
    rows += B.read_csv_dict(str(MASTER_CSV))
    for extra in EXTRA_MASTERS:
        if extra.exists():
            rows = rows + B.read_csv_dict(str(extra))
    vals = load_prize_values()
    rows = [r for r in rows if _prize_worthy(r, vals)]
    return B.dedupe_master_rows(B.drop_admin_dupes_of_clean(rows))


def load_store():
    return _load_store(csv_sig(MASTER_CSV, *EXTRA_MASTERS, ADDED_CSV, ADMIN_CSV))


def parse_design(uploaded):
    # 一時ファイルはユニーク名（複数人同時アップの衝突回避＝チーム利用対応）。/tmpに置く。
    import tempfile
    is_xlsx = uploaded.name.lower().endswith(".xlsx")
    suffix = ".xlsx" if is_xlsx else ".csv"
    fd, tmp = tempfile.mkstemp(suffix=suffix, prefix="design_")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(uploaded.getbuffer())
        if is_xlsx:
            # シート名は指定せず自動検出（旧「設計入力」/新「設計テンプレート」両対応）
            return B.read_design_xlsx(tmp)
        # CSV: 設計シートのCSVエクスポート（賞ランクヘッダ行を自動検出）/ 素の明細CSV 両対応
        return B.read_design_csv_table(tmp)
    finally:
        Path(tmp).unlink(missing_ok=True)


def palette_options(palette):
    opts = []
    for key, r in palette.get("by_key", {}).items():
        opts.append((f'{r.get("種別","")}｜{r.get("pt/種別詳細","") or key}', key, r.get("画像URL", "")))
    return sorted(opts)


# ---- 共有リソース ----
master_rows = load_master()
palette = load_palette()
pal_opts = palette_options(palette)
# 確定表の行（A〜L 12列）から元のカードを引き戻す索引。CSVには型番列が無いので画像URL→原簿で辿る。
@st.cache_resource(show_spinner=False)
def _card_indexes(sig):
    """画像URL→カード / カード名→カード の索引。確定表で『その画像が何のカードか』を
    引き戻して型番・スニダン相場を出すために使う。
    ★原簿(綺麗ソース)だけでなく管理画面由来の画像も入れる（管理画面の画像で確定した賞の
      相場が出ないと、画像の取り違えを値段で見つけられないため）。"""
    by_url, by_name = {}, {}
    for _r in list(master_rows) + list(load_admin_cards()):
        _u = B.get(_r, "画像URL", "image_url", "image")
        if _u:
            by_url.setdefault(_u, _r)
        _n = B._name_key(B.get(_r, "カード名", "name"))
        if _n:
            by_name.setdefault(_n, []).append(_r)
    return by_url, by_name


CARD_BY_URL, CARD_BY_NAME = _card_indexes(
    csv_sig(MASTER_CSV, *EXTRA_MASTERS, ADDED_CSV, ADMIN_CSV))


def card_of_row(image_url, title):
    """確定行から原簿のカードを特定（画像URL優先→カード名が1件に決まる時のみ名前）。"""
    m = CARD_BY_URL.get(image_url)
    if m:
        return m
    cands = CARD_BY_NAME.get(B._name_key(title)) or []
    return cands[0] if len(cands) == 1 else None
def _load_gacha_api_env():
    """受け口の設定を Streamlit Secrets → 環境変数 の順で読み、gacha_api が拾えるよう os.environ に載せる。
    （Streamlit Cloud の Secrets は os.environ に自動で入らないため）。"""
    for k in ("GACHA_API_URL", "GACHA_API_TOKEN"):
        v = ""
        try:
            v = str(st.secrets.get(k, ""))
        except Exception:
            v = ""
        v = v or os.environ.get(k, "")
        if v:
            os.environ[k] = v


_load_gacha_api_env()
WP_USER, WP_PASS = wp_creds()
# 書き込みは「プラグイン受け口(G)が有効」or「WP直REST認証あり」で可能。
# 受け口経由なら海外(Streamlit Cloud)からでも通る＝本命の書き込み手段。
can_write = G.enabled() or bool(WP_USER and WP_PASS)


def sh_upload(filename, data, title):
    """保管庫へ画像追加。受け口(G)優先→無ければWP直REST。戻り値 (id, url)。"""
    if G.enabled():
        return G.upload(filename, data, title)
    return WP.upload_media(filename, data, title, user=WP_USER, app_pass=WP_PASS)


def sh_migrate(src_url, filename, title):
    """外部URL(業者S3等)の画像を取得して保管庫へ追加。受け口優先。戻り値 (id, url)。"""
    data = WP._req(src_url, timeout=60)
    return sh_upload(filename, data, title)


def sh_search(query, per_page=40):
    """保管庫をライブ検索。受け口優先→無ければWP直REST。戻り値 [{"id","title","url","alt"}]。"""
    if G.enabled():
        try:
            return G.search(query, per_page=per_page)
        except Exception:
            return []
    try:
        return WP.search_media(query, user=WP_USER, app_pass=WP_PASS, per_page=per_page)
    except Exception:
        return []


def sh_update(media_id, title):
    if G.enabled():
        return G.update_meta(media_id, title)
    return WPA.update_meta(media_id, title=title, user=WP_USER, app_pass=WP_PASS)


def sh_replace(old_id, filename, data, title):
    if G.enabled():
        return G.replace(old_id, filename, data, title)
    return WPA.replace_media(old_id, filename, data, title, user=WP_USER, app_pass=WP_PASS)


def sh_delete(media_id):
    if G.enabled():
        return G.delete(media_id)
    return WPA.delete_media(media_id, user=WP_USER, app_pass=WP_PASS)

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
    st.markdown(f"照合カード（綺麗な画像優先）: **{len(master_rows):,}**　演出パレット: **{len(pal_opts)}**")
    st.markdown(("画像の追加/編集/削除: **有効**" if can_write
                 else "画像の追加/編集/削除: **停止中**（Secretsに WP_USER / WP_APP_PASS を設定すると有効）"))
    _snk_n = len(load_snk_prices())
    if _snk_n:
        st.markdown(f"スニダン価格: **{_snk_n:,}型番**（{snk_updated_on() or '日付不明'}時点）")
        st.caption("画像の下に「直近取引＝PSA10の直近成約／相場＝PSA10の出品最安」を併記します。"
                   "今この瞬間の値が見たい時は各セクションの🔄で取り直せます。")
    else:
        st.markdown("スニダン価格: **なし**（snkrdunk_prices.csv 未配置）")


# ---- スニダン価格（画像の横に併記）----
def snk_price(kata, name="", rarity=""):
    """型番＋カード名でスニダン価格を引く（誤マッチ防止のため名前が合わない時は価格を出さない）。"""
    return SP.lookup(load_snk_prices(), kata, name, rarity)


def snk_live_of(res):
    """🔄で取り直した『今の値』があれば (sale, ask) を返す。無ければNone。"""
    if not res or res.get("status") != "ok" or not res.get("aid"):
        return None
    v = st.session_state.get("snk_live", {}).get(res["aid"])
    if not v or (v[0] is None and v[1] is None):
        return None
    return (res["sale"] if v[0] is None else v[0], res["ask"] if v[1] is None else v[1])


def snk_show(res):
    """候補サムネの下に価格2行を出す。"""
    html = SP.caption_html(res, live=snk_live_of(res))
    if html:
        st.markdown(html, unsafe_allow_html=True)


def snk_cell(res, which):
    """確定表のセル表示（which="sale" 直近取引価格 / "ask" 相場）。"""
    if not res or res["status"] in ("off", "nohit"):
        return ""
    if res["status"] == "namemismatch":
        return "要確認"
    live = snk_live_of(res)
    v = (live[0] if which == "sale" else live[1]) if live else res[which]
    txt = f"¥{int(v):,}" if v else ""
    if res["status"] == "multi":
        lo, hi = res.get(f"{which}_range", (0, 0))
        txt = (f"¥{lo:,}〜¥{hi:,}" if lo != hi else (f"¥{hi:,}" if hi else "")) + "?"
    return ("⚡" + txt) if (live and txt) else txt


def snk_live_button(aids, key):
    """押した時だけスニダンに問い合わせて『今の値』を取り直す（既定は毎朝更新のキャッシュ値を表示）。"""
    aids = [a for a in dict.fromkeys(aids) if a]
    if not aids:
        return
    if st.button(f"🔄 スニダンの今の価格を取り直す（{len(aids)}枚）", key=f"snklive_{key}",
                 help="押した時だけスニダンに問い合わせます（1枚あたり約1秒）。普段は毎朝更新の値を表示。"):
        from concurrent.futures import ThreadPoolExecutor, as_completed
        store = st.session_state.setdefault("snk_live", {})
        bar = st.progress(0.0, text="スニダンから取得中…")
        done = 0
        with ThreadPoolExecutor(max_workers=5) as ex:
            futs = {ex.submit(SP.fetch_live, a): a for a in aids}
            for fut in as_completed(futs):
                try:
                    store[futs[fut]] = fut.result()
                except Exception:
                    pass
                done += 1
                bar.progress(done / len(aids), text=f"スニダンから取得中… {done}/{len(aids)}枚")
        bar.empty()
        st.rerun()


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
            _, wp_url = sh_migrate(src_url, filename, title)
            st.session_state["migrated"][src_url] = wp_url
            return wp_url
        except Exception as e:
            st.warning(f"移行に失敗（元URLをそのまま使用）: {e}")
            return src_url
    return src_url  # 認証無し=閲覧のみ→元URLを直接使う（後で移行可）


def is_box_like(*texts):
    """BOX/パック/ボックス等の物販商品かを名前/タイトルから判定（→カテゴリBOX・バッジ未開封）。"""
    kw = ("BOX", "ボックス", "パック", "PACK", "ブースター", "カートン", "未開封")
    blob = " ".join(str(t or "") for t in texts).upper()
    return any(k.upper() in blob for k in kw)


def apply_image_pick(row, h, name, cat_opts=None):
    """検索結果 h をこの賞(row)の画像として採用し、manual上書きに反映する。
    保管庫に無いS3画像は保管庫へ移行してからURLを入れる。レア/型番/カテゴリも一緒に更新。
    要追加・確定差し替えの両方で使う共通処理。"""
    if cat_opts is None:
        cat_opts = load_categories()
    fn = SH.san_filename(h.get("kata", ""), name, f"a{row}",
                         ext=os.path.splitext(h["image_url"])[1] or ".png")
    url = resolve_image_url(row, h["image_url"], fn, name)
    picked = {"画像URL上書き": url, "レアリティ": h.get("rarity", ""), "型番": h.get("kata", "")}
    adm_cat = h.get("category", "")
    if is_box_like(h.get("title", ""), h.get("name", ""), name):
        picked["カテゴリ"] = "BOX"
        picked["バッジ"] = "未開封"
        st.session_state[f"badge2_{row}"] = ["未開封"]
    elif adm_cat in cat_opts:
        picked["カテゴリ"] = adm_cat
    elif h.get("rarity", "") in cat_opts:
        picked["カテゴリ"] = h.get("rarity", "")
    st.session_state["manual"].setdefault(row, {}).update(picked)


@st.cache_resource(show_spinner=False)
def _load_thumb_map(sig):
    """画像URL→サムネイルURL（WPが自動生成する中サイズ・高さ300px前後で約20KB）。
    ★一覧のサムネに原寸(200〜700KB)を読ませると1画面で数十MBになり「候補が出るまで数分」に
    なっていた。表示だけサムネに差し替える（CSVに出す画像URLは原寸のまま）。
    表に無いURL（アップしたて等）は原寸にフォールバックするので壊れない。
    更新: ~/minnatoreca-gacha-csv/build_thumb_map.py を日本のIPから実行して差し替え。"""
    m = {}
    p = HERE / "thumb_map.csv"
    if p.exists():
        for r in B.read_csv_dict(str(p)):
            u = (r.get("画像URL") or "").strip()
            t = (r.get("サムネURL") or "").strip()
            if u and t:
                m[u] = t
    return m


def thumb_url(url):
    """一覧表示用の軽い画像URL（無ければ原寸そのまま）。"""
    u = (url or "").strip()
    return _load_thumb_map(csv_sig(HERE / "thumb_map.csv")).get(u, u)


def img_tag(url, radius=6):
    """全画面ボタンの付かないHTML画像（st.imageの拡大トラップを回避）。
    一覧はサムネ（軽い）を表示し、クリックで原寸を別タブで開けるようにする。"""
    if not url:
        return ""
    t = thumb_url(url)
    img = (f'<img src="{t}" loading="lazy" decoding="async" '
           f'style="width:100%;max-width:200px;border-radius:{radius}px;display:block;'
           f'border:1px solid #eee">')
    if t != url:
        return f'<a href="{url}" target="_blank" rel="noopener" title="原寸で開く">{img}</a>'
    return img


def show_img(url):
    st.markdown(img_tag(url), unsafe_allow_html=True)


def compress_img(data, max_w=800, quality=82):
    """アップ画像をWebP圧縮（横max_px・q82）。保管庫の2万枚と同じ基準で容量を大幅削減。
    失敗時は元データのまま（拡張子Noneで返す）。戻り値 (bytes, ext or None)。"""
    try:
        from io import BytesIO
        from PIL import Image
        im = Image.open(BytesIO(data))
        if im.mode in ("P", "LA"):
            im = im.convert("RGBA")
        elif im.mode == "CMYK":
            im = im.convert("RGB")
        if im.width > max_w:
            h = round(im.height * max_w / im.width)
            im = im.resize((max_w, h), Image.LANCZOS)
        out = BytesIO()
        im.save(out, "WEBP", quality=quality, method=6)
        return out.getvalue(), ".webp"
    except Exception:
        return data, None


def upload_fn(kata, rar, data, ext=".webp"):
    """アップ画像のファイル名を『型番-レア-内容ハッシュ』のASCII一意名にする。
    カード名が日本語だとASCII変換で消えて 'add.webp' 等になり衝突する問題を回避。"""
    import hashlib
    h = hashlib.md5(data).hexdigest()[:8]
    fn = SH.san_filename(kata or "", rar or "", h, ext=ext)
    return fn


tab_make, tab_view, tab_add = st.tabs(
    ["① ガチャCSV作成", "② 保管庫（検索・コピー・編集）", "③ 画像を追加"])


@st.cache_resource(ttl=120, show_spinner=False, max_entries=200)
def wp_live_search(query, limit=40):
    """WPメディアをライブ検索して store 形式で返す（新着アップ分の同期）。
    受け口(G)経由なら海外からも読める。ダメなら空リストで安全にフォールバック。
    ★毎rerunで各検索ボックスがネットワーク問い合わせして重くなるため、クエリ単位で
    120秒キャッシュ（新着アップは最長2分で反映／採用時はrerunで即反映され検索非依存）。"""
    if not query:
        return []
    hits = sh_search(query, per_page=limit)
    out = []
    for m in hits:
        nm, rar, kata = parse_card_title(m.get("title", ""))
        out.append({"name": nm or m.get("title", ""), "rarity": rar, "kata": kata,
                    "image_url": m.get("url", ""), "wp_id": m.get("id", ""), "source": "保管庫(WP)"})
    return out


@st.cache_resource(ttl=600, show_spinner=False, max_entries=200)
def search_static(query, sig, n_dopa=8, n_admin=16):
    """静的DB(保管庫DOPA＋管理画面ダンプ2.6万件)を賞名で検索。
    ★毎rerunで各検索ボックスが全件スキャンして重くなるため、クエリ+ファイル署名で
    キャッシュ（sig=対象CSVのstat署名。差し替え時だけ再スキャン）。"""
    return SH.search_dopa(master_rows, query, limit=n_dopa) + SH.search_admin(load_admin(), query, limit=n_admin)


def _search_sig():
    """search_static のキャッシュ無効化用：照合対象CSVのstat署名。"""
    return csv_sig(MASTER_CSV, *EXTRA_MASTERS, ADDED_CSV, ADMIN_CSV)


def merge_by_wpid(csv_rows, wp_rows):
    """静的CSVの結果とWPライブ結果を結合し、wp_id/URLで重複排除（CSV優先）。"""
    seen = set()
    out = []
    for r in list(csv_rows) + list(wp_rows):
        key = str(r.get("wp_id") or "") or (r.get("image_url") or "")
        if key and key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


def parse_card_title(t):
    """WPメディアのタイトル『カード名（レア）[型番]』を name/rar/kata に分解。"""
    import re as _re
    kata = rar = ""
    m = _re.search(r"[\[［]([^\]］]+)[\]］]\s*$", t)
    if m:
        kata = m.group(1); t = t[:m.start()]
    m = _re.search(r"[（(]([^）)]+)[）)]\s*$", t.strip())
    if m:
        rar = m.group(1); t = t[:m.start()]
    return t.strip(), rar, kata

# ============================================================ ① ガチャCSV作成
def render_make(uploaded, category="交換専用"):
    """設計シートアップ後の本体（tab内で呼ぶ。st.stopは使わずreturnで抜ける）。
    category=レアリティを持たない賞（演出/ポイント変換/最低保証）のG列フォールバック。"""
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

    # 実カードはレアリティを1枚ずつ自動でG列に。categoryは演出/pt賞など
    # レアリティを持たない賞のフォールバックとしてのみ使う。
    out_rows, unmatched, warnings, ambiguous = B.build(
        master_rows, inject, B.DEFAULT_HEADERS, {}, palette, default_category=category,
        fallback_rows=load_admin_cards(),        # 型番＋名前が一致すれば管理画面の画像も自動で当てる
        valid_categories=load_categories())      # G列は実在フォルダーに寄せる（インポート弾かれ防止）

    c1, c2, c3 = st.columns(3)
    c1.metric("確定（CSV出力）", len(out_rows))
    c2.metric("要選択（画像で選ぶ）", len(ambiguous))
    c3.metric("要追加（保管庫に無し）", len(unmatched))
    st.divider()

    # ---- 要選択：同名で絵柄が複数 ----
    if ambiguous:
        st.subheader("画像を選ぶ（同名で絵柄が複数）")
        # 候補ごとのスニダン価格を先に引く（ローカルCSV参照なので通信なし＝軽い）
        amb_res = {(a["row"], i): snk_price(c["型番"], c["カード名"], c["レアリティ"])
                   for a in ambiguous for i, c in enumerate(a["候補"])}
        snk_live_button([r.get("aid") for r in amb_res.values()], "ambiguous")
        for a in ambiguous:
            row = a["row"]
            st.markdown(f"**{a['ランク']}　{a['設計上の名前']}**　"
                        f"<span style='color:#888'>還元{a['還元pt']}pt・{len(a['候補'])}候補</span>",
                        unsafe_allow_html=True)
            if a.get("注意"):
                st.caption("⚠ " + a["注意"])
            cols = st.columns(6)   # 候補1件でも列幅を固定（1列だと画像が全幅に伸びて見づらい）
            labels = []
            for idx, c in enumerate(a["候補"]):
                with cols[idx % 6]:
                    if c["画像URL"]:
                        show_img(c["画像URL"])
                    st.caption(f'{c["型番"]}｜{c["レアリティ"]}')
                    snk_show(amb_res[(row, idx)])
                labels.append(f'{c["型番"]}｜{c["レアリティ"]}｜{c["カード名"]}')
            choice = st.radio("絵柄を選択", ["（未選択）"] + labels,
                              key=f"radio_{row}", horizontal=True, label_visibility="collapsed")
            if choice != "（未選択）":
                picks[row] = a["候補"][labels.index(choice)]["型番"]
            else:
                picks.pop(row, None)
            # この絵柄でよくない時は、下の「保管庫に無い賞」と同じ検索/追加をここでも開ける
            if a.get("注意"):
                _render_pick_ui(a["row"], a["設計上の名前"], manual, key="amb")
            st.divider()

    # ---- 要追加：保管庫に無し → 管理画面を検索して使う分だけ移行 ----
    if unmatched:
        st.subheader("保管庫に無い賞（管理画面から探して1クリックで移行）")
        st.caption("賞をひとつ開くと、その賞の候補だけを読み込みます"
                   "（全部の候補を一度に出すと画像が数十MBになり重くなるため）。"
                   "正しい画像の『これを使う』を押すと、その1枚だけ保管庫にコピーしてCSVに入ります。")
        for u in unmatched:
            row = u["row"]
            name = u["設計上の名前"]
            st.markdown(f"**{name}**　<span style='color:#888'>{u['種別']}</span>",
                        unsafe_allow_html=True)
            _render_pick_ui(row, name, manual, key="um", open_default=(u is unmatched[0]))
            st.divider()

    _render_confirm_and_download(uploaded, out_rows, unmatched, ambiguous, warnings,
                                 inject, manual)


def _render_pick_ui(row, name, manual, key="um", open_default=False):
    """賞ひとつ分の「画像を探して選ぶ／追加する」UI。★開いた時だけ検索・描画する。
    以前は全賞ぶんを常に描画していたため、賞が20〜30ある設計シートでは
    1回の操作ごとに 保管庫API検索×賞の数 と 数十MBのサムネイル読込 が走って重かった。"""
    state_key = f"pick_open_{key}"
    if state_key not in st.session_state and open_default:
        st.session_state[state_key] = row     # 最初の1件だけ既定で開く（閉じたら閉じたまま）
    is_open = st.session_state.get(state_key) == row
    bc1, bc2 = st.columns([1, 4])
    if bc1.button("閉じる" if is_open else "🔍 画像を探す", key=f"open_{key}_{row}"):
        st.session_state[state_key] = None if is_open else row
        st.rerun()
    cur_url = manual.get(row, {}).get("画像URL上書き", "")
    if cur_url and not is_open:
        bc2.markdown(f'<img src="{thumb_url(cur_url)}" style="height:70px;border-radius:4px">'
                     '　<span style="color:#888">この画像を使用中</span>', unsafe_allow_html=True)
    if not is_open:
        return
    with st.container():
        _render_pick_body(row, name, manual, key)


SHOW_FIRST = 12   # 候補サムネの初期表示枚数（残りは「もっと見る」で。画像の読込量を抑える）


def _render_pick_body(row, name, manual, key="um"):
            pal_labels = ["（パレットから選ばない）"] + [o[0] for o in pal_opts]
            cat_opts = load_categories()   # G列カテゴリの有効フォルダー（要追加の上書き用）
            qc1, qc2 = st.columns([3, 2])
            q = qc1.text_input("検索ワード（部分一致）", value=name, key=f"q_{key}_{row}")
            hits = list(search_static(q, _search_sig()))   # クエリ単位でキャッシュ（毎rerunの全件再スキャンを回避）
            # ★保管庫へのライブ問い合わせは既定でしない。海外(Streamlit Cloud)からだと
            #   無応答で数十秒待たされることがあり、それが「1件なのに遅い」の主因だった。
            #   保管庫の画像は同梱CSVで検索できるので、ここに出ないのは「ツールから今アップしたて」だけ。
            if qc2.checkbox("今アップした画像も探す", key=f"live_{key}_{row}",
                            help="保管庫に直接アップしたばかりの画像も探します（数秒〜十数秒かかることがあります）"):
                for w in wp_live_search(q, limit=12):
                    hits.append({"name": w["name"], "rarity": w["rarity"], "kata": w["kata"],
                                 "image_url": w["image_url"], "title": w["name"],
                                 "category": "", "id": "", "source": "保管庫(WP)"})
            if hits:
                more_key = f"more_{key}_{row}"
                if len(hits) > SHOW_FIRST and not st.session_state.get(more_key):
                    if st.button(f"残り{len(hits)-SHOW_FIRST}件も表示", key=f"morebtn_{key}_{row}"):
                        st.session_state[more_key] = True
                        st.rerun()
                    hits = hits[:SHOW_FIRST]
                hit_res = [snk_price(h.get("kata", ""), h.get("name", "") or h.get("title", ""),
                                     h.get("rarity", "")) for h in hits]
                snk_live_button([r.get("aid") for r in hit_res], f"unmatched_{row}")
                cols = st.columns(6)
                for idx, h in enumerate(hits):
                    with cols[idx % 6]:
                        if h["image_url"]:
                            show_img(h["image_url"])
                        src = "保管庫" if h["image_url"].startswith(WP.WP_BASE) else "管理画面"
                        st.caption(f'{h["title"][:22]}\n［{src}］')
                        snk_show(hit_res[idx])
                        if st.button("これを使う", key=f"use_{row}_{idx}"):
                            fn = SH.san_filename(h.get("kata", ""), name, f"a{idx}",
                                                 ext=os.path.splitext(h["image_url"])[1] or ".png")
                            url = resolve_image_url(row, h["image_url"], fn, name)
                            # 画像URL＋選んだカードのレアリティ/型番/実カテゴリを持たせる（マージ）
                            adm_cat = h.get("category", "")
                            picked = {"画像URL上書き": url,
                                      "レアリティ": h.get("rarity", ""),
                                      "型番": h.get("kata", "")}
                            # BOX/パック商品なら カテゴリ=BOX＋バッジ=未開封 を自動セット
                            if is_box_like(h.get("title", ""), h.get("name", ""), name):
                                picked["カテゴリ"] = "BOX"
                                picked["バッジ"] = "未開封"
                                # バッジ欄(widget)側にも反映しないと再描画で消える
                                st.session_state[f"badge2_{row}"] = ["未開封"]
                            elif adm_cat in cat_opts:     # 実カテゴリが有効フォルダーなら採用
                                picked["カテゴリ"] = adm_cat
                            elif h.get("rarity", "") in cat_opts:  # レアが有効フォルダーなら採用
                                picked["カテゴリ"] = h.get("rarity", "")
                            manual.setdefault(row, {}).update(picked)
                            st.rerun()
            else:
                st.caption("該当なし。検索ワードを短くするか、下で画像URL/パレットを指定してください。")
            # カテゴリ(G)の上書き：パック/特別賞などレアが無い実カード用に、この賞のフォルダーを選べる
            cur = manual.get(row, {})
            cur_cat = cur.get("カテゴリ", "") or cur.get("レアリティ", "")
            gi = (cat_opts.index(cur_cat) + 1) if cur_cat in cat_opts else 0
            gc1, gc2 = st.columns(2)
            gsel = gc1.selectbox(f"カテゴリ(G)を指定（未指定なら自動）", ["（自動）"] + cat_opts,
                                 index=gi, key=f"gcat_{row}")
            if gsel != "（自動）":
                manual.setdefault(row, {})["カテゴリ"] = gsel
            elif "カテゴリ" in cur and cur.get("カテゴリ") not in cat_opts:
                manual[row].pop("カテゴリ", None)
            # バッジ(L)：BOX/未開封は「未開封」、鑑定品は「PSA10」等。
            # 「PSA10＋発送のみ」のように2つ併用する賞があるので最大2つ選べる（管理画面はカンマ区切り）。
            badge_opts = ["未開封", "PSA10", "PSA9", "PSA8", "発送のみ", "シングルカード", "傷あり"]
            cb = [b.strip() for b in (cur.get("バッジ", "") or "").split(",") if b.strip() in badge_opts]
            bsel = gc2.multiselect("バッジ(L)（最大2つ・BOXは未開封など）", badge_opts,
                                   default=cb[:2], max_selections=2, key=f"badge2_{row}")
            if bsel:
                manual.setdefault(row, {})["バッジ"] = ",".join(bsel)
            elif "バッジ" in cur:
                manual[row].pop("バッジ", None)
            with st.expander("正しい画像が無い時（アップロード / URL / 演出パレット）"):
                # 正しい画像を直接アップして保管庫に入れ、この賞に使う（差し替え相当）
                up = st.file_uploader("正しい画像をアップロード（保管庫に追加してこの賞に使う）",
                                      type=["png", "jpg", "jpeg", "webp"], key=f"upimg_{row}")
                if up is not None and st.button("この画像を使う（保管庫に追加）", key=f"upbtn_{row}"):
                    if not can_write:
                        st.error("画像追加にはログイン(WP_USER/WP_APP_PASS)が必要です。")
                    else:
                        ext = os.path.splitext(up.name)[1] or ".png"
                        data, wext = compress_img(up.getvalue())   # WebP自動圧縮
                        ext = wext or ext
                        fn = upload_fn(cur.get("型番", ""), cur.get("レアリティ", ""),
                                       data, ext=ext)
                        try:
                            _, nu = sh_upload(fn, data, name)
                            manual.setdefault(row, {})["画像URL上書き"] = nu
                            st.success("保管庫に追加してこの賞に設定しました（自動圧縮済み）。")
                            st.rerun()
                        except Exception as e:
                            st.error(f"追加に失敗: {e}")
                mu = st.text_input("または画像URLを直接指定", key=f"url_{row}",
                                   value=manual.get(row, {}).get("画像URL上書き", ""))
                sel = st.selectbox("または演出パレット", pal_labels, key=f"pal_{row}")
                if mu.strip():
                    manual.setdefault(row, {})["画像URL上書き"] = mu.strip()
                elif sel != "（パレットから選ばない）":
                    manual.setdefault(row, {})["演出キー"] = pal_opts[pal_labels.index(sel) - 1][1]


def _render_confirm_and_download(uploaded, out_rows, unmatched, ambiguous, warnings,
                                 inject, manual):
    """確定プレビュー（編集・削除）と管理画面インポートCSVのダウンロード。"""
    # ---- 確定プレビュー（その場で編集・削除できる）& ダウンロード ----
    st.subheader("確定してCSVに出力される賞")
    if out_rows:
        st.caption("表の値を直接編集できます。『削除』にチェックした賞はCSVから外れます"
                   "（ランク・カード名・カテゴリ・バッジ・還元pt・在庫・画像URLはダブルクリックで書き換え）。"
                   "画像を差し替えたい時は『画像URL』を新しいURLに書き換えてください。"
                   "『実価値(設計)』は設計シートの実価値/枚、『直近取引』『相場』は"
                   "**採用した画像のカード**のスニダン価格。桁がズレていたら画像の取り違えを疑ってください。")
        # 各確定行のスニダン価格（画像URL→原簿→型番で照合。通信なし）
        conf_res, conf_card = [], []
        for r in out_rows:
            m = card_of_row(r[5], r[1])
            conf_card.append(m or {})
            conf_res.append(snk_price(B.get(m or {}, "型番", "kataban"),
                                      B.get(m or {}, "カード名", "name") or r[1],
                                      B.get(m or {}, "レアリティ", "rarity")) if m else None)
        snk_live_button([(x or {}).get("aid") for x in conf_res], "confirm")
        edit_src = [{"_i": i, "削除": False, "画像": thumb_url(r[5]),
                     "ランク": r[10], "カード名": r[1],
                     "照合した型番": B.get(conf_card[i], "型番", "kataban"),
                     "カテゴリ(G)": r[6],
                     "バッジ": r[11], "実価値(設計)": r[3], "還元pt": r[4], "在庫": r[7],
                     "直近取引価格": snk_cell(conf_res[i], "sale"),
                     "相場": snk_cell(conf_res[i], "ask"),
                     "画像URL": r[5]}
                    for i, r in enumerate(out_rows)]
        edited = st.data_editor(
            edit_src, use_container_width=True, hide_index=True, key="confirm_editor",
            # ★表は一覧性優先でコンパクトなまま（画像以外の項目も確認するのに、行が高いと
            #   スクロールが増えて見づらい）。画像の確認は下の「大きい画像で確認する」で。
            column_config={
                "_i": None,
                "削除": st.column_config.CheckboxColumn("削除", width="small"),
                "画像": st.column_config.ImageColumn("画像", width="small"),
                "ランク": st.column_config.TextColumn("ランク", width="small"),
                "カード名": st.column_config.TextColumn("カード名"),
                "照合した型番": st.column_config.TextColumn(
                    "照合した型番", disabled=True, width="small",
                    help="採用した画像が原簿/管理画面でどのカードだったか。設計の型番と違えば取り違えです"),
                "実価値(設計)": st.column_config.TextColumn(
                    "実価値(設計)", help="設計シートの実価値/枚。CSVのD列(参照価格)になります"),
                "カテゴリ(G)": st.column_config.TextColumn("カテゴリ(G)"),
                "バッジ": st.column_config.TextColumn(
                    "バッジ", help="PSA10,発送のみ のようにカンマで最大2つ"),
                "還元pt": st.column_config.TextColumn("還元pt"),
                "在庫": st.column_config.TextColumn("在庫"),
                "直近取引価格": st.column_config.TextColumn(
                    "直近取引価格", disabled=True, width="medium",
                    help="スニダンPSA10の直近成約価格（成約が無い＝空欄）。編集不可の参考値"),
                "相場": st.column_config.TextColumn(
                    "相場", disabled=True, width="medium",
                    help="スニダンPSA10の出品最安（今出ている売り希望の下限。成約より高めに出る）。編集不可の参考値"),
                "画像URL": st.column_config.TextColumn(
                    "画像URL", help="書き換えると左の画像プレビューも更新されます", width="large"),
            })
        # 編集・削除を out_rows に反映して最終行を作る
        final_rows = []
        for e in edited:
            if e.get("削除"):
                continue
            r = list(out_rows[int(e["_i"])])
            r[10] = str(e.get("ランク", r[10]) or "")
            r[1] = str(e.get("カード名", r[1]) or "")
            r[6] = str(e.get("カテゴリ(G)", r[6]) or "")
            r[3] = str(e.get("実価値(設計)", r[3]) or "")
            r[11] = str(e.get("バッジ", r[11]) or "")
            r[4] = str(e.get("還元pt", r[4]) or "")
            r[7] = str(e.get("在庫", r[7]) or "")
            new_url = str(e.get("画像URL", r[5]) or "")
            if new_url and new_url != r[5]:
                r[5] = new_url
                if not r[0]:      # A URL(非表示)が空なら画像URLで補完
                    r[0] = new_url
            final_rows.append(r)
        dropped = len(out_rows) - len(final_rows)
        if dropped:
            st.caption(f"（{dropped}件を削除中。CSVには {len(final_rows)}件 が出力されます）")

        # ---- 大きい画像で最終確認（画像の取り違え防止）----
        # 表と同じサムネURLなので通信量は増えない（ブラウザのキャッシュがそのまま効く）。
        with st.expander("🖼 大きい画像で確認する（設計の値とスニダン相場を並べて表示）", expanded=True):
            st.caption("設計シートの実価値と、採用した画像のカードのスニダン相場が"
                       "大きくズレていたら、別の絵柄を拾っている可能性があります。"
                       "画像をクリックすると原寸で開きます。")
            gcols = st.columns(5)
            for gi, e in enumerate(edited):
                if e.get("削除"):
                    continue
                r = out_rows[int(e["_i"])]
                card = conf_card[int(e["_i"])]
                res = conf_res[int(e["_i"])]
                with gcols[gi % 5]:
                    show_img(str(e.get("画像URL", r[5]) or ""))
                    st.markdown(
                        f'<div style="font-size:0.82rem;line-height:1.45">'
                        f'<b>{e.get("ランク","")}</b>　{str(e.get("カード名",""))[:22]}<br>'
                        f'<span style="color:#888">型番 {B.get(card, "型番", "kataban") or "—"}</span><br>'
                        f'実価値 <b>{e.get("実価値(設計)","") or "—"}</b>／還元 {e.get("還元pt","")}pt<br>'
                        f'<span style="color:#c67">相場 {snk_cell(res, "ask") or "—"}</span>'
                        f'</div>', unsafe_allow_html=True)
                    st.divider()

        # ---- 確定した賞の画像を、カード名で検索して差し替える（URL手入力の代わり）----
        with st.expander("🔄 確定した賞の画像を差し替える（カード名などで検索して選ぶ）"):
            st.caption("URLを手で貼らなくても、カード名で保管庫/管理画面を検索して画像を選び直せます。"
                       "『これを使う』を押すと、その賞の画像が差し替わります（保管庫に無ければコピーして使用）。")
            unresolved = {u["row"] for u in unmatched} | {a["row"] for a in ambiguous}
            conf = [(i, B.get(inject[i - 2], "カード名", "name"),
                     B._norm_rank(B.get(inject[i - 2], "ランク", "rank")))
                    for i in range(2, len(inject) + 2) if i not in unresolved]
            if not conf:
                st.caption("差し替えできる確定賞がありません。")
            else:
                labels = [f"{rk or '—'}｜{nm}" for (_i, nm, rk) in conf]
                sidx = st.selectbox("差し替える賞を選ぶ", list(range(len(conf))),
                                    format_func=lambda k: labels[k], key="chg_pick")
                row, name, _rk = conf[sidx]
                cur_ov = manual.get(row, {}).get("画像URL上書き", "")
                cc1, cc2, cc3 = st.columns([3, 1, 1])
                q = cc1.text_input("検索ワード（部分一致）", value=name, key=f"chg_q_{row}")
                # ★検索は押した時だけ実行（既定では走らせない＝CSV出力までを軽く保つ）。
                if cc2.button("🔍 検索して選ぶ", key=f"chg_go_{row}"):
                    st.session_state["chg_show"] = (row, q)
                if cur_ov and cc3.button("元の画像に戻す", key=f"chg_reset_{row}"):
                    for k in ("画像URL上書き", "レアリティ", "型番", "カテゴリ", "バッジ"):
                        manual.get(row, {}).pop(k, None)
                    st.session_state.pop("chg_show", None)
                    st.rerun()
                shown = st.session_state.get("chg_show")
                if shown and shown[0] == row:
                    sq = shown[1] or q
                    cat_opts = load_categories()
                    hits = list(search_static(sq, _search_sig()))   # 同梱CSVのみ＝通信なしで即答
                    if not hits:
                        st.caption("該当なし。検索ワードを短くして再検索してください。")
                    else:
                        cols = st.columns(6)
                        for idx, h in enumerate(hits):
                            with cols[idx % 6]:
                                if h["image_url"]:
                                    show_img(h["image_url"])
                                src = "保管庫" if h["image_url"].startswith(WP.WP_BASE) else "管理画面"
                                st.caption(f'{h["title"][:22]}\n［{src}］')
                                if st.button("これを使う", key=f"chg_use_{row}_{idx}"):
                                    apply_image_pick(row, h, name, cat_opts)
                                    st.session_state.pop("chg_show", None)
                                    st.rerun()
    else:
        final_rows = []
        st.info("まだ確定した賞がありません。上で画像を選ぶ／指定すると増えます。")

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(B.DEFAULT_HEADERS)
    w.writerows(final_rows)
    csv_bytes = ("﻿" + buf.getvalue()).encode("utf-8")

    # ---- ★景品画像の取り違え検査（2026-08-25 新設）----
    # 画像URL→原簿→カード名 を突き合わせ、賞品名と違うカードの絵が入っていたらDLさせない。
    # 2026-08-25、70%LOOPで カメール→キノココ／レックウザVMAX→デンボク が管理画面まで通った。
    # 詳細は verify_images.py の冒頭。
    img_ng, img_warn = [], []
    try:
        import verify_images as VI
        if final_rows:
            _cards, _pal, _ = VI.load_master_index([Path(__file__).resolve().parent])
            _res = VI.check([{"name": r[1], "url": r[5], "where": f"{i}行目"}
                             for i, r in enumerate(final_rows, start=1)], _cards, _pal)
            img_ng = [x for x in _res if x["verdict"].startswith("NG")]
            img_warn = [x for x in _res if x["verdict"].startswith("要確認")]
            if img_ng:
                st.error("**画像が賞品と違います。直すまでCSVは出さないでください。**\n\n"
                         + "\n".join(f"- {x['where']}｜{x['detail']}" for x in img_ng))
            elif img_warn:
                st.warning("画像の出どころを原簿から辿れない賞があります（保管庫に足したら "
                           "`master_db_added.csv` と `thumb_map.csv` にも追記してください）:\n\n"
                           + "\n".join(f"- {x['where']}｜{x['name']}" for x in img_warn))
            else:
                st.caption(f"✅ 画像チェック: {len(_res)}件すべて賞品名と画像のカードが一致しています。")
    except Exception as e:                      # 検査が壊れてもCSV生成自体は止めない
        st.warning(f"画像チェックを実行できませんでした（{e}）。画像は目視で確認してください。")

    force_dl = False
    if img_ng:
        force_dl = st.checkbox("取り違えの指摘を承知のうえでダウンロードする", value=False,
                               help="原則チェックしない。指摘が誤りだと確認できた時だけ。")

    st.download_button(f"管理画面インポートCSVをダウンロード（{len(final_rows)}件）",
                       data=csv_bytes, file_name=Path(uploaded.name).stem + "_import.csv",
                       mime="text/csv", type="primary",
                       disabled=(len(final_rows) == 0 or (bool(img_ng) and not force_dl)))

    # ---- 管理画面へのインポート手順（DLしたCSVをどこに貼るか）----
    with st.expander("▶ このCSVを管理画面に入れる手順", expanded=(len(out_rows) > 0)):
        st.markdown(
            "1. [管理画面にログイン](https://minnano-toreca.com)\n"
            "2. 左メニュー **「商品」** → 登録したい **ガチャ** を開く\n"
            "3. **「カード一覧」** タブ → **「CSVのインポート」**\n"
            "4. いまダウンロードした **`_import.csv`** を選んでインポート\n\n"
            "※ CSVの **カテゴリ（G列）** は、先に管理画面で **カードフォルダー** を作っておく必要があります"
            "（未作成だと取り込みが弾かれます）。\n"
            "※ 文字化けする時は、CSVをExcelで開き直さずそのままアップしてください（UTF-8のまま取り込む）。")

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
    st.caption("設計シートをアップするだけ → 自動照合＋カテゴリ自動 → 管理画面インポートCSV")
    uploaded = st.file_uploader("ガチャ設計シート（.xlsx / .csv）", type=["xlsx", "csv"])
    if not uploaded:
        st.info("設計シートをアップロードすると照合が始まります。")
    else:
        render_make(uploaded)

# ============================================================ ② 保管庫（検索・コピー・編集）
VIEW_FIRST = 48   # 保管庫タブの初期表示枚数（残りは「もっと見る」）


def card_cell(h):
    """グリッド1マス：サムネ＋名前＋型番コピー＋（開くと）全コピー/編集。省スペース。"""
    mid = h.get("wp_id", "")
    if h["image_url"]:
        show_img(h["image_url"])
    st.markdown(f"**{h['name'][:20]}**", unsafe_allow_html=True)
    st.code(h["kata"] or "—", language=None)     # 型番＝一番使うのですぐコピー
    snk_show(snk_price(h.get("kata", ""), h.get("name", ""), h.get("rarity", "")))
    with st.expander("コピー / 編集"):
        st.caption("カード名"); st.code(h["name"], language=None)
        st.caption("レア"); st.code(h["rarity"] or "—", language=None)
        st.caption("名前/型番/レア(1行)")
        st.code(f'{h["name"]}\t{h["kata"]}\t{h["rarity"]}', language=None)
        st.caption("画像URL"); st.code(h["image_url"], language=None)
        # ★編集欄は「編集する」を押したカードにだけ出す。全カードぶん常に作ると
        #   1画面で数百個の入力欄になり、表示・操作が目に見えて重くなるため。
        if can_write and mid and st.session_state.get("edit_mid") != mid:
            if st.button("✏️ 編集する", key=f"editbtn_{mid}"):
                st.session_state["edit_mid"] = mid
                st.rerun()
        elif can_write and mid:
            st.divider()
            n_name = st.text_input("カード名", value=h["name"], key=f"an_{mid}")
            n_kata = st.text_input("型番", value=h["kata"], key=f"ak_{mid}")
            n_rar = st.text_input("レア", value=h["rarity"], key=f"ar_{mid}")

            def _title():
                t = (n_name or "").strip()
                if (n_rar or "").strip():
                    t += f"（{n_rar.strip()}）"
                if (n_kata or "").strip():
                    t += f"[{n_kata.strip()}]"
                return t

            if st.button("名前などを保存", key=f"save_{mid}"):
                try:
                    sh_update(mid, _title())
                    st.success("保存しました")
                except Exception as e:
                    st.error(f"保存に失敗: {e}")
            rep = st.file_uploader("画像を差し替え", type=["png", "jpg", "jpeg", "webp"], key=f"rep_{mid}")
            if rep is not None and st.button("この画像に差し替え", key=f"repbtn_{mid}"):
                ext = os.path.splitext(rep.name)[1] or ".png"
                data, wext = compress_img(rep.getvalue())   # WebP自動圧縮
                ext = wext or ext
                fn = upload_fn(n_kata, n_rar, data, ext=ext)
                try:
                    _, nu = sh_replace(mid, fn, data, _title())
                    st.success("差し替えました（自動圧縮済み・URLが変わります）"); st.code(nu)
                except Exception as e:
                    st.error(f"差し替えに失敗: {e}")
            if st.checkbox("削除を確認", key=f"delchk_{mid}") and \
               st.button("完全に削除", key=f"delbtn_{mid}"):
                try:
                    sh_delete(mid)
                    st.success("削除しました")
                except Exception as e:
                    st.error(f"削除に失敗: {e}")


with tab_view:
    st.caption("保管庫を部分一致で検索。型番はすぐコピー、その他コピー・編集は各カードの「コピー / 編集」を開いて。")
    store_rows = load_store()
    q = st.text_input("検索ワード（カード名 or 型番の一部・部分一致）", key="view_q",
                      placeholder="例: リザードン / 066/060 / PSA10")
    tc1, tc2 = st.columns([3, 1])
    ncol = tc2.selectbox("表示列数", [4, 3, 5, 6], index=0, key="view_ncol")
    st.checkbox("今アップした画像も探す（数秒かかります）", key="view_live",
                help="同梱の保管庫データに無い＝アップしたばかりの画像も探します")
    if q:
        hits = SH.search_store(store_rows, q, limit=120)
        # ★保管庫へのライブ問い合わせは既定でしない（海外からだと無応答で数十秒待つことがある）。
        #   同梱CSVに無いのは「今アップしたて」だけなので、必要な時だけチェックを入れる。
        if st.session_state.get("view_live"):
            hits = merge_by_wpid(hits, wp_live_search(q, limit=40))[:120]
        n_all = len(hits)
        # ★初期表示は48件まで（1画面に何百枚も出すと重い）。残りはボタンで。
        # 「もっと見る」は検索ワードごと（別の語を入れたらまた48件から）。
        if n_all > VIEW_FIRST and st.session_state.get("view_more") != q:
            hits = hits[:VIEW_FIRST]
        tc1.write(f"**{n_all} 件**（保管庫 {len(store_rows):,} 枚＋WP新着）　"
                  f"多すぎる時は型番や正式名で絞り込むと見やすいです")
        if n_all > len(hits):
            if st.button(f"残り{n_all - len(hits)}件も表示", key="view_more_btn"):
                st.session_state["view_more"] = q
                st.rerun()
        cols = st.columns(ncol)
        for i, h in enumerate(hits):
            with cols[i % ncol]:
                card_cell(h)
                st.divider()
    else:
        st.info(f"検索ワードを入れると保管庫（{len(store_rows):,}枚）から表示します。")

# ============================================================ ③ 画像を追加
with tab_add:
    st.caption("新しい画像を保管庫に追加します。追加後は「② 保管庫」で編集・差し替え・削除できます。")
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
        data, wext = compress_img(up_img.getvalue())   # WebP自動圧縮
        ext = wext or ext
        fn = upload_fn(a_kata.strip(), a_rar.strip(), data, ext=ext)
        try:
            _, url = sh_upload(fn, data, title)
            st.success("保管庫に追加しました（自動圧縮済み）。")
            st.code(url)
            show_img(url)
            st.caption("「② 保管庫」で名前検索すると出てきます（反映まで少し時間がかかる場合があります）。")
        except Exception as e:
            st.error(f"追加に失敗: {e}")
