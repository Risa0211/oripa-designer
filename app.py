"""Streamlit UI — みんなのトレカ オリパ商品設計ツール"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
import streamlit as st
import pandas as pd

import config
from auth import check_password


st.set_page_config(
    page_title="みんなのトレカ オリパ設計ツール",
    page_icon="assets/icon.png",
    layout="wide",
)

# ログイン認証
if not check_password():
    st.stop()

from inventory import load_all_inventory
from research import load_all_references, find_reference, count_cards_in_tier, TIER_COLS
from designer import DesignSpec, TierSpec, design, save_reservation, build_result_from_selections
from operations import approve, cancel, close_sold_out
from sheets_client import open_inventory


# Sheets API クォータ対策: キャッシュ + 自動リトライ + フォールバック
import time as _time

def _safe_load(loader, retries: int = 4):
    """Sheets APIを叩く関数を安全に呼ぶ (429/quota時に指数バックオフリトライ)"""
    last_err = None
    for attempt in range(retries):
        try:
            return loader()
        except Exception as e:
            last_err = e
            msg = str(e).lower()
            is_quota = "429" in str(e) or "quota" in msg or "rate" in msg
            if not is_quota:
                # クォータ以外のエラーは即座に上げる
                raise
            wait = 2 ** attempt  # 1, 2, 4, 8秒
            _time.sleep(wait)
    # 最終的に諦めて空リスト
    import streamlit as _st
    _st.warning(f"⚠️ Sheets API クォータ超過のため一時的に空表示 (再読み込みで復帰): {str(last_err)[:80]}")
    return []


@st.cache_data(ttl=1800, show_spinner=False)
def cached_premium_gachas():
    from research import load_premium_gachas
    return _safe_load(load_premium_gachas)


@st.cache_data(ttl=1800, show_spinner=False)
def cached_new_gachas():
    from research import load_new_gachas
    return _safe_load(load_new_gachas)


@st.cache_data(ttl=1800, show_spinner=False)
def cached_dopa_products():
    from research import load_dopa_products
    return _safe_load(load_dopa_products)


@st.cache_data(ttl=3600, show_spinner=False)
def cached_references():
    """既存リサーチDB 8770件もキャッシュ (TTL 1時間)"""
    from research import load_all_references
    return _safe_load(load_all_references)


@st.cache_data(ttl=3600, show_spinner="スニダン全カードを読込中...")
def cached_snkrdunk_index():
    """スニダン全カード価格インデックス（ポケカ＋ワンピ・シングル/BOX/パック）を
    InventoryItem 化して返す。無在庫モードの候補プールに合流させる用途。"""
    try:
        from snkrdunk_index import load_snkrdunk_index
        return load_snkrdunk_index()
    except Exception as ex:
        st.warning(f"⚠️ スニダンインデックス読込失敗: {str(ex)[:80]}")
        return []


@st.cache_data(ttl=300, show_spinner=False)
def cached_price_refresh_stamp():
    """商品別カードマスタ!P1:P3 から最終一括更新日時を取得 (JST文字列)"""
    try:
        from research import open_research
        ss = open_research()
        ws = ss.worksheet('商品別カードマスタ')
        vals = ws.get('P1:P3')
        ts = vals[1][0] if len(vals) >= 2 and vals[1] else ''
        note = vals[2][0] if len(vals) >= 3 and vals[2] else ''
        return ts, note
    except Exception as ex:
        return '', f'取得失敗: {str(ex)[:60]}'


# ロゴヘッダー
header_cols = st.columns([1, 4, 1])
with header_cols[0]:
    st.image("assets/logo_wide.png", width=300)
with header_cols[1]:
    st.markdown(
        """
        <div style='padding-top: 20px;'>
          <h2 style='margin:0; color:#1a1a1a;'>オリパ商品設計ツール</h2>
          <p style='margin:0; color:#666; font-size:14px;'>競合設計を参考に、在庫から商品構成を自動生成します</p>
        </div>
        """,
        unsafe_allow_html=True,
    )
with header_cols[2]:
    if st.session_state.get("authenticated"):
        if st.button("ログアウト", use_container_width=True):
            st.session_state.authenticated = False
            st.rerun()

st.markdown("---")

# テストモードは廃止（運営は無在庫運用のため本番固定）。config が本番スプシを返すよう False 固定。
st.session_state.test_mode = False


# ---------- スニダン価格 最終更新バナー (全タブ共通表示) ----------
def _render_price_refresh_banner():
    _ts, _note = cached_price_refresh_stamp()
    _JST = timezone(timedelta(hours=9))
    if _ts:
        try:
            _last = datetime.strptime(_ts, '%Y-%m-%d %H:%M:%S').replace(tzinfo=_JST)
            _hours = (datetime.now(_JST) - _last).total_seconds() / 3600
        except Exception:
            _hours = -1
        if 0 <= _hours <= 26:
            _bg, _fg, _icon, _msg = '#d1fae5', '#065f46', '✅', '最新'
        elif 26 < _hours <= 48:
            _bg, _fg, _icon, _msg = '#fef3c7', '#92400e', '⚠️', 'やや古い'
        else:
            _bg, _fg, _icon, _msg = '#fee2e2', '#991b1b', '❌', '要確認'
        _hours_disp = f'{_hours:.1f}時間前' if _hours >= 0 else '経過不明'
        cols = st.columns([5, 1])
        with cols[0]:
            st.markdown(
                f'<div style="background:{_bg};color:{_fg};padding:10px 16px;'
                f'border-radius:6px;border-left:5px solid {_fg};font-size:15px;">'
                f'<b>{_icon} スニダン価格 最終一括更新: {_ts} (JST) — {_hours_disp}・{_msg}</b>'
                f'<br><span style="font-size:12px;opacity:0.85;">{_note}｜毎朝 JST 06:00 に自動更新（失敗時のみChatwork通知）</span>'
                f'</div>',
                unsafe_allow_html=True,
            )
        with cols[1]:
            if st.button('🔄 最新に更新', help='キャッシュを破棄して再取得', use_container_width=True):
                cached_price_refresh_stamp.clear()
                st.rerun()
    else:
        st.warning(f'⚠️ スニダン価格の最終更新日時が未取得です。{_note}')

_render_price_refresh_banner()


# ---------- 共通画像取得関数 (tab_template で使用) ----------
@st.cache_data(ttl=3600, show_spinner=False)
def _get_snk_image(snk_url):
    """スニダンページから og:image URL を取得 (.webp→.jpg化)"""
    import requests, re as _re_img
    if not snk_url or not snk_url.startswith('http'):
        return ''
    try:
        r = requests.get(snk_url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=15)
        m = _re_img.search(r'<meta property="og:image" content="([^"]+)"', r.text)
        if m:
            return m.group(1).replace('.webp', '.jpg')
    except Exception:
        pass
    return ''


@st.cache_data(ttl=3600, show_spinner=False)
def _get_tc_image(base_url, card_name, rarity):
    """トレカセンター商品ページから該当カードの画像URLを取得"""
    if not base_url or not base_url.startswith('http'):
        return ''
    try:
        from torecacenter_scraper import fetch_by_url
        detail = fetch_by_url(base_url)
        nm = (card_name or '').strip()
        rar = (rarity or '').strip()
        for c in (detail.get('cards') or []):
            cn = (c.get('name') or '').strip()
            cr = (c.get('rarity') or '').strip()
            if cn == nm and cr == rar:
                return c.get('image_url') or ''
        for c in (detail.get('cards') or []):
            if (c.get('name') or '').strip() == nm:
                return c.get('image_url') or ''
    except Exception:
        pass
    return ''


# パック数/枚数/個数 multiplier 検出
# 括弧の有無問わず「N PACK」「NPACK」「N枚」「N個」「Nセット」「NBOX」を検出
import re as _re_mult
MULTIPLIER_PATTERN = _re_mult.compile(
    r'[(（]?\s*(\d+)\s*(PACK|パック|枚|個|セット|SET|set|BOX|ボックス|箱)\s*[)）]?',
    _re_mult.IGNORECASE,
)


_UNIT_NORM = {
    'パック': 'pack', 'pack': 'pack',
    'ボックス': 'box', 'box': 'box', '箱': 'box',
    'セット': 'set', 'set': 'set',
    '枚': 'mai', '個': 'ko',
}


def extract_multiplier_and_base(card_name):
    """カード名から数量を抽出。ベース名は正規化済(空白/括弧/日英差を吸収)
    スニダン価格は「そのアイテム1個(=1BOX/1PACK)」の価格を返す約束なので単純に×N倍する
    例:
      'ブラックボルト(2PACK)'   → (2, 'ブラックボルトpack')  # 1パック単価×2
      'ブラックボルト 3パック'    → (3, 'ブラックボルトpack')
      'メガシンフォニア(1BOX)'  → (1, 'メガシンフォニアbox')  # 1BOX価格×1
      '通常カード'             → (1, '通常カード')
    """
    if not card_name:
        return 1, card_name
    m = MULTIPLIER_PATTERN.search(card_name)
    if m:
        mult = int(m.group(1))
        unit = m.group(2).lower()
        unit_norm = _UNIT_NORM.get(unit, unit)
        base = card_name[:m.start()] + unit_norm + card_name[m.end():]
        base_norm = _re_mult.sub(r'[\s()()（）\[\]【】]+', '', base).lower()
        return mult, base_norm
    return 1, _re_mult.sub(r'[\s]+', '', card_name).lower()


def _save_card_match(base_no, card_name, rarity, tier, qty, snk_url, price, source_note, status='confirmed_by_worker'):
    """商品別カードマスタに高速append。失敗時は例外を投げて呼び出し元でst.errorさせる。
    status: confirmed_by_worker / confirmed_by_designer / provisional_review / provisional_clip
    """
    from research import open_research, clear_per_product_card_cache
    from datetime import datetime
    multiplier, _ = extract_multiplier_and_base(card_name)
    final_price = int(price) * multiplier if price else 0
    note_suffix = f' ×{multiplier}={final_price}' if multiplier > 1 else ''
    worker = st.session_state.get('_worker_name', '不明')
    ss = open_research()
    try:
        ws_per = ss.worksheet('商品別カードマスタ')
    except Exception:
        ws_per = ss.add_worksheet(title='商品別カードマスタ', rows=10000, cols=15)
        ws_per.update([['商品No', 'リライトNo', 'カード名', 'レアリティ', '賞', '数量',
                       'snkrdunk URL', '買取価格(円)', '価格取得元', 'スニダン商品名',
                       '採用方法', '更新日時']], 'A1', value_input_option='USER_ENTERED')
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    full_status = f'{status} | {source_note}{note_suffix} | by:{worker}'
    row = [base_no, '', card_name, rarity, tier, qty, snk_url, final_price,
           source_note + note_suffix, '', full_status, now]
    # 書き込み+レスポンス検証(APIが実際に反映したか確認)
    resp = ws_per.append_row(row, value_input_option='USER_ENTERED',
                             include_values_in_response=True)
    clear_per_product_card_cache()
    # ★検証: レスポンスに書き込んだURL/更新日時が入っているか
    try:
        updated_vals = (resp or {}).get('updates', {}).get('updatedData', {}).get('values', [])
        if not updated_vals:
            raise RuntimeError(f"書き込みAPIレスポンス空 (updates.updatedData.values 不在): {str(resp)[:200]}")
        wrote = updated_vals[0]
        if (str(wrote[0] if len(wrote)>0 else '') != str(base_no) or
            (wrote[6] if len(wrote)>6 else '') != snk_url):
            raise RuntimeError(
                f"書き込み内容不一致: 期待=商品{base_no}/{snk_url} 実際=商品{wrote[0] if len(wrote)>0 else '?'}/{wrote[6] if len(wrote)>6 else '?'}"
            )
    except RuntimeError:
        raise
    except Exception:
        pass  # レスポンス構造が想定外でも、append 自体が例外投げなければ書き込みは成功しているとみなす


def _fetch_price_for_url(snk_url, card_name, rarity):
    """指定URLから価格取得"""
    import re as _re_fetch
    from snkrdunk_client import fetch_recent_price, fetch_apparel_meta
    meta = fetch_apparel_meta(snk_url.rsplit("/", 1)[-1]) if "/apparels/" in snk_url else None
    target_name = (meta.get("name") or "") if meta else ""
    is_pack = bool(_re_fetch.search(r'(パック|PACK|BOX|ボックス|箱)', target_name + card_name))
    grade = "PSA10" if "PSA" in (target_name + rarity).upper() else ""
    try:
        price, msg = fetch_recent_price(snk_url, grade, is_pack=is_pack, item_name=card_name)
        return price or 0, msg
    except Exception as ex:
        return 0, f'ERR:{str(ex)[:50]}'


# ---------- メインタブ ----------
(tab_design, tab_premium, tab_template, tab_rewrite,
 tab_torecacenter, tab_dopa_list, tab_paid_list, tab_new_list,
 tab_products, tab_suggest, tab_inventory) = st.tabs([
    "📝 新規設計", "🎰 限定ガチャ",
    "📋 景品設計（競合コピー）",
    "✨ リライト商品案",
    "🎴 トレカセンター商品一覧", "🎲 DOPA商品一覧",
    "🎰 有料ガチャ一覧", "🆕 新規ガチャ一覧",
    "🎰 作成済みガチャ", "🔄 改善提案", "🃏 商品一覧"
])


with tab_design:
    import pandas as pd
    from puzzle_designer import (
        PrizeRow, DesignMeta, compute, apply_ladder,
        LADDER_LEAN_TOP, LADDER_HEAVY_TOP, to_import_rows, IMPORT_HEADERS,
        METHODS, METHOD_SHIP, METHOD_CHOICE, METHOD_PT,
    )

    st.subheader("🎯 自分で設計（パズル型・設計シート準拠）")
    st.caption(
        "単価×総口数＝売上。目玉から積んで、pt還元率・実利益率・総上乗せ率・アド確率(1/Y)・"
        "末広がり判定がライブで出ます。スプシの『ガチャ設計シート』と同じ計算＋自動判定です。"
    )

    # 賞品テーブルの列定義
    PZ_COLS = ["賞ランク", "カード名", "型番", "口数", "実価値/枚", "送料/件",
               "受取方法", "上乗せ倍率", "表示PT直接(任意)", "除外"]

    def _pz_default_df():
        return pd.DataFrame([
            {"賞ランク": "1等", "カード名": "", "型番": "", "口数": 1, "実価値/枚": 0,
             "送料/件": 500, "受取方法": METHOD_SHIP, "上乗せ倍率": 2.0, "表示PT直接(任意)": None, "除外": False},
            {"賞ランク": "その他", "カード名": "1pt交換専用", "型番": "", "口数": 100, "実価値/枚": 1,
             "送料/件": 0, "受取方法": METHOD_PT, "上乗せ倍率": 1.0, "表示PT直接(任意)": 1, "除外": False},
        ], columns=PZ_COLS)

    if "pz_df" not in st.session_state:
        st.session_state.pz_df = _pz_default_df()

    def _df_to_rows(df):
        rows = []
        for _, x in df.iterrows():
            try:
                direct = x.get("表示PT直接(任意)")
                direct = None if pd.isna(direct) or direct in ("", None) else int(float(direct))
            except (ValueError, TypeError):
                direct = None
            def _i(v, d=0):
                try:
                    return int(float(v)) if not pd.isna(v) else d
                except (ValueError, TypeError):
                    return d
            def _f(v, d=0.0):
                try:
                    return float(v) if not pd.isna(v) else d
                except (ValueError, TypeError):
                    return d
            rows.append(PrizeRow(
                rank=str(x.get("賞ランク", "") or ""), name=str(x.get("カード名", "") or ""),
                model_no=str(x.get("型番", "") or ""), count=_i(x.get("口数")),
                real_value=_i(x.get("実価値/枚")), shipping=_i(x.get("送料/件")),
                method=str(x.get("受取方法", METHOD_CHOICE) or METHOD_CHOICE),
                markup=_f(x.get("上乗せ倍率")), display_pt_direct=direct,
                exclude=bool(x.get("除外", False)),
            ))
        return rows

    # ---------- ① 基本情報 ----------
    st.markdown("### ① 基本情報")
    cat = st.radio("カテゴリー", ["ポケモン", "ワンピース"], horizontal=True, key="pz_cat")
    b1, b2, b3, b4 = st.columns(4)
    pz_title = b1.text_input("ガチャタイトル", key="pz_title", placeholder="例: 1/319の天門を開け!")
    unit_price = b2.number_input("単価(pt/口・1pt=1円)", min_value=1, value=500, step=1, key="pz_unit")
    total_tickets = b3.number_input("総口数", min_value=1, value=5000, step=100, key="pz_total")
    revenue = unit_price * total_tickets
    b4.metric("総売上(円)", f"¥{revenue:,}")
    # 自分で決める項目（購入上限・アド確率しきい値・課金額）
    b5, b6, b7 = st.columns(3)
    limit_day = b5.text_input("購入上限 口/日", value="300", key="pz_ld")
    limit_total = b6.text_input("購入上限 口/累計", value="1000", key="pz_lt")
    ad_threshold = b7.number_input("アド確率のしきい値 X(pt以上)", min_value=0, value=5500, step=500, key="pz_adx",
                                   help="このpt以上の当たりを『アド』とみなし 1/Y を計算(例:1BOX相当¥5,500)")
    # 課金ガチャ用: 引く権利(課金額)。★売上には含めない(別勘定)
    bc1, bc2, bc3 = st.columns(3)
    charge_amount = bc1.number_input("引く権利(課金額・円/回)", min_value=0, value=0, step=1000, key="pz_charge",
                                     help="1回引く権利を得るための課金額。★売上には含めません(別勘定・課金回収の原資)")
    bc2.metric("総口数×引く権利(参考)", f"¥{charge_amount*total_tickets:,}",
               help="課金分の参考値。売上には含めない")
    if charge_amount > 0:
        bc3.info("💰 課金ガチャ: 課金分は売上に非算入。S2(額面最悪)のマイナスはこの課金回収で吸収されます。")

    # 前提値（社内の経験値・通常は触らない）は畳んでおく
    with st.expander("⚙️ 詳細設定（前提値・通常は既定のままでOK）"):
        d1, d2, d3 = st.columns(3)
        cost_rate = d1.number_input("pt実質原価率", min_value=0.0, max_value=1.0, value=0.72, step=0.01, key="pz_cr",
                                    help="1ptを商品で消化する実コスト(社内経験値)。実利益率・S3の計算に使用。既定0.72")
        external = d2.number_input("外部付与見込み(売上比)", min_value=0.0, max_value=0.5, value=0.02, step=0.01, key="pz_ext",
                                   help="クーポン/紹介pt等ガチャ外で配るpt。末広がり判定に使用。既定0.02")
        progress = d3.number_input("売れ止まり想定(進捗率)", min_value=0.0, max_value=1.0, value=0.3, step=0.05, key="pz_pg",
                                   help="途中終了リスク判定用の想定売上進捗。既定0.3")

    # ---------- 計算結果パネル（基本情報の直下・下で賞品を組むとライブ更新）----------
    st.markdown("### 📊 計算結果・自動判定（下で賞品を組むとリアルタイム更新）")
    pz_result = st.container()

    # ---------- ② カードを探して追加 ----------
    st.markdown("### ② カードを探して賞品に追加（スニダン相場から価格で選ぶ）")
    sc1, sc2, sc3, sc4 = st.columns([3, 2, 2, 2])
    pz_q = sc1.text_input("カード名で検索", key="pz_search", placeholder="例: リザードン / ルフィ / VSTARユニバース BOX")
    pz_target = sc2.number_input("目標相場(円・近い順)", min_value=0, value=0, step=1000, key="pz_tp")
    pz_show = sc3.number_input("表示件数", min_value=5, max_value=100, value=15, step=5, key="pz_show")
    pz_valsrc = sc4.radio("実価値に使う価格（自動入力）", ["直近取引", "相場"],
                          key="pz_valsrc", help="➕追加時に『実価値/枚』へ自動で入る価格。表に入れた後も手修正できます")

    def _recent(it):
        return getattr(it, "price_recent", 0) or it.price

    def _min(it):
        return getattr(it, "price_min", 0) or it.price

    def _val_for(it):
        return _min(it) if pz_valsrc == "相場" else _recent(it)

    if pz_q or pz_target:
        idx_all = cached_snkrdunk_index()
        pool = [it for it in idx_all if (it.tab.startswith("ワンピ") if cat == "ワンピース" else not it.tab.startswith("ワンピ"))]
        if pz_q:
            _q = pz_q.lower()
            pool = [it for it in pool if _q in (it.name or "").lower() or _q in (it.series or "").lower()]
        if pz_target > 0:
            pool = sorted(pool, key=lambda x: abs(_val_for(x) - pz_target))
        else:
            pool = sorted(pool, key=lambda x: -_val_for(x))
        st.caption(f"候補 {len(pool):,} 件中 上位 {min(pz_show, len(pool))} 件 ｜ 価格2系統: 直近取引 と 相場（スニダン）")
        hc = st.columns([5, 2, 2, 1, 1])
        hc[0].caption("カード名"); hc[1].caption("直近取引"); hc[2].caption("相場")
        hc[3].caption("区分"); hc[4].caption("")
        for j, it in enumerate(pool[:int(pz_show)]):
            cc = st.columns([5, 2, 2, 1, 1])
            _nm = f"[{it.name}]({it.snkrdunk_url})" if getattr(it, "snkrdunk_url", "") else it.name
            cc[0].markdown(f"{_nm} `{it.series or ''}`")
            cc[1].markdown(f"¥{_recent(it):,}")
            cc[2].markdown(f"¥{_min(it):,}")
            cc[3].markdown(f"[{it.tab}]")
            if cc[4].button("➕ 追加", key=f"pz_add_{j}_{it.row_idx}"):
                newrow = {"賞ランク": "", "カード名": it.name, "型番": (it.card_no or it.series or ""),
                          "口数": 1, "実価値/枚": int(_val_for(it)), "送料/件": 500,
                          "受取方法": METHOD_CHOICE, "上乗せ倍率": 1.5, "表示PT直接(任意)": None, "除外": False}
                st.session_state.pz_df = pd.concat(
                    [st.session_state.pz_df, pd.DataFrame([newrow], columns=PZ_COLS)], ignore_index=True)
                st.rerun()

    # ---------- ③ 賞品テーブル（直接編集） ----------
    st.markdown("### ③ 賞品テーブル（直接編集・行の追加/削除OK）")
    lc1, lc2, lc3, lc4 = st.columns(4)
    if lc1.button("倍率ラダー: 上位薄(1.3/1.5/1.7/2.0)", key="pz_ladder_lean", use_container_width=True):
        rows = _df_to_rows(st.session_state.pz_df)
        apply_ladder(rows, LADDER_LEAN_TOP)
        for i, r in enumerate(rows):
            st.session_state.pz_df.iat[i, PZ_COLS.index("上乗せ倍率")] = r.markup
        st.rerun()
    if lc2.button("倍率ラダー: 上位厚(2.0/1.7/1.5/1.3)", key="pz_ladder_heavy", use_container_width=True):
        rows = _df_to_rows(st.session_state.pz_df)
        apply_ladder(rows, LADDER_HEAVY_TOP)
        for i, r in enumerate(rows):
            st.session_state.pz_df.iat[i, PZ_COLS.index("上乗せ倍率")] = r.markup
        st.rerun()
    if lc3.button("最低保証＝単価にそろえる", key="pz_floor_unit", use_container_width=True,
                  help="pt限定/floor行の表示PT直接を単価に合わせる（トレセン級お得）"):
        for i in range(len(st.session_state.pz_df)):
            if st.session_state.pz_df.iat[i, PZ_COLS.index("受取方法")] == METHOD_PT:
                st.session_state.pz_df.iat[i, PZ_COLS.index("表示PT直接(任意)")] = int(unit_price)
        st.rerun()
    if lc4.button("テーブルをリセット", key="pz_reset", use_container_width=True):
        st.session_state.pz_df = _pz_default_df()
        st.rerun()

    edited = st.data_editor(
        st.session_state.pz_df, num_rows="dynamic", use_container_width=True, key="pz_editor",
        column_config={
            "賞ランク": st.column_config.TextColumn("賞ランク", width="small"),
            "カード名": st.column_config.TextColumn("カード名", width="medium"),
            "型番": st.column_config.TextColumn("型番", width="small"),
            "口数": st.column_config.NumberColumn("口数", min_value=0, step=1, width="small"),
            "実価値/枚": st.column_config.NumberColumn("実価値/枚", min_value=0, step=100, format="%d"),
            "送料/件": st.column_config.NumberColumn("送料/件", min_value=0, step=100, format="%d"),
            "受取方法": st.column_config.SelectboxColumn("受取方法", options=METHODS, width="small"),
            "上乗せ倍率": st.column_config.NumberColumn("上乗せ倍率", min_value=0.0, step=0.1, format="%.2f"),
            "表示PT直接(任意)": st.column_config.NumberColumn("表示PT直接(任意)", min_value=0, step=1, format="%d"),
            "除外": st.column_config.CheckboxColumn("除外", width="small"),
        },
    )
    # 編集を session に反映
    if not edited.equals(st.session_state.pz_df):
        st.session_state.pz_df = edited.reset_index(drop=True)

    rows = _df_to_rows(edited)
    meta = DesignMeta(
        title=pz_title, unit_price=int(unit_price), total_tickets=int(total_tickets),
        cost_rate=float(cost_rate), external_grant=float(external),
        assumed_progress=float(progress),
        limit_per_day=limit_day, limit_total=limit_total, ad_threshold_pt=int(ad_threshold),
        charge_amount=int(charge_amount),
    )
    res = compute(meta, rows)

    # ---------- サイドバー: 設計サマリー（スクロールしても常時見える）----------
    with st.sidebar:
        _vmark = {"OK": "🟢 OK 公開可", "注意": "🟡 注意（公開可）", "NG": "🔴 NG 公開不可"}[res.verdict]
        st.markdown("### 📊 設計サマリー")
        st.caption(f"**{pz_title or '（無題）'}**　単価{unit_price:,}pt×{total_tickets:,}口")
        st.markdown(f"**{_vmark}**")
        st.metric("pt還元率", f"{res.coin_return:.1%}")
        st.metric("実利益率", f"{res.real_profit_rate:.1%}")
        st.metric(f"アド確率(≧{ad_threshold:,}pt)", f"1/{res.ad_Y:.0f}" if res.ad_Y else "-")
        st.metric("S2 全員pt(最悪)", f"¥{res.s2:,}")
        st.metric("最低保証(pt)", f"{res.min_guarantee:,}")
        _cnt_ok = "✅" if res.count_sum == total_tickets else "⚠️"
        st.caption(f"{_cnt_ok} 口数 {res.count_sum:,} / {total_tickets:,}")
        st.caption(f"末広がりEV {res.effective_pt_ev:.0%}（1.0以上NG）")
        st.divider()
        st.caption("💡 下でカード/口数を編集すると即反映。ここは固定表示なのでスクロールしても見えます。")

    # ---------- 各賞のライブ内訳 ----------
    disp = []
    for p in rows:
        if not (p.name.strip() or p.count):
            continue
        disp.append({
            "賞": p.rank, "カード名": p.name, "口数": p.count,
            "出現率": (f"{p.count/total_tickets:.3%}" if total_tickets else "-"),
            "1/X": (f"1/{total_tickets/p.count:.0f}" if p.count else "-"),
            "表示PT/枚": p.display_pt_per, "表示PT合計": p.display_pt_total,
            "実価値/枚": p.real_value, "実価値合計": p.real_value_total,
            "受取方法": p.method, "除外": "✓" if p.exclude else "",
        })
    if disp:
        st.dataframe(pd.DataFrame(disp), use_container_width=True, hide_index=True)

    with pz_result:
        # ---------- ④ 計算結果 ----------
        st.markdown("#### 計算結果（設計シートのヘッダと同じ）")
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("総売上(円)", f"¥{res.revenue:,}")
        m2.metric("◆ pt還元率", f"{res.coin_return:.2%}", help="表示PT合計 ÷ 売上（バナーの◯%）")
        m3.metric("◆ 実利益率", f"{res.real_profit_rate:.2%}", help="1 − 実価値合計 ÷ 売上")
        m4.metric("◆ 総上乗せ率", f"{res.total_markup:.2%}", help="表示PT合計 ÷ 実価値合計")
        m5, m6, m7, m8 = st.columns(4)
        m5.metric(f"アド確率(≧{ad_threshold:,}pt)", (f"1/{res.ad_Y:.0f}" if res.ad_Y else "-"),
                  help="表示PTがXpt以上の口数から算出。1/319等")
        m6.metric("最低保証(pt)", f"{res.min_guarantee:,}")
        m7.metric("表示PT合計", f"¥{res.sum_display_pt:,}")
        m8.metric("実価値合計", f"¥{res.sum_real_value:,}")

        st.markdown("#### 🛡️ 損益シナリオ（完売時）")
        s1c, s2c, s3c, s4c = st.columns(4)
        s1c.metric("S1 全員発送", f"¥{res.s1:,}")
        s2c.metric("S2 全員pt(額面最悪)", f"¥{res.s2:,}", delta=("赤字" if res.s2 < 0 else "黒字"),
                   delta_color=("inverse" if res.s2 < 0 else "normal"))
        s3c.metric("S3 全員pt(実質原価×" + f"{cost_rate:.2f})", f"¥{res.s3:,}",
                   delta=("赤字" if res.s3 < 0 else "黒字"), delta_color=("inverse" if res.s3 < 0 else "normal"))
        s4c.metric("最大損失(S1〜S3最悪)", f"¥{res.max_loss:,}")
        st.caption(f"実効pt建てEV（末広がり指標）= pt建てEV {res.pt_ev:.1%} ＋ 外部付与 {external:.0%} = **{res.effective_pt_ev:.1%}**（1.0以上でNG）")

        # ---------- ⑤ 自動判定 ----------
        st.markdown("#### 🛡️ 自動判定（OK公開可 まで直す）")
        v = res.verdict
        if v == "NG":
            st.error("■ 総合判定：**NG 公開不可** — 下の赤を直してください")
        elif v == "注意":
            st.warning("■ 総合判定：**注意（公開可）** — 内容を確認して判断")
        else:
            st.success("■ 総合判定：**OK 公開可**")
        for c in res.checks:
            icon = {"OK": "🟢", "注意": "🟡", "NG": "🔴"}[c.status]
            line = f"{icon} **{c.label}**" + (f" — {c.detail}" if c.detail else "")
            (st.error if c.status == "NG" else st.warning if c.status == "注意" else st.caption)(line)

    # ---------- ⑥ 書き出し ----------
    st.markdown("### ④ 書き出し")
    import csv as _csv
    import io as _io
    buf = _io.StringIO()
    w = _csv.writer(buf)
    w.writerow(IMPORT_HEADERS)
    for r_ in to_import_rows(rows):
        w.writerow(r_)
    st.download_button(
        "📥 管理画面取込CSVを書き出す", data=buf.getvalue().encode("utf-8-sig"),
        file_name=f"{(pz_title or 'gacha').replace('/', '_')}_import.csv", mime="text/csv",
        help="Price=実価値/枚・Redemption Points=表示PT/枚・Inventory=口数。画像URLは登録時に追加",
    )
    st.caption("💡 リライト（トレセン以外のサイト）: ②で似たカードを検索して積むか、③のテーブルに賞・カード・口数を直接手入力してください。URL登録は不要です。")


# ---------- 景品設計タブ（競合コピー型） ----------
with tab_template:
    from research import (
        load_design_template, load_premium_gachas, load_new_gachas,
        PremiumGacha, NewGacha, upsert_premium_gacha, upsert_new_gacha,
        delete_premium_gacha, delete_new_gacha,
    )
    from datetime import datetime as _dt

    st.subheader("📋 景品設計（競合コピー型）")
    st.caption("競合の商品No.を入力すると景品明細が展開され、カード・本数・上乗せ倍率を編集して還元率を試算できます")

    # 商品No検索 + 各タブからの選択
    # 商品一覧タブからの転送を受け取る
    jump_no = st.session_state.pop("_jump_to_template_no", None)
    jump_dopa = st.session_state.pop("_jump_to_template_dopa_id", None)
    if jump_no:
        st.session_state["tmpl_no_input"] = str(jump_no)
        st.info(f"📌 商品No.{jump_no} がセットされました。下の「📥 読み込み」を押してください")
    if jump_dopa:
        st.info(f"📌 DOPA商品 {jump_dopa} を「🎲 DOPA商品から」で選んで「📥 読み込み」を押してください")

    # ===== 商品ページURL貼り付けで即取込 =====
    st.markdown("##### 🔗 商品URLを貼って即取込（最速）")
    url_cols = st.columns([5, 1])
    with url_cols[0]:
        paste_url = st.text_input(
            "商品URL",
            placeholder="https://japan-toreca.com/oripa/pokemon/71871 または https://dopa-game.jp/pokemon/gacha/...",
            key="tmpl_paste_url",
        )
    with url_cols[1]:
        st.write("")
        url_fetch_btn = st.button("📥 URLから取込", type="primary", key="tmpl_url_fetch", use_container_width=True)
    if url_fetch_btn and paste_url.strip():
        from torecacenter_scraper import fetch_by_url
        from research import find_card_in_master
        from inventory import find_card_in_inventory, load_all_inventory
        with st.spinner("URLから商品情報取得中..."):
            try:
                data = fetch_by_url(paste_url.strip())
            except Exception as e:
                st.error(f"取得失敗: {e}")
                data = None
        if not data:
            st.error("URLから情報を取得できませんでした (URL形式を確認してください)")
        else:
            try:
                _inv_pool_url = load_all_inventory()
            except Exception:
                _inv_pool_url = []
            cards_data = []
            for c in data.get("cards", []):
                nm = c.get("name", "")
                rar = c.get("rarity", "")
                inv_hit = find_card_in_inventory(nm, rar, inventory=_inv_pool_url)
                if inv_hit:
                    val = inv_hit.purchase_price if inv_hit.purchase_price > 0 else inv_hit.price
                    snk = inv_hit.snkrdunk_url
                else:
                    cm = find_card_in_master(nm, rar)
                    val = int(cm.buy_price) if cm else 0
                    snk = cm.snkrdunk_url if cm else ""
                cards_data.append({
                    "賞": c.get("rank", ""),
                    "カード名": nm,
                    "レアリティ": rar,
                    "本数": int(c.get("quantity", 0)),
                    "実価値/枚(円)": val,
                    "snkrdunk URL": snk,
                    "上乗せ倍率": 0.0, "除外": False,
                })
            st.session_state["tmpl_state"] = {
                "no": str(data.get("no", "")),
                "title": data.get("title", ""),
                "url": data.get("url", ""),
                "price": int(data.get("price_per_coin", 0)),
                "total_tickets": int(data.get("total_tickets", 0)),
                "charge_amount": 0,
                "cards": cards_data,
            }
            st.session_state["tmpl_loaded_src"] = f"{data.get('source', '')}｜URL直接取込"
            st.success(f"✅ 取得完了: {data.get('title')} | カード明細{len(cards_data)}件")
            st.rerun()

    st.markdown("---")
    st.markdown("##### または、商品リストから選択")

    top_cols = st.columns([2, 2.5, 2.5, 2.5, 2])
    with top_cols[0]:
        no_input = st.text_input("トレカセンター商品No.", placeholder="例: 7401", key="tmpl_no_input")
    with top_cols[1]:
        # DOPA商品一覧から選ぶ (キャッシュ経由)
        try:
            dopa_list = cached_dopa_products()
        except Exception as ex:
            st.warning(f"DOPA一覧ロード失敗: {ex}")
            dopa_list = []
        dopa_options = ["（DOPA商品から選ぶ）"] + [f"{g.title[:40]}（{g.price}pt×{g.total_tickets:,}口）" for g in dopa_list]
        dopa_pick = st.selectbox("🎲 DOPA商品から", dopa_options, key="tmpl_dopa_pick")
    with top_cols[2]:
        # 有料ガチャから選ぶ
        try:
            paid_list = cached_premium_gachas()
        except Exception:
            paid_list = []
        paid_options = ["（有料ガチャから選ぶ）"] + [f"{g.site}｜{g.title}" for g in paid_list]
        paid_pick = st.selectbox("🎰 有料ガチャから", paid_options, key="tmpl_paid_pick")
    with top_cols[3]:
        # 新規ガチャから選ぶ
        try:
            new_list = cached_new_gachas()
        except Exception:
            new_list = []
        new_options = ["（新規ガチャから選ぶ）"] + [f"{g.site}｜{g.title}" for g in new_list]
        new_pick = st.selectbox("🆕 新規ガチャから", new_options, key="tmpl_new_pick")
    with top_cols[4]:
        st.write("")
        load_btn = st.button("📥 読み込み", type="primary", use_container_width=True, key="tmpl_load_btn")

    # ---- 読み込み処理 ----
    if load_btn:
        from research import find_card_in_master, find_card_for_product, snkrdunk_search_url
        from inventory import find_card_in_inventory, load_all_inventory

        # 在庫を1回ロードして使い回す
        try:
            _inv_pool = load_all_inventory()
        except Exception:
            _inv_pool = []

        def _resolve_card(name, rarity, base_no=None):
            """カードに対し: 在庫スプシ→商品別カードマスタ(base_no優先)→通常カードマスタDB の順で取得"""
            # 1. 在庫からマッチ
            inv_hit = find_card_in_inventory(name, rarity, inventory=_inv_pool)
            if inv_hit:
                val = inv_hit.purchase_price if inv_hit.purchase_price > 0 else inv_hit.price
                return {
                    "実価値/枚(円)": int(val),
                    "snkrdunk URL": inv_hit.snkrdunk_url,
                    "_src": f"在庫({inv_hit.tab})",
                }
            # 2. 商品別カードマスタ優先(base_noあり時) → 無ければ通常カードマスタ
            cm = find_card_for_product(base_no, name, rarity) if base_no else find_card_in_master(name, rarity)
            if cm:
                src_label = "商品別カードマスタ" if (base_no and cm.source and 'auto' in cm.source.lower() or 'manual' in cm.source.lower()) else "カードマスタ"
                return {
                    "実価値/枚(円)": int(cm.buy_price),
                    "snkrdunk URL": cm.snkrdunk_url,
                    "_src": src_label,
                }
            return {"実価値/枚(円)": 0, "snkrdunk URL": "", "_src": ""}

        loaded = None
        loaded_src = ""
        if no_input.strip():
            tpl = load_design_template(no_input.strip())
            if tpl:
                cards_data = []
                resolved_count = 0
                _base_no_for_lookup = no_input.strip()
                for c in tpl.cards:
                    r = _resolve_card(c.card_name, c.rarity, base_no=_base_no_for_lookup)
                    if r["_src"]:
                        resolved_count += 1
                    cards_data.append({
                        "賞": c.tier, "カード名": c.card_name, "レアリティ": c.rarity,
                        "本数": int(c.qty),
                        "実価値/枚(円)": r["実価値/枚(円)"],
                        "snkrdunk URL": r["snkrdunk URL"],
                        "上乗せ倍率": 0.0, "除外": False,
                    })
                loaded = {
                    "no": str(tpl.no), "title": tpl.title, "url": tpl.url,
                    "price": int(tpl.price or 0),
                    "total_tickets": int(tpl.total_tickets or 0),
                    "charge_amount": 0,
                    "cards": cards_data,
                }
                loaded_src = f"商品No.{tpl.no}（トレカセンター）"
            else:
                st.warning(f"商品No.{no_input} は景品明細・リサーチDB双方に見つかりませんでした")
        elif dopa_pick and dopa_pick != "（DOPA商品から選ぶ）":
            g = dopa_list[dopa_options.index(dopa_pick) - 1]
            from dopa_scraper import fetch_pack_detail
            cards_data = []
            try:
                detail = fetch_pack_detail(str(g.product_id).replace("DOPA-", ""), "pokemon")
                if detail and detail.get("cards"):
                    for c in detail["cards"]:
                        kind_note = ""
                        if c["kind"] == "hazure":
                            kind_note = "（外れpt還元）"
                        elif c["kind"] == "lastone":
                            kind_note = "（ラストワン）"
                        r = _resolve_card(c["name"], c["rarity"])
                        # 在庫/マスタにあればその価格、なければDOPA表示pt(1pt=1円換算)を初期値
                        real_value = r["実価値/枚(円)"] if r["_src"] else int(c["point"])
                        cards_data.append({
                            "賞": c["rank"] + kind_note,
                            "カード名": c["name"],
                            "レアリティ": c["rarity"],
                            "本数": int(c["quantity"]),
                            "実価値/枚(円)": real_value,
                            "snkrdunk URL": r["snkrdunk URL"],
                            "上乗せ倍率": 0.0,
                            "除外": False,
                        })
            except Exception as ex:
                st.warning(f"カード明細の自動取得に失敗: {ex}")
            loaded = {
                "no": g.product_id, "title": g.title, "url": g.url,
                "price": g.price, "total_tickets": g.total_tickets,
                "charge_amount": 0,
                "cards": cards_data,
            }
            note = f"ラストワン={'あり' if g.has_last_one else 'なし'}・最低保証{g.min_point}pt・残{g.remaining:,}・カード明細{len(cards_data)}件"
            loaded_src = f"DOPA｜{g.title}（{note}）"
        elif paid_pick and paid_pick != "（有料ガチャから選ぶ）":
            g = paid_list[paid_options.index(paid_pick) - 1]
            # DOPA等の有料ガチャは景品明細を独自に持つ場合あり
            from research import load_premium_gacha_prizes
            try:
                prize_cards = load_premium_gacha_prizes(g.product_id)
            except Exception:
                prize_cards = []
            cards_data = []
            for c in prize_cards:
                cm = find_card_in_master(c.card_name, c.rarity)
                cards_data.append({
                    "賞": c.tier, "カード名": c.card_name, "レアリティ": c.rarity,
                    "本数": int(c.qty),
                    "実価値/枚(円)": int(cm.buy_price) if cm else 0,
                    "snkrdunk URL": cm.snkrdunk_url if cm else "",
                    "上乗せ倍率": 0.0, "除外": False,
                })
            loaded = {
                "no": g.product_id, "title": g.title, "url": g.url,
                "price": g.price, "total_tickets": g.total_tickets,
                "charge_amount": g.charge_amount,
                "cards": cards_data,
            }
            loaded_src = f"{g.site}｜{g.title}（有料ガチャ）"
        elif new_pick and new_pick != "（新規ガチャから選ぶ）":
            g = new_list[new_options.index(new_pick) - 1]
            cards_data = []
            if g.site == "トレカセンター" and g.no:
                tpl_n = load_design_template(g.no)
                if tpl_n:
                    for c in tpl_n.cards:
                        r = _resolve_card(c.card_name, c.rarity, base_no=str(g.no))
                        cards_data.append({
                            "賞": c.tier, "カード名": c.card_name, "レアリティ": c.rarity,
                            "本数": int(c.qty),
                            "実価値/枚(円)": r["実価値/枚(円)"],
                            "snkrdunk URL": r["snkrdunk URL"],
                            "上乗せ倍率": 0.0, "除外": False,
                        })
            loaded = {
                "no": g.no, "title": g.title, "url": g.url,
                "price": g.price, "total_tickets": g.total_tickets,
                "charge_amount": 0,
                "cards": cards_data,
            }
            loaded_src = f"{g.site}｜{g.title}（新規ガチャ）"

        # リライト商品案からの転送時: 設計単価/総口数を反映 + rewrite_meta を保持
        rw_meta = st.session_state.pop("_jump_to_template_rewrite_meta", None)
        if loaded and rw_meta:
            # 単価/口数を設計値で上書き(空でなければ)
            try:
                _dp = int(float(rw_meta.get("design_price") or 0))
                if _dp > 0:
                    loaded["price"] = _dp
            except Exception:
                pass
            try:
                _tt = int(float(rw_meta.get("total_tickets") or 0))
                if _tt > 0:
                    loaded["total_tickets"] = _tt
            except Exception:
                pass
            # 上乗せ倍率は読み込み時には入れない(価格データ不完全な場合 還元率が破綻するため)
            # 代わりに「📊 リライト設計値を再適用」ボタンで価格取得後に反映できるよう meta を保持
            loaded["_rewrite_meta"] = rw_meta
            try:
                _avg_markup = float(rw_meta.get("avg_markup") or 0)
            except Exception:
                _avg_markup = 0.0
            loaded_src = f"{loaded_src}｜リライト商品案転送(目標利益率{rw_meta.get('profit_rate','')}・平均上乗せ{_avg_markup:.2f}x)"

        if loaded:
            st.session_state["tmpl_state"] = loaded
            st.session_state["tmpl_loaded_src"] = loaded_src
            st.rerun()

    state = st.session_state.get("tmpl_state")
    if not state:
        st.info("👆 商品No.を入力するか、有料ガチャ/新規ガチャから選んで「読み込み」を押してください")
    else:
        # ---- ヘッダ情報 ----
        st.markdown("---")
        # 📊 上部KPIサマリー領域(コード下部の計算結果をここに描画)
        kpi_container = st.container(border=True)
        header_row = st.columns([3, 2, 2, 2, 2])
        with header_row[0]:
            st.markdown(f"**📦 {state['title']}**")
            if state.get("url"):
                st.markdown(f"商品No: `{state['no']}`　[商品ページを開く]({state['url']})")
            else:
                st.markdown(f"商品No: `{state['no']}`")
            st.caption(f"出典: {st.session_state.get('tmpl_loaded_src', '')}")
        with header_row[1]:
            price = st.number_input("単価/口(円)", min_value=0, value=int(state["price"]),
                                    step=100, key="tmpl_price")
        with header_row[2]:
            total_tickets = st.number_input("総口数", min_value=0, value=int(state["total_tickets"]),
                                            step=1, key="tmpl_total")
        with header_row[3]:
            charge_amount = st.number_input(
                "課金額(pt買い増し相当・円)", min_value=0,
                value=int(state.get("charge_amount", 0)), step=1000, key="tmpl_charge",
                help="有料ガチャの場合の課金分。売上に上乗せされます"
            )
        with header_row[4]:
            bulk_markup = st.number_input(
                "一括上乗せ倍率", min_value=0.0, value=1.0, step=0.05,
                key="tmpl_bulk_markup",
                help="各カード行の倍率が空(0)の場合、この倍率を適用"
            )

        # ---- カード明細編集 ----
        st.markdown("##### 🎴 景品明細（編集可能）")
        # 明細空のときはテンプレ生成 + 案内
        if not state["cards"]:
            st.warning(
                "⚠️ この商品の景品明細はDB未登録です（完売済みの場合や、トレカセンター側の"
                "card_detailが取得不可なケース）。\n\n"
                "下のテンプレ行に **カード名 / レアリティ / 本数** を手入力してください。"
                "在庫スプシにカード名一致があれば、入力後「🔎 URL空欄を自動検索」"
                "または「🔄 全行の買取価格を取得」で実価値が自動補完されます。"
            )
            state["cards"] = [
                {"賞": "1等", "カード名": "", "レアリティ": "", "本数": 0,
                 "実価値/枚(円)": 0, "snkrdunk URL": "", "上乗せ倍率": 0.0, "除外": False},
                {"賞": "2等", "カード名": "", "レアリティ": "", "本数": 0,
                 "実価値/枚(円)": 0, "snkrdunk URL": "", "上乗せ倍率": 0.0, "除外": False},
                {"賞": "3等", "カード名": "", "レアリティ": "", "本数": 0,
                 "実価値/枚(円)": 0, "snkrdunk URL": "", "上乗せ倍率": 0.0, "除外": False},
                {"賞": "ラストワン", "カード名": "", "レアリティ": "", "本数": 0,
                 "実価値/枚(円)": 0, "snkrdunk URL": "", "上乗せ倍率": 0.0, "除外": False},
            ]
        # snkrdunk URL / 発送限 列がない既存stateに後付け
        for c in state["cards"]:
            c.setdefault("snkrdunk URL", "")
            c.setdefault("発送限", False)

        # ===== 💰 最大の操作: 一括取得+配分 (最目立つ位置) =====
        rw_meta_state = state.get("_rewrite_meta") if state else None
        unified_label = "💰 スニダン最新価格を一括取得"
        if rw_meta_state:
            unified_label += " ＋ 上乗せ倍率を自動配分"
        unified_row = st.columns([2.5, 2.5])
        with unified_row[0]:
            unified_fetch_btn = st.button(
                unified_label,
                type="primary",
                key="tmpl_unified_fetch",
                use_container_width=True,
                help="全カードについて:\n"
                     "1. URL空欄行→スニダン自動検索→URL+価格セット\n"
                     "2. URL有り行→スニダン最新価格を再取得\n"
                     + ("3. リライト商品案の平均上乗せ率を等別配分して各カードの上乗せ倍率に反映\n" if rw_meta_state else "")
                     + "（カード数×約1-2秒かかります）"
            )
        with unified_row[1]:
            _ncards = len(state.get("cards", []))
            _est_sec = max(int(_ncards * 1.2), 5)
            _est_label = f"⏱ 目安: 約 {_ncards} カード / {_est_sec}秒"
            if rw_meta_state:
                try:
                    _am = float(rw_meta_state.get("avg_markup") or 0)
                except Exception:
                    _am = 0.0
                _est_label += f" ｜📌 リライト設計: 平均上乗せ **{_am:.2f}x** / 目標利益率 **{rw_meta_state.get('profit_rate','?')}**"
            st.markdown(_est_label)

        # 上乗せ率手動指定→自動配分。リライトメタ or リライト商品案シートから自動取得
        _auto_markup = 0.0
        _markup_src = ""
        if rw_meta_state:
            try:
                _auto_markup = float(rw_meta_state.get('avg_markup') or 0)
                if _auto_markup > 0:
                    _markup_src = "リライト商品案転送(リライト時計算済)"
            except Exception: pass
        if _auto_markup <= 0 and state.get('no'):
            try:
                from sheets_client import get_client as _gc_m
                _ss_inv = _gc_m().open_by_key(config.get_active_inventory_sheet_id())
                _ws_re = _ss_inv.worksheet(config.TAB_REWRITE_CANDIDATES)
                _rrows = _ws_re.get_all_values()
                if _rrows:
                    _rh = _rrows[0]
                    if 'ベースNo' in _rh and '上乗せ率' in _rh:
                        _c_base = _rh.index('ベースNo')
                        _c_mk = _rh.index('上乗せ率')
                        for _r in _rrows[1:]:
                            if len(_r) > max(_c_base, _c_mk) and str(_r[_c_base]).strip() == str(state['no']).strip():
                                try:
                                    _v = float(_r[_c_mk])
                                    if _v > 0:
                                        _auto_markup = _v
                                        _markup_src = f"リライト商品案シート自動参照(ベースNo={state['no']})"
                                        break
                                except: pass
            except Exception: pass
        if _auto_markup <= 0:
            _auto_markup = 1.81
            _markup_src = "業界平均デフォルト(リライト商品案にエントリなし)"
        markup_row = st.columns([2, 1.5, 4])
        with markup_row[0]:
            manual_avg_markup = st.number_input(
                "🎯 平均上乗せ倍率", min_value=1.0, max_value=5.0,
                value=_auto_markup, step=0.05, key="tmpl_manual_markup",
                help="この値をベースに各カードのtier別上乗せを自動配分(1等2.0/2等1.7/3等1.5 等)"
            )
        with markup_row[1]:
            apply_markup_btn = st.button("📊 上乗せ自動配分", key="tmpl_apply_markup",
                                          type="primary", use_container_width=True,
                                          help="現在の実価値とtier別重みで上乗せ倍率を自動計算→各カード行に反映")
        with markup_row[2]:
            st.caption(f"📌 推奨値出典: **{_markup_src}** ／ 必要なら手動調整可")

        st.markdown("###### 細かい操作")
        action_row = st.columns([1, 1.2, 1, 1.4])
        with action_row[0]:
            fetch_all_btn = st.button("🔄 全行の買取価格を取得", key="tmpl_fetch_all",
                                       help="snkrdunk URL列に入っている全カードの買取価格を取得→マスタにも保存")
        with action_row[1]:
            auto_search_btn = st.button("🔎 URL空欄を自動検索", key="tmpl_auto_search",
                                         help="URL空の行をスニダン検索→URLセット+価格取得")
        with action_row[2]:
            search_links_btn = st.button("🔗 検索リンク表示", key="tmpl_search_links",
                                          help="各カードのスニダン検索ページリンクを下に表示")
        reapply_markup_btn = False
        if rw_meta_state:
            with action_row[3]:
                reapply_markup_btn = st.button(
                    "📊 リライト設計値のみ再適用",
                    key="tmpl_reapply_markup",
                    help="価格は触らず、現在の実価値ベースで上乗せ倍率だけ等別配分し直す",
                )

        # 上乗せ自動配分処理
        if apply_markup_btn:
            import re as _re_apm
            from collections import defaultdict as _dd_apm
            _TIER_WEIGHT = {'1等':1.6,'2等':1.3,'3等':1.0,'4等':0.8,'5等':0.7,'6等':0.7,'7等':0.7,'キリ番':1.0,'ラストワン':1.2}
            _MAX_MARKUP = {'1等':3.5,'2等':2.8,'3等':2.2,'4等':1.8,'5等':1.5,'6等':1.5,'7等':1.5,'キリ番':2.2,'ラストワン':2.5}
            _HAZURE = _re_apm.compile(r'(coin交換専用|coin\s*$|coin相当|ボーナス\s*$|交換専用|キャッシュバック|ガチャ券)')
            _cost_by_tier = _dd_apm(float)
            for _c in state["cards"]:
                _nm = str(_c.get("カード名", "") or "")
                if _HAZURE.search(_nm): continue
                _cost_by_tier[_c.get("賞", "")] += float(_c.get("実価値/枚(円)", 0) or 0) * float(_c.get("本数", 0) or 0)
            _total = sum(_cost_by_tier.values())
            if _total <= 0:
                st.error("実価値合計が0です。先にスニダン価格を取得してください")
            else:
                _target = _total * manual_avg_markup
                _weighted = sum(_cost_by_tier[t] * _TIER_WEIGHT.get(t, 1.0) for t in _cost_by_tier)
                _k = _target / _weighted if _weighted else 1
                _tm = {}
                for _t in _cost_by_tier:
                    _m = max(_TIER_WEIGHT.get(_t, 1.0) * _k, 1.0)
                    _tm[_t] = round(min(_m, _MAX_MARKUP.get(_t, 2.0)), 2)
                _new_cards = []
                for _c in state["cards"]:
                    _nc = dict(_c)
                    _nm = str(_nc.get("カード名", "") or "")
                    if _HAZURE.search(_nm):
                        _nc["上乗せ倍率"] = 0.0
                    else:
                        _nc["上乗せ倍率"] = _tm.get(_nc.get("賞", ""), round(manual_avg_markup, 2))
                    _new_cards.append(_nc)
                st.session_state["tmpl_state"]["cards"] = _new_cards
                st.success("📊 上乗せ配分: " + " / ".join(f"{t} {m}x" for t, m in _tm.items()))
                st.rerun()

        if reapply_markup_btn:
            import re as _re
            from collections import defaultdict as _defaultdict
            _TIER_WEIGHT = {
                '1等': 1.6, '2等': 1.3, '3等': 1.0, '4等': 0.8, '5等': 0.7,
                '6等': 0.7, '7等': 0.7, 'キリ番': 1.0, 'ラストワン': 1.2,
            }
            _MAX_MARKUP_BY_TIER = {
                '1等': 3.5, '2等': 2.8, '3等': 2.2, '4等': 1.8, '5等': 1.5,
                '6等': 1.5, '7等': 1.5, 'キリ番': 2.2, 'ラストワン': 2.5,
            }
            _HAZURE = _re.compile(
                r'(coin交換専用|coin\s*$|coin相当|coin引換|ポイント相当|ボーナスpt|'
                r'ボーナス\s*$|交換専用|キャッシュバック|ガチャ券|チケット\s*$|ハズレ\s*$)'
            )
            try:
                _avg_markup = float(rw_meta_state.get("avg_markup") or 0)
            except Exception:
                _avg_markup = 0.0
            try:
                _expected_cost = float(rw_meta_state.get("expected_cost") or 0)
            except Exception:
                _expected_cost = 0.0

            # 現在の実価値で cost_by_tier 計算
            _cost_by_tier = _defaultdict(float)
            _zero_count = 0
            for _c in state["cards"]:
                _name = str(_c.get("カード名", "") or "")
                _val = float(_c.get("実価値/枚(円)", 0) or 0)
                _qty = float(_c.get("本数", 0) or 0)
                if _HAZURE.search(_name):
                    continue
                if _val <= 0 and _qty > 0:
                    _zero_count += 1
                _cost_by_tier[_c.get("賞", "")] += _val * _qty
            _total_cost = sum(_cost_by_tier.values())

            # 強制配分: 平均上乗せ率があれば 必ず配分(乖離あっても警告のみ)
            if _avg_markup <= 0:
                st.error("リライト設計の平均上乗せ率が0です。シート側『上乗せ率』列を確認してください")
            else:
                _tier_markup = {}
                if _total_cost > 0:
                    _target_disp = _total_cost * _avg_markup
                    _weighted = sum(_cost_by_tier[t] * _TIER_WEIGHT.get(t, 1.0) for t in _cost_by_tier)
                    if _weighted > 0:
                        _k = _target_disp / _weighted
                        for _t in _cost_by_tier:
                            _m = _TIER_WEIGHT.get(_t, 1.0) * _k
                            _m = max(_m, 1.0)
                            _cap = _MAX_MARKUP_BY_TIER.get(_t, 2.0)
                            _tier_markup[_t] = round(min(_m, _cap), 2)
                _new_cards = []
                for _i, _c in enumerate(state["cards"]):
                    _nc = dict(_c)
                    _name = str(_nc.get("カード名", "") or "")
                    if _HAZURE.search(_name):
                        _nc["上乗せ倍率"] = 0.0
                    else:
                        _nc["上乗せ倍率"] = _tier_markup.get(_nc.get("賞", ""), round(_avg_markup, 2))
                    _new_cards.append(_nc)
                st.session_state["tmpl_state"]["cards"] = _new_cards
                if _tier_markup:
                    _msg = "✅ 上乗せ倍率を等別配分: " + " / ".join(f"{t} {m}x" for t, m in _tier_markup.items())
                else:
                    _msg = f"✅ 上乗せ倍率を一律 {_avg_markup:.2f}x で配分(実価値が全0のためtier別計算不可)"
                if _zero_count:
                    _msg += f"\n\n⚠️ 実価値0のカードが {_zero_count}行あります(これらは表示PTに寄与せず還元率が想定と異なる場合あり)"
                if _expected_cost > 0 and _total_cost > 0 and (_total_cost < _expected_cost * 0.5 or _total_cost > _expected_cost * 2.0):
                    _msg += f"\n⚠️ 想定仕入¥{int(_expected_cost):,} vs 現在¥{int(_total_cost):,} で乖離大"
                st.success(_msg)
                st.rerun()

        if state["cards"]:
            df_init = pd.DataFrame(state["cards"])
        else:
            df_init = pd.DataFrame([{
                "賞": "1等", "カード名": "", "レアリティ": "",
                "本数": 1, "実価値/枚(円)": 0, "snkrdunk URL": "",
                "上乗せ倍率": 0.0, "発送限": False, "除外": False,
            }])

        # 列順を統一
        col_order = ["賞", "カード名", "レアリティ", "本数", "実価値/枚(円)", "snkrdunk URL", "上乗せ倍率", "発送限", "除外"]
        for c in col_order:
            if c not in df_init.columns:
                if c == "snkrdunk URL":
                    df_init[c] = ""
                elif c in ("発送限", "除外"):
                    df_init[c] = False
                else:
                    df_init[c] = 0
        df_init = df_init[col_order]

        # 全カード表示(スクロールなし)のため高さを行数で動的計算
        _row_h = 35
        _header_h = 40
        _editor_height = max(_header_h + _row_h * (len(df_init) + 1) + 4, 200)
        _editor_height = min(_editor_height, 4000)

        edited = st.data_editor(
            df_init,
            num_rows="dynamic",
            use_container_width=True,
            height=_editor_height,
            column_config={
                "賞": st.column_config.TextColumn("賞", width="small"),
                "カード名": st.column_config.TextColumn("カード名", width="medium"),
                "レアリティ": st.column_config.TextColumn("レア", width="small"),
                "本数": st.column_config.NumberColumn("本数", min_value=0, step=1, width="small"),
                "実価値/枚(円)": st.column_config.NumberColumn("実価値/枚(円)", min_value=0, step=100, format="¥%d"),
                "snkrdunk URL": st.column_config.TextColumn(
                    "snkrdunk URL",
                    help="貼り直しで修正OK。空欄→🔎自動検索 / URLあり→🔄買取価格取得",
                    width="medium",
                ),
                "上乗せ倍率": st.column_config.NumberColumn("上乗せ倍率", min_value=0.0, step=0.1,
                                                  help="0なら一括倍率を適用"),
                "発送限": st.column_config.CheckboxColumn(
                    "発送限", width="small",
                    help="ONにすると当選=必ず発送(実価値=仕入原価で計算)。OFFの場合はポイント還元想定で表示PTをコスト計算",
                ),
                "除外": st.column_config.CheckboxColumn("除外", width="small"),
            },
            key="tmpl_card_editor",
        )

        # ---- 検索リンク表示 ----
        if search_links_btn:
            from research import snkrdunk_search_url
            st.markdown("##### 🔗 スニダン検索リンク（クリックして商品ページを開き、URLをコピー）")
            for _, r in edited.iterrows():
                name = str(r.get("カード名", "")).strip()
                if not name:
                    continue
                rarity = str(r.get("レアリティ", "") or "").strip()
                url = snkrdunk_search_url(name, rarity)
                st.markdown(f"- **{name}** ({rarity}): [スニダン検索]({url})")

        # ===== 一括取得+配分 (unified_fetch_btn) =====
        if unified_fetch_btn:
            from snkrdunk_client import (
                search_apparel_id_by_keyword, fetch_recent_price, fetch_apparel_meta
            )
            from research import CardMaster, upsert_card_master
            import time as _t
            import re as _re
            rows = list(edited.iterrows())
            new_rows = []
            errors = []
            search_count = 0
            fetch_count = 0
            progress = st.progress(0.0, text="スニダン一括取得中...")
            for i, (_, r) in enumerate(rows):
                new_r = dict(r)
                cur_url = str(r.get("snkrdunk URL", "") or "").strip()
                name = str(r.get("カード名", "")).strip()
                rarity = str(r.get("レアリティ", "") or "").strip()
                if not name:
                    new_rows.append(new_r)
                    progress.progress((i + 1) / max(len(rows), 1))
                    continue

                # ステップ1: URL空欄なら検索
                if not cur_url:
                    try:
                        cands = search_apparel_id_by_keyword(name, rarity, max_candidates=3)
                    except Exception as ex:
                        cands = []
                        errors.append(f"{name}: search失敗 {ex}")
                    if cands:
                        cur_url = cands[0]["url"]
                        new_r["snkrdunk URL"] = cur_url
                        search_count += 1
                    else:
                        errors.append(f"{name}: スニダン候補なし")
                        new_rows.append(new_r)
                        progress.progress((i + 1) / max(len(rows), 1),
                                          text=f"取得中 {i+1}/{len(rows)} ({name[:20]})")
                        _t.sleep(0.6)
                        continue

                # ステップ2: URL確定→最新価格取得
                meta = fetch_apparel_meta(cur_url.rsplit("/", 1)[-1]) if "/apparels/" in cur_url else None
                target_name = (meta.get("name") or "") if meta else ""
                is_pack_target = bool(_re.search(r'(パック|PACK|BOX|ボックス|箱)', target_name + name))
                if "PSA" in (target_name + rarity).upper():
                    grade_hint = "PSA10"
                else:
                    grade_hint = ""
                try:
                    price, msg = fetch_recent_price(cur_url, grade_hint, is_pack=is_pack_target)
                    if price:
                        new_r["実価値/枚(円)"] = int(price)
                        upsert_card_master(CardMaster(
                            name=name, rarity=rarity, snkrdunk_url=cur_url,
                            buy_price=int(price), source=msg, updated_at="",
                        ))
                        fetch_count += 1
                    else:
                        errors.append(f"{name} ({rarity}): 価格取れず ({msg})")
                except Exception as ex:
                    errors.append(f"{name}: 価格取得失敗 {ex}")
                new_rows.append(new_r)
                progress.progress((i + 1) / max(len(rows), 1),
                                  text=f"取得中 {i+1}/{len(rows)} ({name[:20]})")
                _t.sleep(0.3)
            progress.empty()
            st.session_state["tmpl_state"]["cards"] = new_rows

            # ステップ3: リライトメタがあれば上乗せ倍率を等別配分
            applied_markup_msg = ""
            if rw_meta_state:
                from collections import defaultdict as _defaultdict
                _TIER_WEIGHT = {
                    '1等': 1.6, '2等': 1.3, '3等': 1.0, '4等': 0.8, '5等': 0.7,
                    '6等': 0.7, '7等': 0.7, 'キリ番': 1.0, 'ラストワン': 1.2,
                }
                _MAX_MARKUP_BY_TIER = {
                    '1等': 3.5, '2等': 2.8, '3等': 2.2, '4等': 1.8, '5等': 1.5,
                    '6等': 1.5, '7等': 1.5, 'キリ番': 2.2, 'ラストワン': 2.5,
                }
                _HAZURE = _re.compile(
                    r'(coin交換専用|coin\s*$|coin相当|coin引換|ポイント相当|ボーナスpt|'
                    r'ボーナス\s*$|交換専用|キャッシュバック|ガチャ券|チケット\s*$|ハズレ\s*$)'
                )
                try:
                    _avg_markup = float(rw_meta_state.get("avg_markup") or 0)
                except Exception:
                    _avg_markup = 0.0
                try:
                    _expected_cost = float(rw_meta_state.get("expected_cost") or 0)
                except Exception:
                    _expected_cost = 0.0
                _cost_by_tier = _defaultdict(float)
                _zero_count = 0
                for _c in new_rows:
                    _nm = str(_c.get("カード名", "") or "")
                    _val = float(_c.get("実価値/枚(円)", 0) or 0)
                    _qty = float(_c.get("本数", 0) or 0)
                    if _HAZURE.search(_nm):
                        continue
                    if _val <= 0 and _qty > 0:
                        _zero_count += 1
                    _cost_by_tier[_c.get("賞", "")] += _val * _qty
                _total_cost = sum(_cost_by_tier.values())
                # 強制配分: 平均上乗せ率があれば 必ず配分(乖離あっても警告のみ)
                if _avg_markup <= 0:
                    applied_markup_msg = "⚠️ 平均上乗せ率が0のため上乗せ倍率は未配分(リライト商品案シートの『上乗せ率』列を確認)"
                else:
                    # tier別配分(実価値0カードはハズレ枠扱い=0、実価値ありは平均上乗せ率を一律)
                    _final_cards = []
                    _tier_markup_used = {}
                    if _total_cost > 0:
                        _target_disp = _total_cost * _avg_markup
                        _weighted = sum(_cost_by_tier[t] * _TIER_WEIGHT.get(t, 1.0) for t in _cost_by_tier)
                        if _weighted > 0:
                            _k = _target_disp / _weighted
                            for _t in _cost_by_tier:
                                _m = _TIER_WEIGHT.get(_t, 1.0) * _k
                                _m = max(_m, 1.0)
                                _cap = _MAX_MARKUP_BY_TIER.get(_t, 2.0)
                                _tier_markup_used[_t] = round(min(_m, _cap), 2)
                    for _c in new_rows:
                        _nc = dict(_c)
                        _nm = str(_nc.get("カード名", "") or "")
                        if _HAZURE.search(_nm):
                            _nc["上乗せ倍率"] = 0.0
                        else:
                            # 配分結果があれば tier別、なければ一律平均
                            _nc["上乗せ倍率"] = _tier_markup_used.get(
                                _nc.get("賞", ""), round(_avg_markup, 2)
                            )
                        _final_cards.append(_nc)
                    st.session_state["tmpl_state"]["cards"] = _final_cards
                    if _tier_markup_used:
                        applied_markup_msg = "📊 上乗せ倍率を等別配分: " + " / ".join(
                            f"{_t} {_m}x" for _t, _m in _tier_markup_used.items()
                        )
                    else:
                        applied_markup_msg = f"📊 上乗せ倍率を一律 {_avg_markup:.2f}x で配分(実価値が全0のためtier別計算不可)"
                    # 乖離があれば警告を追加表示
                    if _zero_count > 0:
                        applied_markup_msg += f"\n⚠️ 実価値0のカードが {_zero_count}行あります(これらは表示PTに寄与せず、還元率が想定と異なる場合があります)"
                    if _expected_cost > 0 and _total_cost > 0 and (_total_cost < _expected_cost * 0.5 or _total_cost > _expected_cost * 2.0):
                        applied_markup_msg += (
                            f"\n⚠️ 想定仕入¥{int(_expected_cost):,} vs 現在¥{int(_total_cost):,} で乖離大。"
                            f"還元率を画面下で確認し、必要なら上乗せ倍率を手動調整してください"
                        )

            _summary = f"✅ URL検索 {search_count}件 / 価格取得 {fetch_count}件 完了"
            if applied_markup_msg:
                _summary += f"\n\n{applied_markup_msg}"
            st.success(_summary)
            if errors:
                with st.expander(f"⚠️ 失敗 {len(errors)}件"):
                    for e in errors[:30]:
                        st.text(e)
            st.rerun()

        # ---- 自動URL検索（空欄行に対して） ----
        if auto_search_btn:
            from snkrdunk_client import search_apparel_id_by_keyword, fetch_recent_price
            from research import CardMaster, upsert_card_master
            import time as _t
            updated_count = 0
            errors = []
            rows = list(edited.iterrows())
            progress = st.progress(0.0, text="スニダン検索中...")
            new_rows = []
            for i, (_, r) in enumerate(rows):
                new_r = dict(r)
                cur_url = str(r.get("snkrdunk URL", "") or "").strip()
                name = str(r.get("カード名", "")).strip()
                rarity = str(r.get("レアリティ", "") or "").strip()
                if cur_url or not name:
                    new_rows.append(new_r)
                    progress.progress((i + 1) / max(len(rows), 1))
                    continue
                # DDGで検索
                try:
                    cands = search_apparel_id_by_keyword(name, rarity, max_candidates=3)
                except Exception as ex:
                    cands = []
                    errors.append(f"{name}: search失敗 {ex}")
                if cands:
                    best = cands[0]
                    found_url = best["url"]
                    new_r["snkrdunk URL"] = found_url
                    # 商品名からPSA10/パック判別
                    target_name = (best.get("name") or "")
                    import re as _re
                    is_pack_target = bool(_re.search(r'(パック|PACK|BOX|ボックス|箱)', target_name))
                    if "PSA" in target_name.upper() or "PSA" in rarity.upper():
                        grade_hint = "PSA10"
                    else:
                        grade_hint = ""
                    try:
                        price, msg = fetch_recent_price(found_url, grade_hint, is_pack=is_pack_target)
                        if price:
                            new_r["実価値/枚(円)"] = int(price)
                            upsert_card_master(CardMaster(
                                name=name, rarity=rarity, snkrdunk_url=found_url,
                                buy_price=int(price), source=msg, updated_at="",
                            ))
                            updated_count += 1
                        else:
                            errors.append(f"{name}: URLは取得したが価格取れず ({msg})")
                    except Exception as ex:
                        errors.append(f"{name}: 価格取得失敗 {ex}")
                else:
                    errors.append(f"{name}: 候補なし")
                new_rows.append(new_r)
                progress.progress((i + 1) / max(len(rows), 1),
                                  text=f"検索中... {i + 1}/{len(rows)}")
                _t.sleep(0.6)  # DDGレート対策
            progress.empty()
            st.session_state["tmpl_state"]["cards"] = new_rows
            st.success(
                f"✅ {updated_count}件の自動検索→URL+価格セット完了\n\n"
                f"💾 結果はカードマスタDBに保存されました。"
                f"**次回以降は同じカード名で景品明細を読み込めば自動入力**されます（再検索不要）"
            )
            if errors:
                with st.expander(f"⚠️ 検索できなかった/失敗 {len(errors)}件"):
                    for e in errors[:30]:
                        st.text(e)
            st.rerun()

        # ---- 一括価格取得 ----
        if fetch_all_btn:
            from snkrdunk_client import fetch_recent_price, fetch_apparel_meta
            from research import CardMaster, upsert_card_master
            import re as _re
            updated_count = 0
            errors = []
            progress = st.progress(0.0, text="価格取得中...")
            rows = list(edited.iterrows())
            new_rows = []
            for i, (_, r) in enumerate(rows):
                url = str(r.get("snkrdunk URL", "") or "").strip()
                name = str(r.get("カード名", "")).strip()
                rarity = str(r.get("レアリティ", "") or "").strip()
                new_r = dict(r)
                if url and name:
                    # URLからスニダン商品メタ取得しパック判定
                    meta = fetch_apparel_meta(url.rsplit("/", 1)[-1]) if "/apparels/" in url else None
                    target_name = (meta.get("name") or "") if meta else ""
                    is_pack_target = bool(_re.search(r'(パック|PACK|BOX|ボックス|箱)', target_name + name))
                    if "PSA" in target_name.upper() or "PSA" in rarity.upper():
                        grade_hint = "PSA10"
                    else:
                        grade_hint = ""
                    price, msg = fetch_recent_price(url, grade_hint, is_pack=is_pack_target)
                    if price:
                        new_r["実価値/枚(円)"] = int(price)
                        upsert_card_master(CardMaster(
                            name=name, rarity=rarity, snkrdunk_url=url,
                            buy_price=int(price), source=msg, updated_at="",
                        ))
                        updated_count += 1
                    else:
                        errors.append(f"{name} ({rarity}): {msg}")
                new_rows.append(new_r)
                progress.progress((i+1)/max(len(rows), 1), text=f"取得中... {i+1}/{len(rows)}")
            progress.empty()
            # stateに反映してrerun
            st.session_state["tmpl_state"]["cards"] = new_rows
            if updated_count:
                st.success(f"✅ {updated_count}件の価格を取得・マスタ保存しました")
            if errors:
                with st.expander(f"⚠️ 取得失敗 {len(errors)}件"):
                    for e in errors[:30]:
                        st.text(e)
            st.rerun()

        # ---- 計算 ----
        df_calc = edited.fillna({
            "本数": 0, "実価値/枚(円)": 0, "上乗せ倍率": 0.0, "発送限": False, "除外": False
        })
        df_calc["本数"] = pd.to_numeric(df_calc["本数"], errors="coerce").fillna(0).astype(int)
        df_calc["実価値/枚(円)"] = pd.to_numeric(df_calc["実価値/枚(円)"], errors="coerce").fillna(0).astype(int)
        df_calc["上乗せ倍率"] = pd.to_numeric(df_calc["上乗せ倍率"], errors="coerce").fillna(0.0).astype(float)
        df_calc["発送限"] = df_calc["発送限"].astype(bool)
        df_calc["除外"] = df_calc["除外"].astype(bool)

        # 各行の倍率（0なら一括倍率を使う）
        df_calc["適用倍率"] = df_calc["上乗せ倍率"].where(df_calc["上乗せ倍率"] > 0, bulk_markup)
        df_calc["表示PT/枚"] = (df_calc["実価値/枚(円)"] * df_calc["適用倍率"]).round().astype(int)
        df_calc["表示PT合計"] = df_calc["表示PT/枚"] * df_calc["本数"]
        df_calc["実価値合計"] = df_calc["実価値/枚(円)"] * df_calc["本数"]
        # 弊社コスト = 発送限ON→実価値/枚、OFF→表示PT/枚 (最悪ケース=ポイント還元で払い戻し想定)
        df_calc["コスト/枚"] = df_calc["実価値/枚(円)"].where(df_calc["発送限"], df_calc["表示PT/枚"]).astype(int)
        df_calc["コスト合計"] = df_calc["コスト/枚"] * df_calc["本数"]
        # 除外を反映
        active = df_calc[~df_calc["除外"]]

        total_card_qty = int(active["本数"].sum())
        total_real = int(active["実価値合計"].sum())
        total_pt_view = int(active["表示PT合計"].sum())
        # 発送限別コスト
        ship_active = active[active["発送限"]]
        point_active = active[~active["発送限"]]
        ship_cost = int(ship_active["実価値合計"].sum())      # 実発送分の仕入れ
        point_cost = int(point_active["表示PT合計"].sum())    # ポイント還元想定分
        total_cost_worst = ship_cost + point_cost             # 弊社の実質支出 (最悪ケース)

        revenue = price * total_tickets + charge_amount
        profit = revenue - total_cost_worst                   # 弊社の得(円)
        profit_rate = (profit / revenue) if revenue else 0    # 弊社の利益率
        real_return = (total_real / revenue) if revenue else 0
        coin_return = (total_pt_view / revenue) if revenue else 0
        markup_diff = coin_return - real_return

        # ---- 結果表示 (画面上部の kpi_container に描画) ----
        with kpi_container:
            st.markdown("##### 📊 計算結果（画面最上部固定）")

            # 1段目: 売上/表示PT/コスト/利益
            m1 = st.columns(4)
            m1[0].metric(
                "売上",
                f"¥{revenue:,}",
                help=f"単価 ¥{price:,} × {total_tickets:,}口" + (f" + 課金 ¥{charge_amount:,}" if charge_amount else "")
            )
            m1[1].metric(
                "表示ポイント数(発行総額)",
                f"{total_pt_view:,}pt",
                help="全カードの表示PT合計 = ポイント還元された場合に払い戻す最悪コスト"
            )
            m1[2].metric(
                "弊社コスト(最悪ケース)",
                f"¥{total_cost_worst:,}",
                help=f"発送限ON={ship_cost:,}円(実仕入) + 発送限OFF={point_cost:,}円(表示PT還元想定)"
            )
            m1[3].metric(
                "弊社の得",
                f"¥{profit:,}",
                delta=f"{profit_rate:.1%}",
                delta_color="normal" if profit >= 0 else "inverse",
                help="売上 − 弊社コスト(最悪ケース)"
            )

            # 2段目: 還元率/上乗せ差/仕入内訳/カード枚数
            m2 = st.columns(4)
            m2[0].metric(
                "顧客還元率(表示PT基準)",
                f"{coin_return:.1%}",
                help="顧客が見る還元率 = 表示PT合計 / 売上"
            )
            m2[1].metric(
                "実仕入還元率(発送分のみ)",
                f"{real_return:.1%}",
                help="実仕入合計(全カード実価値) / 売上 — 参考値"
            )
            m2[2].metric(
                "上乗せ差分",
                f"+{markup_diff:.1%}",
                help="顧客還元率 − 実仕入還元率"
            )
            m2[3].metric(
                "総口数 vs カード本数",
                f"{total_tickets:,} / {total_card_qty:,}",
                delta=f"差 {total_tickets - total_card_qty:+,}枚" if total_tickets != total_card_qty else "一致",
                delta_color="off" if total_tickets == total_card_qty else "inverse"
            )

            # アラート
            if total_tickets != total_card_qty and total_card_qty > 0:
                st.warning(f"⚠️ 総口数 {total_tickets:,} と カード本数 {total_card_qty:,} が一致しません")
            if profit < 0:
                st.error(f"❌ 弊社の得がマイナス（{profit:,}円）。上乗せ倍率を下げるか、発送限ONのカードを減らしてください")
            if coin_return > 1.0:
                st.error(f"❌ 顧客還元率が100%超え（{coin_return:.1%}）。上乗せ倍率を見直してください")

            # 発送限の内訳
            n_ship = int(ship_active["本数"].sum())
            n_point = int(point_active["本数"].sum())
            st.caption(
                f"📦 発送限ON: {n_ship:,}枚(実仕入 ¥{ship_cost:,}) ／ "
                f"OFF: {n_point:,}枚(ポイント還元想定 ¥{point_cost:,})"
            )

        # 実価値0のカードチェック (販売前に必ず潰すべき=価格未取得 or 除外扱い)
        import re as _re_zero
        _HZ = _re_zero.compile(r'(coin交換専用|coin\s*$|coin相当|ボーナス\s*$|交換専用|キャッシュバック|ガチャ券)')
        zero_value_rows = active[(active["実価値/枚(円)"] == 0) & ~active["カード名"].fillna('').str.contains(_HZ, regex=True, na=False) & (active["本数"] > 0)]
        if not zero_value_rows.empty:
            st.error(
                f"🚫 **実価値¥0のカードが {len(zero_value_rows)}行あります - 販売不可状態です**\n\n"
                f"全件「🖼 カード照合」タブで正しいスニダンURLを登録するか、本当にハズレ枠なら明示してください。"
                f"このまま販売すると利益率計算が不正確で原価割れリスク"
            )
            with st.expander(f"⚠️ 実価値0のカード一覧 ({len(zero_value_rows)}行)", expanded=True):
                st.dataframe(zero_value_rows[["賞", "カード名", "レアリティ", "本数"]], use_container_width=True, hide_index=True)

        # ---- 出現率(参考) ----
        if total_card_qty > 0:
            df_calc["出現率"] = (df_calc["本数"] / total_card_qty * 100).round(2).astype(str) + "%"
            with st.expander("📋 行ごとの内訳(表示PT/コスト/発送限)"):
                show_cols = ["賞", "カード名", "レアリティ", "本数", "出現率",
                             "実価値/枚(円)", "適用倍率", "表示PT/枚", "表示PT合計",
                             "実価値合計", "発送限", "コスト/枚", "コスト合計", "除外"]
                st.dataframe(df_calc[show_cols], use_container_width=True, hide_index=True)

        # ---- カード確認(設計者がスニダンURLを最終チェック) ----
        from research import load_per_product_card_index as _lppci, clear_per_product_card_cache as _cppc
        from research import load_card_master_index as _lcmi
        _cppc()
        _per = _lppci()
        _master = _lcmi()  # カードマスタDB(name|rarity) - 依頼者入力など
        cur_base_no = str(state.get('no', '')).strip()
        # この商品のカードを分類
        check_items = []      # 確認待ち(仮採用 or worker確定 or カードマスタ由来)
        confirmed_items = []  # 設計者確定済
        not_in_db_items = []  # 一切DBに登録なし
        for ri, row in df_calc.iterrows():
            cn = str(row.get('カード名', '')).strip()
            rar = str(row.get('レアリティ', '')).strip()
            if not cn: continue
            key = f'{cur_base_no}|{cn}|{rar}'.lower()
            cm = _per.get(key)
            entry = {'ri': ri, 'row': row, 'cn': cn, 'rar': rar, 'cm': cm, 'src_type': ''}
            if cm:
                src_low = (cm.source or '').lower()
                if 'confirmed_by_designer' in src_low:
                    confirmed_items.append(entry)
                else:
                    entry['src_type'] = 'per'
                    check_items.append(entry)
            else:
                # カードマスタDB(name|rarity)で代替検索 → 仮採用扱い
                master_key = f'{cn}|{rar}'.lower()
                mcm = _master.get(master_key)
                if mcm and mcm.snkrdunk_url.strip().startswith('http'):
                    # CardMaster型を per と同じ形で扱う
                    from types import SimpleNamespace
                    entry['cm'] = SimpleNamespace(
                        snkrdunk_url=mcm.snkrdunk_url,
                        buy_price=mcm.buy_price,
                        source=f'master_db | {mcm.source}',
                    )
                    entry['src_type'] = 'master'
                    check_items.append(entry)
                else:
                    not_in_db_items.append(entry)

        with st.expander(
            f"✅ カード単位でチェック → 確定  (確認待ち {len(check_items)} / 確定済 {len(confirmed_items)} / DB登録なし {len(not_in_db_items)})",
            expanded=(len(check_items) > 0),
        ):
            st.caption(
                "🟡仮採用(CLIP)/画像選定済 = 設計時に必ず最終確認して「✅確認OK」を押して確定 / "
                "✅確定済 = 設計者承認済 / "
                "🔴DB登録なし = カード照合タブで未登録"
            )
            if check_items:
                st.markdown(f"#### 🟡 確認待ち {len(check_items)}件")
                _tmpl_url = state.get('url', '')
                # 確認済みローカルキャッシュ (rerun削減用)
                if '_design_confirmed_local' not in st.session_state:
                    st.session_state['_design_confirmed_local'] = set()
                _done_local = st.session_state['_design_confirmed_local']
                # 一括採用ボタン: ローカル確認待ちを全部一気に確定
                if st.button(f"⚡ 全{len(check_items)}件を一括で✅確定", key=f"bulk_confirm_{cur_base_no}",
                              help="表示中のカードすべてを設計時確定として登録(=スニダンURL確認済の前提)"):
                    if not st.session_state.get('_worker_name'):
                        st.session_state['_worker_name'] = '設計者'
                    _ok_cnt = 0
                    _err_cnt = 0
                    with st.spinner(f"{len(check_items)}件を一括登録中..."):
                        for e in check_items:
                            try:
                                cm = e['cm']
                                _save_card_match(cur_base_no, e['cn'], e['rar'], str(e['row']['賞']),
                                                 int(e['row']['本数']), cm.snkrdunk_url, cm.buy_price,
                                                 '一括確定', status='confirmed_by_designer')
                                _done_local.add(f"{cur_base_no}|{e['cn']}|{e['rar']}".lower())
                                _ok_cnt += 1
                            except Exception as ex:
                                _err_cnt += 1
                    st.success(f"✅ {_ok_cnt}件確定 / エラー{_err_cnt}件")
                    st.rerun()

                for e in check_items:
                    e_local_key = f"{cur_base_no}|{e['cn']}|{e['rar']}".lower()
                    if e_local_key in _done_local:
                        st.caption(f"✅ {e['row']['賞']} {e['cn']} ({e['rar']}) - 確定済(今セッション)")
                        continue
                    cm = e['cm']
                    src_low = (cm.source or '').lower()
                    if e.get('src_type') == 'master':
                        label = "🟠 仮採用(在庫スプシ/マスタDB由来)"
                    elif 'clip' in src_low:
                        label = "🟡 仮採用(CLIP)"
                    elif 'review' in src_low:
                        label = "⏸ ワーカー要確認"
                    elif 'confirmed_by_worker' in src_low or 'manual_ui' in src_low or 'manual_url' in src_low:
                        label = "🟢 画像選定済"
                    else:
                        label = f"❓ {cm.source[:20]}"
                    # トレカセンター画像取得 (キャッシュ済)
                    tc_img = _get_tc_image(_tmpl_url, e['cn'], e['rar']) if _tmpl_url else ''
                    ck_cols = st.columns([0.8, 2.2, 1.6, 1.6, 0.8, 1, 1])
                    with ck_cols[0]:
                        if tc_img:
                            st.image(tc_img, width=80)
                    with ck_cols[1]:
                        st.markdown(f"**{e['row']['賞']} {e['cn']}**")
                        st.caption(f"{e['rar']} ｜ {label}")
                    with ck_cols[2]:
                        if _tmpl_url:
                            st.link_button("🎴 競合", _tmpl_url, use_container_width=True)
                    with ck_cols[3]:
                        if cm.snkrdunk_url:
                            st.link_button("🔗 スニダン", cm.snkrdunk_url, use_container_width=True)
                        else:
                            st.caption("URLなし")
                    with ck_cols[4]:
                        st.caption(f"¥{int(cm.buy_price):,}")
                    with ck_cols[5]:
                        if st.button("✅確認OK", key=f"confirm_{cur_base_no}_{e['cn']}_{e['rar']}_{e['ri']}",
                                      use_container_width=True, type="primary"):
                            if not st.session_state.get('_worker_name'):
                                st.session_state['_worker_name'] = '設計者'
                            try:
                                _save_card_match(cur_base_no, e['cn'], e['rar'], str(e['row']['賞']),
                                                 int(e['row']['本数']), cm.snkrdunk_url, cm.buy_price,
                                                 f"設計時確認OK", status='confirmed_by_designer')
                                _done_local.add(e_local_key)
                                st.toast(f"✅ {e['cn']} 確定", icon="✅")
                                st.rerun()
                            except Exception as ex:
                                st.error(f"保存失敗: {str(ex)[:80]}")
                    with ck_cols[6]:
                        # 修正用ポップオーバー (URL + レアリティ + カード名)
                        with st.popover("✏️ 修正", use_container_width=True):
                            with st.form(key=f"fix_form_{cur_base_no}_{e['ri']}", clear_on_submit=False):
                                st.caption(f"現在: {e['cn']} ({e['rar']})")
                                new_name = st.text_input("カード名 (誤りなら修正)", value=e['cn'])
                                new_rarity = st.text_input("レアリティ (例: SAR/CSR/CHR/SR/SSR/HR/UR/PROMO等)",
                                                            value=e['rar'])
                                new_url = st.text_input("新スニダンURL",
                                                         placeholder="https://snkrdunk.com/apparels/...")
                                _submitted_design = st.form_submit_button("💾 修正して確定", type="primary", use_container_width=True)
                            if _submitted_design:
                                url = new_url.strip()
                                if not url.startswith('http'):
                                    st.warning("URLを正しく入力してください")
                                else:
                                    if not st.session_state.get('_worker_name'):
                                        st.session_state['_worker_name'] = '設計者'
                                    final_name = (new_name or e['cn']).strip()
                                    final_rar = (new_rarity or e['rar']).strip()
                                    try:
                                        price, msg = _fetch_price_for_url(url, final_name, final_rar)
                                        if price <= 0:
                                            st.error(f"価格0で取得失敗 ({msg[:50]})。URL再確認してください")
                                        else:
                                            _save_card_match(cur_base_no, final_name, final_rar, str(e['row']['賞']),
                                                             int(e['row']['本数']), url, price,
                                                             f"設計時修正(name/rar変更)",
                                                             status='confirmed_by_designer')
                                            # tmpl_state["cards"] の該当行も更新(見た目も変える)
                                            for _ci, _c in enumerate(st.session_state.get('tmpl_state', {}).get('cards', [])):
                                                if (str(_c.get('カード名', '')).strip() == e['cn'] and
                                                    str(_c.get('レアリティ', '')).strip() == e['rar'] and
                                                    str(_c.get('賞', '')).strip() == str(e['row']['賞'])):
                                                    _c['カード名'] = final_name
                                                    _c['レアリティ'] = final_rar
                                                    _c['実価値/枚(円)'] = price
                                                    _c['snkrdunk URL'] = url
                                                    break
                                            _done_local.add(e_local_key)
                                            change_note = ""
                                            if final_name != e['cn']: change_note += f" / カード名:{e['cn']}→{final_name}"
                                            if final_rar != e['rar']: change_note += f" / レア:{e['rar']}→{final_rar}"
                                            st.success(f"修正確定 ¥{price:,}{change_note}")
                                            st.rerun()
                                    except Exception as ex:
                                        st.error(f"エラー: {str(ex)[:80]}")
            if confirmed_items:
                st.markdown(f"#### ✅ 設計者確定済 {len(confirmed_items)}件")
                for e in confirmed_items:
                    cm = e['cm']
                    st.caption(f"✅ {e['row']['賞']} {e['cn']} ({e['rar']}) - ¥{int(cm.buy_price):,}")
            if not_in_db_items:
                st.markdown(f"#### 🔴 商品別カードマスタDB未登録 {len(not_in_db_items)}件")
                st.warning("以下のカードは商品別カードマスタDBに未登録です。カード照合タブで登録してください")
                for e in not_in_db_items[:20]:
                    st.caption(f"🔴 {e['row']['賞']} {e['cn']} ({e['rar']})")
                if len(not_in_db_items) > 20:
                    st.caption(f"... 他{len(not_in_db_items) - 20}件")

        # ---- アクション ----
        st.markdown("---")
        act_cols = st.columns([1, 1, 1, 3])
        with act_cols[0]:
            if st.button("🔄 読み込みやり直し", key="tmpl_reset"):
                st.session_state.pop("tmpl_state", None)
                st.rerun()


# ---------- リライト商品案タブ ----------
@st.cache_data(ttl=180)
def _load_rewrite_candidates():
    """リライト商品案タブから全件取得"""
    try:
        from sheets_client import get_client
        gc = get_client()
        ss = gc.open_by_key(config.get_active_inventory_sheet_id())
        ws = ss.worksheet(config.TAB_REWRITE_CANDIDATES)
        rows = ws.get_all_values()
        if not rows or len(rows) < 2:
            return [], []
        return rows[0], rows[1:]
    except Exception as e:
        st.error(f"リライト商品案読込エラー: {e}")
        return [], []


with tab_rewrite:
    st.subheader("✨ リライト商品案")
    st.caption("トレカセンター完売商品をベースにリライトした商品案。行を選択→「📋景品設計に転送」で全賞構成展開+価格取得+計算→ユーザーが調整→保存")

    headers, data_rows = _load_rewrite_candidates()
    if not headers or not data_rows:
        st.info("リライト商品案がありません。スクリプトで一括投入してください。")
    else:
        # データフレーム化
        df_rw = pd.DataFrame(data_rows, columns=headers)
        # 数値列に変換
        for c in ["No", "単価(coin)", "総口数", "設計単価(coin)", "設計売上(円)", "実仕入(円)"]:
            if c in df_rw.columns:
                df_rw[c] = pd.to_numeric(df_rw[c], errors="coerce")

        # フィルタ
        fc = st.columns([3, 2, 2, 1])
        with fc[0]:
            rw_search = st.text_input("🔍 タイトル / No.", key="rw_search",
                                       placeholder="例: ピカチュウ、47、リーリエ")
        with fc[1]:
            rw_status_opts = ["全て"] + sorted([s for s in df_rw.get("調整ステータス", pd.Series([])).dropna().unique() if s])
            rw_status = st.selectbox("ステータス", rw_status_opts, key="rw_status")
        with fc[2]:
            rw_judge_opts = ["全て"]
            if "実利益率" in df_rw.columns:
                rw_judge_opts += ["利益率45-50%(達成)", "利益率<45%(不足)", "利益率>50%(過剰)", "利益率<0%(赤字)"]
            rw_judge = st.selectbox("利益率フィルタ", rw_judge_opts, key="rw_judge")
        with fc[3]:
            if st.button("🔄 再読込", key="rw_reload"):
                _load_rewrite_candidates.clear()
                st.rerun()

        filtered = df_rw.copy()
        if rw_search:
            s = rw_search.strip().lower()
            mask = filtered["サムネタイトル"].fillna("").str.lower().str.contains(s, na=False) \
                 | filtered["No"].fillna(0).astype(str).str.contains(s, na=False) \
                 | filtered.get("ベースNo", pd.Series([""]*len(filtered))).fillna("").str.contains(s, na=False)
            filtered = filtered[mask]
        if rw_status != "全て" and "調整ステータス" in filtered.columns:
            filtered = filtered[filtered["調整ステータス"] == rw_status]
        if rw_judge != "全て" and "実利益率" in filtered.columns:
            # %記号除去して数値化
            pr_num = filtered["実利益率"].astype(str).str.rstrip("%").replace("", "0").astype(float)
            if rw_judge == "利益率45-50%(達成)":
                filtered = filtered[(pr_num >= 45) & (pr_num <= 50)]
            elif rw_judge == "利益率<45%(不足)":
                filtered = filtered[pr_num < 45]
            elif rw_judge == "利益率>50%(過剰)":
                filtered = filtered[pr_num > 50]
            elif rw_judge == "利益率<0%(赤字)":
                filtered = filtered[pr_num < 0]

        st.markdown(f"**{len(filtered)}件** / 全{len(df_rw)}件")

        if not filtered.empty:
            # 主要列だけ表示(全列見たければ「全列表示」ボタン)
            show_all = st.checkbox("全列表示", value=False, key="rw_show_all")
            if show_all:
                show_df = filtered
            else:
                pref_cols = ["No", "サムネタイトル", "ベースNo", "元タイトル",
                            "単価(coin)", "総口数", "総還元率", "最低保証",
                            "実利益率", "上乗せ率", "調整ステータス", "ステータス"]
                show_cols = [c for c in pref_cols if c in filtered.columns]
                show_df = filtered[show_cols]

            st.caption("⬇️ **行をクリックして選択** → 下に転送ボタンが表示されます")
            ev = st.dataframe(
                show_df.reset_index(drop=True),
                use_container_width=True, hide_index=True,
                on_select="rerun", selection_mode="single-row",
                key="rw_table",
            )
            sel_rows = (ev.selection or {}).get("rows", [])
            if sel_rows:
                sel = filtered.iloc[sel_rows[0]]
                sa, sb, sc = st.columns([4, 2, 2])
                with sa:
                    st.success(f"選択中: No.{int(sel['No'])}｜{sel['サムネタイトル']} (ベース{sel.get('ベースNo','')})")
                with sb:
                    if st.button("📋 景品設計に転送", type="primary", key="rw_to_template",
                                  use_container_width=True):
                        st.session_state["_jump_to_template_no"] = str(sel.get('ベースNo', ''))
                        # 設計値(設計単価/上乗せ率)も引き渡し → 読み込み時にカード明細に反映
                        st.session_state["_jump_to_template_rewrite_meta"] = {
                            "title": str(sel.get("サムネタイトル", "")),
                            "design_price": str(sel.get("設計単価(coin)", "") or sel.get("単価(coin)", "")),
                            "total_tickets": str(sel.get("総口数", "")),
                            "avg_markup": str(sel.get("上乗せ率", "")),
                            "profit_rate": str(sel.get("実利益率", "")),
                            "expected_cost": str(sel.get("実仕入(円)", "")),
                        }
                        st.toast(f"📋 景品設計タブで「📥 読み込み」を押してください (ベース{sel.get('ベースNo','')} セット済)")
                with sc:
                    st.metric("利益率", str(sel.get("実利益率", "?")), help="目標45-50%")
        else:
            st.info("該当する商品がありません")


# ---------- トレカセンター商品一覧タブ ----------
with tab_torecacenter:
    st.subheader("🎴 トレカセンター商品一覧")
    st.caption(f"リサーチDB完売オリパ {len(cached_references()):,}件。検索→「📋設計する」で景品設計タブに自動転送")

    # 最新情報取得ボタン
    sync_col = st.columns([2, 1, 4])
    with sync_col[0]:
        tc_sync_btn = st.button("🔄 トレカセンター販売中の最新を取得",
                                 help="japan-toreca.com APIから現在販売中の商品を取得。既存DBにないものを追加します")
    with sync_col[1]:
        tc_sync_cat = st.selectbox("カテゴリ", ["pokemon", "onepiece", "yugioh", "hobby", "ws_tcg", "mtg", "duel_masters", "popmart"],
                                    key="tc_sync_cat")
    if tc_sync_btn:
        with st.spinner(f"トレカセンター {tc_sync_cat} 取込中..."):
            try:
                from torecacenter_scraper import sync_to_research_db
                result = sync_to_research_db(category=tc_sync_cat, verbose=False)
                cached_references.clear()
                cached_new_gachas.clear()
                # 結果を session_state に保存して再描画後も表示
                st.session_state["_tc_sync_result"] = result
                st.rerun()
            except Exception as e:
                st.error(f"取込エラー: {e}")

    # 同期結果表示 (session_stateから)
    tc_result = st.session_state.get("_tc_sync_result")
    if tc_result:
        st.success(
            f"✅ 取得 {tc_result['fetched']}件 / **{tc_result['added']}件 新規追加** "
            f"/ うち 🆕新規限定 {tc_result.get('new_gacha_added', 0)}件 自動振り分け"
        )
        items = tc_result.get("added_items", [])
        if items:
            with st.expander(f"📋 追加された {len(items)}件 一覧", expanded=True):
                df_added = pd.DataFrame([{
                    "No": x["no"], "タイトル": x["title"],
                    "単価(coin)": x["price"], "総口数": x["total_tickets"],
                    "残数": x.get("left_cards", 0),
                    "URL": x["url"], "タグ": x.get("tags", ""),
                } for x in items])
                st.dataframe(df_added, use_container_width=True, hide_index=True,
                             column_config={"URL": st.column_config.LinkColumn("URL")})
        if st.button("× 結果を閉じる", key="tc_close_result"):
            st.session_state.pop("_tc_sync_result", None)
            st.rerun()

    refs_all = cached_references()
    f_cols = st.columns([3, 1, 1, 1])
    with f_cols[0]:
        tc_search = st.text_input("🔍 タイトル / No.", key="tc_search",
                                   placeholder="例: ピカチュウ、3532、JACKPONCHO")
    with f_cols[1]:
        tc_min_price = st.number_input("単価下限(pt)", min_value=0, value=0, step=100, key="tc_min_price")
    with f_cols[2]:
        tc_max_price = st.number_input("単価上限(pt)", min_value=0, value=0, step=100,
                                        key="tc_max_price", help="0=制限なし")
    with f_cols[3]:
        tc_limit = st.number_input("表示上限", min_value=20, max_value=2000, value=100, step=50, key="tc_limit")

    filtered_refs = refs_all
    if tc_search:
        s = tc_search.strip().lower()
        filtered_refs = [r for r in filtered_refs if s in r.title.lower() or s in str(r.no)]
    if tc_min_price > 0:
        filtered_refs = [r for r in filtered_refs if r.price_per_coin >= tc_min_price]
    if tc_max_price > 0:
        filtered_refs = [r for r in filtered_refs if r.price_per_coin <= tc_max_price]

    st.markdown(f"**{len(filtered_refs):,}件** / 全{len(refs_all):,}件 (上位{min(tc_limit, len(filtered_refs))}件表示)")

    if filtered_refs:
        view_refs = filtered_refs[:int(tc_limit)]
        df_tc = pd.DataFrame([{
            "No": r.no, "タイトル": r.title,
            "単価(coin)": r.price_per_coin,
            "総口数": r.total_tickets,
            "完売日": r.sold_date,
            "URL": r.url,
            "タグ": r.tags,
        } for r in view_refs])

        st.caption("⬇️ **行をクリックして選択** → 下に転送ボタンが表示されます")
        ev = st.dataframe(
            df_tc, use_container_width=True, hide_index=True,
            on_select="rerun", selection_mode="single-row",
            column_config={"URL": st.column_config.LinkColumn("URL")},
            key="tc_table",
        )
        sel_rows = (ev.selection or {}).get("rows", [])
        if sel_rows:
            sel = view_refs[sel_rows[0]]
            sa, sb = st.columns([4, 2])
            with sa:
                st.success(f"選択中: No.{sel.no}｜{sel.title}（¥{sel.price_per_coin:,}×{sel.total_tickets:,}口）")
            with sb:
                if st.button("📋 景品設計に転送", type="primary", key="tc_to_template_quick",
                              use_container_width=True):
                    st.session_state["_jump_to_template_no"] = str(sel.no)
                    st.toast(f"📋 景品設計タブで「📥 読み込み」を押してください (No.{sel.no} セット済)")


# ---------- DOPA商品一覧タブ ----------
with tab_dopa_list:
    _ldopa = cached_dopa_products  # cached経由

    st.subheader("🎲 DOPA商品一覧")
    st.caption("DOPAの全商品（参考データベース）。「📋 景品設計」タブから選んで設計に流用できます")

    sync_cols = st.columns([2, 1, 4])
    with sync_cols[0]:
        sync_btn = st.button("🔄 DOPAから最新一覧を取込",
                              help="DOPAから現在表示中の全件を取得")
    if sync_btn:
        with st.spinner("DOPAから取込中..."):
            try:
                from dopa_scraper import sync_dopa_to_sheets
                result = sync_dopa_to_sheets(category="pokemon", sleep_sec=0.3, verbose=False)
                cached_dopa_products.clear()
                cached_premium_gachas.clear()
                cached_new_gachas.clear()
                st.session_state["_dopa_sync_result"] = result
                st.rerun()
            except Exception as e:
                st.error(f"DOPA取込エラー: {e}")

    # 同期結果表示
    dopa_result = st.session_state.get("_dopa_sync_result")
    if dopa_result:
        items = dopa_result.get("added_items", [])
        added_new = sum(1 for x in items if x.get("is_new"))
        added_paid = sum(1 for x in items if x.get("is_paid"))
        st.success(
            f"✅ 取得 {dopa_result['fetched']}件 / 全DOPA商品 {dopa_result['dopa_products']}件\n\n"
            f"**{dopa_result.get('added', 0)}件 新規追加** "
            f"(うち 🆕新規限定 {added_new}件 / 🎰有料 {added_paid}件)\n\n"
            f"📊 DB全体: 🆕新規限定 {dopa_result['new_gachas']}件 / 🎰有料 {dopa_result['premium_gachas']}件"
        )
        if items:
            with st.expander(f"📋 追加された {len(items)}件 一覧", expanded=True):
                df_added = pd.DataFrame([{
                    "商品ID": x["product_id"], "タイトル": x["title"],
                    "単価(pt)": x["price"], "総口数": x["total_tickets"],
                    "残口数": x["remaining"],
                    "🆕新規限定": "○" if x.get("is_new") else "",
                    "🎰有料限定": "○" if x.get("is_paid") else "",
                    "URL": x["url"],
                } for x in items])
                st.dataframe(df_added, use_container_width=True, hide_index=True,
                             column_config={"URL": st.column_config.LinkColumn("URL")})
        else:
            st.info("既存DBと差分なし(新商品リリースなし)")
        if st.button("× 結果を閉じる", key="dopa_close_result"):
            st.session_state.pop("_dopa_sync_result", None)
            st.rerun()
    try:
        dopa_items = _ldopa()
    except Exception as e:
        st.error(f"読み込みエラー: {e}")
        dopa_items = []

    if not dopa_items:
        st.info("まだ取込なし。上の「🔄 DOPAから最新一覧を取込」を押してください")
    else:
        # フィルタ
        f_cols = st.columns([2, 1, 1, 1])
        with f_cols[0]:
            search = st.text_input("🔍 タイトル検索", key="dopa_search")
        with f_cols[1]:
            f_new = st.checkbox("新規限定のみ", key="dopa_f_new")
        with f_cols[2]:
            f_paid = st.checkbox("有料(課金条件付き)のみ", key="dopa_f_paid")
        with f_cols[3]:
            f_last_one = st.checkbox("ラストワン有のみ", key="dopa_f_last")

        filtered = dopa_items
        if search:
            s = search.strip().lower()
            filtered = [g for g in filtered if s in g.title.lower() or s in g.product_id.lower()]
        if f_new:
            filtered = [g for g in filtered if g.is_new_gacha]
        if f_paid:
            filtered = [g for g in filtered if g.is_paid_gacha]
        if f_last_one:
            filtered = [g for g in filtered if g.has_last_one]

        st.markdown(f"**{len(filtered):,}件 / 全{len(dopa_items):,}件**")
        df = pd.DataFrame([{
            "商品ID": g.product_id, "タイトル": g.title,
            "単価(pt)": g.price, "総口数": g.total_tickets, "残口数": g.remaining,
            "ラストワン": "○" if g.has_last_one else "",
            "最低保証pt": g.min_point,
            "期限(日)": g.limit_day or "",
            "制限数量": g.limit_quantity or "",
            "新規限定": "○" if g.is_new_gacha else "",
            "有料限定": "○" if g.is_paid_gacha else "",
            "URL": g.url,
        } for g in filtered])
        st.caption("⬇️ **行をクリックして選択** → 下に転送ボタンが表示されます。**新規限定/有料限定の○は編集可** (📝表示モード切替)")

        # 編集モード切替
        edit_mode = st.toggle("🔧 振り分け編集モード", key="dopa_edit_mode",
                               help="ONにすると新規限定/有料限定をチェックボックスで編集できる")

        if edit_mode:
            edit_df = df.copy()
            edit_df["🆕新規限定_bool"] = edit_df["新規限定"] == "○"
            edit_df["🎰有料限定_bool"] = edit_df["有料限定"] == "○"
            shown = edit_df[["商品ID", "タイトル", "単価(pt)", "総口数", "残口数",
                             "🆕新規限定_bool", "🎰有料限定_bool", "URL"]]
            edited = st.data_editor(
                shown, use_container_width=True, hide_index=True,
                column_config={
                    "🆕新規限定_bool": st.column_config.CheckboxColumn("🆕新規限定"),
                    "🎰有料限定_bool": st.column_config.CheckboxColumn("🎰有料限定"),
                    "URL": st.column_config.LinkColumn("URL"),
                    "商品ID": st.column_config.TextColumn(disabled=True),
                    "タイトル": st.column_config.TextColumn(disabled=True),
                    "単価(pt)": st.column_config.NumberColumn(disabled=True),
                    "総口数": st.column_config.NumberColumn(disabled=True),
                    "残口数": st.column_config.NumberColumn(disabled=True),
                },
                key="dopa_editor",
            )
            if st.button("💾 振り分けを保存", type="primary", key="dopa_save_classification"):
                # filtered とインデックス対応で更新
                from research import DopaProduct, bulk_upsert_dopa_products, NewGacha, PremiumGacha
                from research import bulk_upsert_new_gachas, bulk_upsert_premium_gachas
                today = datetime.now().strftime("%Y-%m-%d")
                updated_products = []
                add_new_gachas = []
                add_paid_gachas = []
                for i, row in edited.iterrows():
                    orig = next((g for g in filtered if g.product_id == row["商品ID"]), None)
                    if not orig:
                        continue
                    is_new = bool(row["🆕新規限定_bool"])
                    is_paid = bool(row["🎰有料限定_bool"])
                    if orig.is_new_gacha == is_new and orig.is_paid_gacha == is_paid:
                        continue
                    orig.is_new_gacha = is_new
                    orig.is_paid_gacha = is_paid
                    updated_products.append(orig)
                    if is_new:
                        add_new_gachas.append(NewGacha(
                            no=orig.product_id, site="DOPA", title=orig.title, url=orig.url,
                            price=orig.price, total_tickets=orig.total_tickets,
                            new_period="手動指定", registered_at=today,
                            note="手動振り分け", updated_at="",
                        ))
                    if is_paid:
                        add_paid_gachas.append(PremiumGacha(
                            product_id=orig.product_id, site="DOPA", title=orig.title, url=orig.url,
                            price=orig.price, total_tickets=orig.total_tickets,
                            card_types=0, charge_amount=0,
                            note="手動振り分け", updated_at="",
                        ))
                if updated_products:
                    bulk_upsert_dopa_products(updated_products)
                    if add_new_gachas:
                        bulk_upsert_new_gachas(add_new_gachas)
                        cached_new_gachas.clear()
                    if add_paid_gachas:
                        bulk_upsert_premium_gachas(add_paid_gachas)
                        cached_premium_gachas.clear()
                    cached_dopa_products.clear()
                    st.success(f"✅ {len(updated_products)}件の振り分けを保存しました")
                    st.rerun()
                else:
                    st.info("変更なし")
        else:
            ev_d = st.dataframe(
                df, use_container_width=True, hide_index=True,
                on_select="rerun", selection_mode="single-row",
                column_config={
                    "URL": st.column_config.LinkColumn("URL"),
                    "単価(pt)": st.column_config.NumberColumn(format="%d pt"),
                    "残口数": st.column_config.NumberColumn(format="%d"),
                },
                key="dopa_table",
            )
            sel_rows_d = (ev_d.selection or {}).get("rows", [])
            if sel_rows_d:
                sel_d = filtered[sel_rows_d[0]]
                ca, cb = st.columns([4, 2])
                with ca:
                    st.success(f"選択中: {sel_d.product_id}｜{sel_d.title}（{sel_d.price}pt×{sel_d.total_tickets:,}・残{sel_d.remaining:,}）")
                with cb:
                    if st.button("📋 景品設計に転送", type="primary", key="dopa_to_template_quick",
                                  use_container_width=True):
                        st.session_state["_jump_to_template_dopa_id"] = sel_d.product_id
                        st.toast(f"📋 景品設計タブで「🎲 DOPA商品から」セレクトしてください ({sel_d.title[:30]})")


# ---------- 有料ガチャ一覧タブ ----------
with tab_paid_list:
    from research import (
        PremiumGacha as _PG,
        upsert_premium_gacha as _upg, delete_premium_gacha as _dpg,
    )
    _lpg = cached_premium_gachas
    from datetime import datetime as _dt2

    st.subheader("🎰 有料ガチャ一覧")
    st.caption("**○○円課金した人だけ引ける限定ガチャ**を管理（通常のpt消費型は対象外）。「📋 景品設計」タブから選択して設計に使えます")
    st.info("💡 **自動取得は「🎲 DOPA商品一覧」タブ**の🔄ボタンで実行（DOPAの課金条件付き商品が自動で振り分けられます）。下のフォームは手動追加用")

    try:
        items = _lpg()
    except Exception as e:
        st.error(f"読み込みエラー: {e}")
        items = []

    # 表示
    if items:
        df = pd.DataFrame([{
            "商品ID": g.product_id, "サイト": g.site, "タイトル": g.title,
            "単価(円)": g.price, "総口数": g.total_tickets, "カード種数": g.card_types,
            "引く権利の事前課金額(円)": g.charge_amount,
            "URL": g.url, "備考": g.note, "更新日時": g.updated_at,
        } for g in items])
        st.dataframe(df, use_container_width=True, hide_index=True,
                     column_config={"URL": st.column_config.LinkColumn("URL")})
    else:
        st.info("まだ登録なし。下のフォームから追加してください。\n\n例: 「5,000円以上課金者限定 SR確定オリパ」のような **課金条件付き** のガチャを登録")

    st.markdown("---")
    st.markdown("##### ➕ 新規追加 / 更新")
    with st.form("paid_add_form", clear_on_submit=False):
        cols = st.columns([2, 1, 3])
        with cols[0]:
            pf_id = st.text_input("商品ID(任意、空なら自動生成)", placeholder="例: DOPA-555")
        with cols[1]:
            pf_site = st.selectbox("サイト", ["DOPA", "DOKKAN", "EXTRECA", "JTC", "その他"])
        with cols[2]:
            pf_title = st.text_input("タイトル *", placeholder="例: 激100連祭")
        cols2 = st.columns([1, 1, 1, 1])
        with cols2[0]:
            pf_price = st.number_input("単価(円) *", min_value=0, step=100)
        with cols2[1]:
            pf_total = st.number_input("総口数 *", min_value=0, step=1)
        with cols2[2]:
            pf_card_types = st.number_input("カード種数", min_value=0, step=1)
        with cols2[3]:
            pf_charge = st.number_input("引く権利の事前課金額(円)", min_value=0, step=1000,
                                        help="○○円課金したユーザーだけ引ける条件の事前課金額。設計時に売上加算")
        pf_url = st.text_input("商品URL", placeholder="https://...")
        pf_note = st.text_area("備考", height=60)

        submitted = st.form_submit_button("💾 保存", type="primary")
        if submitted:
            if not pf_title or pf_price <= 0 or pf_total <= 0:
                st.error("タイトル・単価・総口数は必須です")
            else:
                pid = pf_id.strip() or f"{pf_site}-{_dt2.now().strftime('%Y%m%d%H%M%S')}"
                cached_premium_gachas.clear()
                _upg(_PG(
                    product_id=pid, site=pf_site, title=pf_title, url=pf_url,
                    price=int(pf_price), total_tickets=int(pf_total),
                    card_types=int(pf_card_types), charge_amount=int(pf_charge),
                    note=pf_note, updated_at="",
                ))
                st.success(f"✅ 保存しました: {pid}")
                st.rerun()

    if items:
        st.markdown("##### 🗑️ 削除")
        del_cols = st.columns([3, 1])
        with del_cols[0]:
            del_id = st.selectbox("削除する商品", [g.product_id for g in items], key="paid_del_pick")
        with del_cols[1]:
            if st.button("削除", key="paid_del_btn"):
                cached_premium_gachas.clear()
                _dpg(del_id)
                st.success(f"削除しました: {del_id}")
                st.rerun()


# ---------- 新規ガチャ一覧タブ ----------
with tab_new_list:
    from research import (
        NewGacha as _NG,
        upsert_new_gacha as _ung, delete_new_gacha as _dng,
    )
    _lng = cached_new_gachas

    st.subheader("🆕 新規ガチャ一覧（トレカセンター登録後X日限定など）")
    st.caption("新規限定オリパを管理。「📋 景品設計」タブから選択して設計に使えます")
    st.info("💡 **自動取得は「🎴 トレカセンター商品一覧」「🎲 DOPA商品一覧」の🔄ボタン**で実行（タイトル判定で新規限定が自動振り分け）。下のフォームは手動追加用")

    # トレカセンター自動候補スキャン
    auto_scan = st.checkbox("🔍 トレカセンター8770件からも新規限定っぽい商品を表示", value=True, key="new_scan_tc")

    try:
        items_n = _lng()
    except Exception as e:
        st.error(f"読み込みエラー: {e}")
        items_n = []

    # トレカセンター自動候補抽出
    tc_candidates = []
    if auto_scan:
        try:
            from dopa_scraper import detect_new_gacha_period
            tc_refs = cached_references()
            for r in tc_refs:
                period = detect_new_gacha_period(r.title)
                if period:
                    tc_candidates.append({
                        "No": r.no, "サイト": "トレカセンター", "タイトル": r.title,
                        "単価(coin)": r.price_per_coin, "総口数": r.total_tickets,
                        "新規限定期間": period, "完売日": r.sold_date,
                        "URL": r.url,
                    })
        except Exception:
            pass
        if tc_candidates:
            st.markdown(f"##### 🎴 トレカセンター 自動候補 {len(tc_candidates)}件")
            df_tc = pd.DataFrame(tc_candidates)
            st.dataframe(df_tc, use_container_width=True, hide_index=True,
                         column_config={"URL": st.column_config.LinkColumn("URL")})
            # 一括登録ボタン
            if st.button(f"💾 トレカセンター候補 {len(tc_candidates)}件をDBに登録", key="tc_new_bulk_save"):
                from research import bulk_upsert_new_gachas
                today = datetime.now().strftime("%Y-%m-%d")
                new_gachas = [_NG(
                    no=str(c["No"]), site="トレカセンター", title=c["タイトル"], url=c["URL"],
                    price=int(c["単価(coin)"] or 0), total_tickets=int(c["総口数"] or 0),
                    new_period=c["新規限定期間"], registered_at=today,
                    note="自動候補(トレカセンター タイトル判定)", updated_at="",
                ) for c in tc_candidates]
                bulk_upsert_new_gachas(new_gachas)
                cached_new_gachas.clear()
                st.success(f"✅ {len(new_gachas)}件をDB登録")
                st.rerun()
            st.markdown("---")

    if items_n:
        df = pd.DataFrame([{
            "No": g.no, "サイト": g.site, "タイトル": g.title,
            "単価(円)": g.price, "総口数": g.total_tickets,
            "新規限定期間": g.new_period, "登録日": g.registered_at,
            "URL": g.url, "備考": g.note, "更新日時": g.updated_at,
        } for g in items_n])
        st.dataframe(df, use_container_width=True, hide_index=True,
                     column_config={"URL": st.column_config.LinkColumn("URL")})
    else:
        st.info("まだ登録なし。下のフォームから追加してください")

    st.markdown("---")
    st.markdown("##### ➕ 新規追加 / 更新")
    with st.form("new_add_form", clear_on_submit=False):
        cols = st.columns([1, 1, 3])
        with cols[0]:
            nf_no = st.text_input("商品No. *", placeholder="例: 7401")
        with cols[1]:
            nf_site = st.selectbox("サイト", ["トレカセンター", "DOPA", "その他"], key="nf_site")
        with cols[2]:
            nf_title = st.text_input("タイトル *", placeholder="例: 新規限定オリパ")
        cols2 = st.columns([1, 1, 1, 1])
        with cols2[0]:
            nf_price = st.number_input("単価(円) *", min_value=0, step=100, key="nf_price")
        with cols2[1]:
            nf_total = st.number_input("総口数 *", min_value=0, step=1, key="nf_total")
        with cols2[2]:
            nf_period = st.text_input("新規限定期間", placeholder="例: 登録後7日", key="nf_period")
        with cols2[3]:
            nf_reg = st.date_input("登録日", value=None, key="nf_reg")
        nf_url = st.text_input("商品URL", placeholder="https://japan-toreca.com/oripa/pokemon/...", key="nf_url")
        nf_note = st.text_area("備考", height=60, key="nf_note")

        submitted = st.form_submit_button("💾 保存", type="primary")
        if submitted:
            if not nf_no or not nf_title or nf_price <= 0 or nf_total <= 0:
                st.error("No・タイトル・単価・総口数は必須です")
            else:
                cached_new_gachas.clear()
                _ung(_NG(
                    no=nf_no.strip(), site=nf_site, title=nf_title, url=nf_url,
                    price=int(nf_price), total_tickets=int(nf_total),
                    new_period=nf_period, registered_at=str(nf_reg) if nf_reg else "",
                    note=nf_note, updated_at="",
                ))
                st.success(f"✅ 保存しました: {nf_no}")
                st.rerun()

    if items_n:
        st.markdown("##### 🗑️ 削除")
        del_cols = st.columns([3, 1])
        with del_cols[0]:
            del_key = st.selectbox(
                "削除する商品",
                [f"{g.no}｜{g.site}｜{g.title}" for g in items_n],
                key="new_del_pick",
            )
            # 選択値から No と site を抽出
            sel_idx = [f"{g.no}｜{g.site}｜{g.title}" for g in items_n].index(del_key)
            sel_g = items_n[sel_idx]
        with del_cols[1]:
            if st.button("削除", key="new_del_btn"):
                cached_new_gachas.clear()
                _dng(sel_g.no, sel_g.site)
                st.success(f"削除しました: {sel_g.no}")
                st.rerun()


# ---------- 限定ガチャタブ ----------
with tab_premium:
    from premium_designer import (
        PremiumDesignSpec, PointBucket, design_premium,
        build_premium_result_from_selections, save_premium_reservation,
    )

    st.subheader("🎰 限定ガチャ設計")
    st.caption("DOPA型の限定ガチャ（外れ枠ポイント還元・最低保証・ラストワン賞対応）")

    # カテゴリー
    pg_cat_cols = st.columns([1, 3])
    with pg_cat_cols[0]:
        pg_category = st.radio(
            "カテゴリー",
            options=["ポケモン", "ワンピース"], horizontal=True,
            label_visibility="collapsed", key="pg_category",
            help="設計するカードの種類。候補カードがポケモン/ワンピースで切り替わります",
        )
    with pg_cat_cols[1]:
        st.caption("🃏 " + ("ワンピースのカードから設計します" if pg_category == "ワンピース" else "ポケモンのカードから設計します"))

    pg_col_stock, pg_col_info = st.columns([1, 3])
    with pg_col_stock:
        # 運営は無在庫運用のため 無在庫 を既定に
        pg_stock_label = st.radio(
            "在庫モード",
            options=["無在庫", "在庫連動"], horizontal=True,
            label_visibility="collapsed",
            key="pg_stock_mode",
        )
    pg_stock_mode = "no_stock" if pg_stock_label == "無在庫" else "linked"
    with pg_col_info:
        if pg_stock_mode == "no_stock":
            st.info("🛒 無在庫モード（既定）: 全カード選択可・在庫スプシ非更新")
        else:
            st.warning("📦 在庫連動モード: 残数量から選択・引当する")

    _pg_cat_word = "ワンピ" if pg_category == "ワンピース" else "ポケカ"
    pg_include_snk_index = False
    if pg_stock_mode == "no_stock":
        pg_include_snk_index = st.checkbox(
            f"🃏 スニダン全カード（{_pg_cat_word}・シングル/BOX/パック）も候補に含める",
            value=True, key="pg_include_snk_index",
            help="在庫スプシに無いカードも、スニダン相場付きで景品候補に選べます（無在庫販売前提）",
        )
    elif pg_category == "ワンピース":
        st.warning("⚠️ ワンピースの在庫はありません。在庫モードを『無在庫』にしてください。")

    st.markdown("### ① 販売パラメータ")
    pc1, pc2, pc3, pc4 = st.columns(4)
    with pc1:
        pg_title = st.text_input("商品タイトル", value="新規限定ガチャ", key="pg_title")
    with pc2:
        pg_total = st.number_input("総口数", min_value=1, value=8000, step=100, key="pg_total")
    with pc3:
        pg_price = st.number_input("1回の消費pt", min_value=1, value=2800, step=100, key="pg_price")
    with pc4:
        pg_profit = st.number_input("目標粗利率（%）", min_value=0.0, max_value=100.0, value=30.0, step=1.0, key="pg_profit")

    pg_revenue = pg_total * pg_price
    st.info(f"💰 売上: ¥{pg_revenue:,}（{pg_total:,}口 × {pg_price:,}pt）")

    st.markdown("### ② 上乗せ率設定（商品全体）")

    from markup import load_presets as _load_pg_presets
    pg_presets = _load_pg_presets()
    pg_preset_names = ["（プリセットを選択）"] + [p.name for p in pg_presets]

    def _apply_preset_premium():
        pick = st.session_state.get("pg_preset_pick", "")
        if pick == "（プリセットを選択）" or not pick:
            return
        preset = next((p for p in pg_presets if p.name == pick), None)
        if not preset:
            return
        st.session_state["pg_base_markup_input"] = preset.base_rate
        for i in range(st.session_state.get("pg_card_tier_count", 3)):
            pg_tname_val = st.session_state.get(f"pg_tname_{i}", "")
            if pg_tname_val in preset.tier_rates:
                st.session_state[f"pg_tmarkup_{i}"] = preset.tier_rates[pg_tname_val]
        st.session_state["_applied_pg_preset_msg"] = f"✅ プリセット「{preset.name}」を適用"

    pg_base_cols = st.columns([2, 3, 2])
    with pg_base_cols[0]:
        # key 管理の widget に value= を併用しない（プリセット反映が効かなくなるため）
        if "pg_base_markup_input" not in st.session_state:
            st.session_state["pg_base_markup_input"] = 30.0
        pg_base_markup = st.number_input(
            "商品全体ベース上乗せ率（%）",
            min_value=-1.0, max_value=200.0, step=5.0,
            key="pg_base_markup_input",
            help="`-1`で価格帯別ルール、`50`で全等1.5倍",
        )
    with pg_base_cols[1]:
        pg_preset_pick = st.selectbox(
            "プリセット", options=pg_preset_names, key="pg_preset_pick",
        )
    with pg_base_cols[2]:
        st.button(
            "✅ プリセット適用",
            disabled=(pg_preset_pick == "（プリセットを選択）"),
            key="pg_apply_preset", use_container_width=True,
            on_click=_apply_preset_premium,
        )

    if st.session_state.get("_applied_pg_preset_msg"):
        st.success(st.session_state.pop("_applied_pg_preset_msg"))

    st.markdown("### ③ ポイント還元設定")
    pp1, pp2 = st.columns(2)
    with pp1:
        pg_min_guarantee = st.number_input(
            "最低保証pt（外れ枠の残口数に配る）",
            min_value=0, value=1000, step=100,
            help="ポイント枠の指定後、残った口数にこのpt数を配ります",
            key="pg_min_guarantee",
        )
    with pp2:
        pg_real_cost_rate = st.slider(
            "ポイント実コスト率（%）",
            min_value=0, max_value=100, value=70, step=5,
            help="還元したポイントのうち、実際にコスト化する割合。100%=全部消費される、0%=塩漬けで実コスト0",
            key="pg_real_cost_rate",
        )

    st.markdown("### ④ 当たりカード等構成")
    if "pg_card_tier_count" not in st.session_state:
        st.session_state.pg_card_tier_count = 3

    pg_default_tiers = [
        ("S賞", 5, 500000, 30.0),
        ("A賞", 50, 80000, 25.0),
        ("B賞", 200, 15000, 20.0),
        ("C賞", 500, 5000, 15.0),
    ]

    pg_card_tiers = []
    headers_c = st.columns([1, 1, 2, 1.5, 1])
    headers_c[0].markdown("**等級**")
    headers_c[1].markdown("**当たり数**")
    headers_c[2].markdown("**目標相場（円）**")
    headers_c[3].markdown("**上乗せ率（%）**")
    headers_c[4].markdown("")

    for i in range(st.session_state.pg_card_tier_count):
        default = pg_default_tiers[i] if i < len(pg_default_tiers) else (f"{i+1}等", 10, 1000, 15.0)
        cc = st.columns([1, 1, 2, 1.5, 1])
        tname = cc[0].text_input(f"name_{i}", value=default[0], label_visibility="collapsed", key=f"pg_tname_{i}")
        tcount = cc[1].number_input(f"count_{i}", min_value=0, value=default[1], step=1, label_visibility="collapsed", key=f"pg_tcount_{i}")
        tprice = cc[2].number_input(f"price_{i}", min_value=0, value=default[2], step=1000, label_visibility="collapsed", key=f"pg_tprice_{i}")
        # key 管理の widget に value= を併用しない（プリセット/自動反映が効かなくなるため）
        _pg_mk_key = f"pg_tmarkup_{i}"
        if _pg_mk_key not in st.session_state:
            st.session_state[_pg_mk_key] = float(default[3])
        tmarkup = cc[3].number_input(f"markup_{i}", min_value=-1.0, max_value=200.0, step=1.0, label_visibility="collapsed", key=_pg_mk_key)
        if tname and tcount > 0:
            from designer import TierSpec
            pg_card_tiers.append(TierSpec(name=tname, count=tcount, target_price=tprice, markup_rate_pct=tmarkup))
        cc[4].caption(f"#{i+1}")

    btn_add_col, btn_rm_col, _ = st.columns([1, 1, 4])
    with btn_add_col:
        if st.button("➕ 等を追加", key="pg_add_tier"):
            st.session_state.pg_card_tier_count += 1
            st.rerun()
    with btn_rm_col:
        if st.button("➖ 等を削除", key="pg_rm_tier", disabled=st.session_state.pg_card_tier_count <= 1):
            st.session_state.pg_card_tier_count -= 1
            st.rerun()

    st.markdown("### ⑤ 外れポイント枠（区分指定）")
    st.caption("ここで指定したpt×口数の合計を超える残口数には、最低保証ptが配られます")
    if "pg_bucket_count" not in st.session_state:
        st.session_state.pg_bucket_count = 3

    pg_default_buckets = [(10000, 5), (5000, 20), (3000, 100), (1000, 500)]
    pg_buckets = []
    h_b = st.columns([2, 2, 1])
    h_b[0].markdown("**ポイント値（pt）**")
    h_b[1].markdown("**口数**")
    h_b[2].markdown("")

    for i in range(st.session_state.pg_bucket_count):
        default = pg_default_buckets[i] if i < len(pg_default_buckets) else (1000, 100)
        bc = st.columns([2, 2, 1])
        pv = bc[0].number_input(f"pv_{i}", min_value=0, value=default[0], step=100, label_visibility="collapsed", key=f"pg_pv_{i}")
        pc_ = bc[1].number_input(f"pc_{i}", min_value=0, value=default[1], step=1, label_visibility="collapsed", key=f"pg_pc_{i}")
        if pv > 0 and pc_ > 0:
            pg_buckets.append(PointBucket(point_value=pv, count=pc_))
        bc[2].caption(f"#{i+1}")

    bbcol1, bbcol2, _ = st.columns([1, 1, 4])
    with bbcol1:
        if st.button("➕ 区分追加", key="pg_add_b"):
            st.session_state.pg_bucket_count += 1
            st.rerun()
    with bbcol2:
        if st.button("➖ 区分削除", key="pg_rm_b", disabled=st.session_state.pg_bucket_count <= 1):
            st.session_state.pg_bucket_count -= 1
            st.rerun()

    st.markdown("### ⑥ ラストワン賞（任意）")
    pg_has_last = st.checkbox("ラストワン賞あり（最後の1口を引いた人に確定）", value=True, key="pg_has_last")
    pg_last_tier = None
    pg_last_pt = 0
    if pg_has_last:
        last_type = st.radio(
            "ラストワン賞のタイプ", options=["カード", "ポイント"],
            horizontal=True, key="pg_last_type",
        )
        if last_type == "カード":
            lc1, lc2 = st.columns(2)
            with lc1:
                pg_last_price = st.number_input("ラストワン目標相場（円）", min_value=0, value=1000000, step=10000, key="pg_last_price")
            with lc2:
                pg_last_markup = st.number_input("ラストワン上乗せ率（%）", min_value=-1.0, max_value=200.0, value=30.0, step=1.0, key="pg_last_markup")
            from designer import TierSpec
            pg_last_tier = TierSpec(name="ラストワン賞", count=1, target_price=pg_last_price, markup_rate_pct=pg_last_markup)
        else:
            pg_last_pt = st.number_input("ラストワンpt", min_value=0, value=100000, step=1000, key="pg_last_pt")

    st.markdown("### ⑦ 競合参考（任意）")
    pg_use_ref = st.checkbox("既存リサーチDBから参考競合を選ぶ", value=False, key="pg_use_ref")
    pg_selected_ref = None
    if pg_use_ref:
        refs_all = load_all_references()
        pg_search = st.text_input("検索", placeholder="例: 福袋、JTC、超高還元", key="pg_ref_search")
        filtered = [r for r in refs_all if (pg_search.lower() in r.title.lower() or pg_search in r.no) if pg_search.strip()]
        if filtered:
            pg_idx = st.selectbox(
                "競合", options=range(min(50, len(filtered))),
                format_func=lambda i: f"No.{filtered[i].no} {filtered[i].title} (¥{filtered[i].price_per_coin}×{filtered[i].total_tickets:,}口)",
                key="pg_ref_pick",
            )
            pg_selected_ref = filtered[pg_idx]
            with st.expander(f"参考: {pg_selected_ref.title}"):
                for t, v in pg_selected_ref.tiers.items():
                    st.markdown(f"**{t}**: {v[:200]}{'...' if len(v) > 200 else ''}")

    pg_note = st.text_area("メモ（任意）", height=60, key="pg_note")

    st.markdown("---")
    pg_b1, pg_b2, pg_b3 = st.columns([1, 1, 2])
    with pg_b1:
        pg_preview_btn = st.button("🔍 自動提案", type="primary", key="pg_preview", use_container_width=True)
    with pg_b2:
        pg_reset_btn = st.button("♻ 再割当", key="pg_reset",
                                 disabled=st.session_state.get("pg_session") is None,
                                 use_container_width=True)
    with pg_b3:
        pg_save_btn = st.button("💾 この内容で保存", type="secondary", key="pg_save",
                                disabled=st.session_state.get("pg_session") is None,
                                use_container_width=True)

    def build_pg_spec():
        return PremiumDesignSpec(
            title=pg_title,
            reference_no=pg_selected_ref.no if pg_selected_ref else "",
            reference_title=pg_selected_ref.title if pg_selected_ref else "",
            total_tickets=pg_total, price_per_spin=pg_price,
            target_profit_rate=pg_profit / 100,
            stock_mode=pg_stock_mode,
            card_tiers=pg_card_tiers,
            point_buckets=pg_buckets,
            minimum_guarantee_pt=pg_min_guarantee,
            point_real_cost_rate=pg_real_cost_rate / 100,
            has_last_one=pg_has_last,
            last_one_tier=pg_last_tier,
            last_one_point=pg_last_pt,
            note=pg_note,
            base_markup_rate=pg_base_markup,
        )

    if pg_preview_btn or pg_reset_btn:
        spec_pg = build_pg_spec()
        with st.spinner("マッチング中..."):
            pg_inventory = None
            if pg_stock_mode == "no_stock" and pg_include_snk_index:
                pg_inventory = _safe_load(load_all_inventory) + cached_snkrdunk_index()
            # カテゴリーで候補を絞る（ワンピ=ワンピタブのみ / ポケモン=ワンピ以外）
            if pg_inventory is not None:
                if pg_category == "ワンピース":
                    pg_inventory = [it for it in pg_inventory if it.tab.startswith("ワンピ")]
                else:
                    pg_inventory = [it for it in pg_inventory if not it.tab.startswith("ワンピ")]
            result_pg = design_premium(spec_pg, inventory=pg_inventory, reference=pg_selected_ref)
        tier_selections_pg = {
            tr.name: [(it.tab, it.row_idx) for it in tr.selected]
            for tr in result_pg.card_tier_results
        }
        last_one_sel = None
        if result_pg.last_one_tier_result and result_pg.last_one_tier_result.selected:
            it = result_pg.last_one_tier_result.selected[0]
            last_one_sel = (it.tab, it.row_idx)
        st.session_state.pg_session = {
            "spec": spec_pg,
            "tier_selections": tier_selections_pg,
            "last_one_selection": last_one_sel,
            "inventory": result_pg.all_inventory,
            "ref": pg_selected_ref,
        }

    pg_session = st.session_state.get("pg_session")
    if pg_session:
        live_pg_spec = build_pg_spec()
        for t in live_pg_spec.card_tiers:
            pg_session["tier_selections"].setdefault(t.name, [])
        valid_names_pg = {t.name for t in live_pg_spec.card_tiers}
        pg_session["tier_selections"] = {k: v for k, v in pg_session["tier_selections"].items() if k in valid_names_pg}
        pg_session["spec"] = live_pg_spec

        result_pg = build_premium_result_from_selections(
            live_pg_spec, pg_session["tier_selections"],
            pg_session["inventory"],
            last_one_selection=pg_session.get("last_one_selection"),
            reference=pg_session.get("ref"),
        )

        st.markdown("## 結果")
        rc1, rc2, rc3 = st.columns(3)
        rc1.metric("売上", f"¥{result_pg.total_revenue:,}")
        rc2.metric("実コスト", f"¥{result_pg.total_card_cost + result_pg.total_point_real_cost:,}")
        rc3.metric("粗利", f"¥{result_pg.gross_profit:,}", delta=f"{result_pg.actual_profit_rate:.1%}")

        rc4, rc5, rc6 = st.columns(3)
        rc4.metric("顧客還元率", f"{result_pg.customer_return_rate:.1%}",
                   help="顧客が見る還元率（pt額面ベース、ポイント還元含む）")
        rc5.metric("実還元率", f"{result_pg.real_return_rate:.1%}",
                   help="運営の本当の還元率（カード仕入れ + ポイント実コスト）")
        rc6.metric("pt上乗せ差分",
                   f"{(result_pg.customer_return_rate - result_pg.real_return_rate)*100:+.1f}pt")

        # --- 最悪ケース試算（当選者が全員ポイント還元＋外れptも全額面で払戻す想定）---
        st.markdown("###### 🛡️ 最悪ケース試算（全員ポイント還元・外れptも額面満額で払戻す想定）")
        pg_worst_cost = result_pg.total_coin_value  # pt額面(カード) + ポイント額面(満額)
        pg_worst_profit = result_pg.total_revenue - pg_worst_cost
        pg_worst_rate = (pg_worst_profit / result_pg.total_revenue) if result_pg.total_revenue else 0
        pw1, pw2, pw3 = st.columns(3)
        pw1.metric(
            "弊社コスト（最悪ケース）", f"¥{pg_worst_cost:,}",
            help="カードのpt額面 + 外れポイント額面（実コスト率を掛けない満額）。全員がpt還元・満額払戻しした場合の上限コスト",
        )
        pw2.metric(
            "最悪ケース粗利", f"¥{pg_worst_profit:,}", delta=f"{pg_worst_rate:.1%}",
            delta_color="normal" if pg_worst_profit >= 0 else "inverse",
            help="売上 − pt額面合計。下振れの底の利益",
        )
        pw3.metric(
            "最悪ケース還元率", f"{result_pg.customer_return_rate:.1%}",
            help="=顧客還元率。100%を超えると最悪時は赤字",
        )
        if pg_worst_profit < 0:
            st.error(f"⚠️ 最悪ケースで赤字（¥{pg_worst_profit:,}）。ポイント実コスト率を過信せず、上乗せ率/最低保証ptを見直してください。")

        with st.expander("📊 詳細内訳"):
            st.markdown(f"""
| 項目 | 金額 |
|---|---|
| 売上 | ¥{result_pg.total_revenue:,} |
| カード相場合計 | ¥{result_pg.total_card_market:,} |
| カード仕入れ合計 | ¥{result_pg.total_card_cost:,} |
| ポイント還元（額面） | ¥{result_pg.total_point_value:,} |
| ポイント還元（実コスト × {live_pg_spec.point_real_cost_rate:.0%}） | ¥{result_pg.total_point_real_cost:,} |
| pt額面合計（顧客視点） | ¥{result_pg.total_coin_value:,} |
| **実コスト合計** | ¥{result_pg.total_card_cost + result_pg.total_point_real_cost:,} |
| **粗利** | ¥{result_pg.gross_profit:,} |
""")
            st.markdown(f"**口数内訳**: 当たり {result_pg.all_card_count} 口 + 外れ {result_pg.all_point_count} 口"
                        + (" + ラストワン 1 口" if (live_pg_spec.has_last_one and (result_pg.last_one_tier_result or live_pg_spec.last_one_point > 0)) else "")
                        + f" = {result_pg.all_card_count + result_pg.all_point_count + (1 if (live_pg_spec.has_last_one and (result_pg.last_one_tier_result or live_pg_spec.last_one_point > 0)) else 0)} 口（総口数 {live_pg_spec.total_tickets} 口）")
            st.markdown(f"**外れ構成**: {result_pg.point_result.buckets_summary}")

        # 警告
        if result_pg.warnings:
            with st.expander(f"⚠ 警告 ({len(result_pg.warnings)}件)", expanded=any(w[0]=='critical' for w in result_pg.warnings)):
                for sev, title, detail in result_pg.warnings:
                    if sev == "critical":
                        st.error(f"🔴 **{title}**\n\n{detail}")
                    elif sev == "warning":
                        st.warning(f"🟡 **{title}**\n\n{detail}")
                    else:
                        st.info(f"🔵 **{title}**\n\n{detail}")

        # 各カード等の選定表示（既存タブと同じスタイル、簡略版）
        st.markdown("### 当たりカード等の構成")
        all_inv_pg = pg_session["inventory"]
        inv_by_key_pg = {(it.tab, it.row_idx): it for it in all_inv_pg}
        used_in_pg = set()
        for keys in pg_session["tier_selections"].values():
            for k in keys:
                used_in_pg.add(k)
        if pg_session.get("last_one_selection"):
            used_in_pg.add(pg_session["last_one_selection"])

        for tspec in live_pg_spec.card_tiers:
            tname = tspec.name
            keys = pg_session["tier_selections"].get(tname, [])
            current_items = [inv_by_key_pg.get(k) for k in keys if k in inv_by_key_pg]
            avg = sum(it.price for it in current_items) // len(current_items) if current_items else 0
            with st.expander(f"{tname}｜目標¥{tspec.target_price:,} × {tspec.count}枚｜選定{len(current_items)}枚 平均¥{avg:,}", expanded=False):
                if current_items:
                    for i, it in enumerate(current_items):
                        cols = st.columns([5, 2, 1])
                        cols[0].markdown(f"{it.name} `{it.series or ''}`")
                        cols[1].markdown(f"¥{it.price:,}")
                        if cols[2].button("❌", key=f"pg_rm_{tname}_{i}"):
                            pg_session["tier_selections"][tname].pop(i)
                            st.rerun()
                # 候補追加
                target = tspec.target_price
                if live_pg_spec.stock_mode == "no_stock":
                    cand = [it for it in all_inv_pg if (it.tab, it.row_idx) not in used_in_pg]
                else:
                    cand = [it for it in all_inv_pg if it.available_qty > 0 and (it.tab, it.row_idx) not in used_in_pg]
                pg_nameq = st.text_input(
                    f"🔍 カード名で絞り込み_{tname}", key=f"pg_nameq_{tname}",
                    placeholder="例: リザードン / ルフィ / BOX", label_visibility="collapsed",
                ).strip()
                if pg_nameq:
                    _pnq = pg_nameq.lower()
                    cand = [it for it in cand if _pnq in (it.name or "").lower() or _pnq in (it.series or "").lower()]
                if target > 0:
                    cand.sort(key=lambda x: abs(x.price - target))
                show_n = st.number_input(f"表示件数_{tname}", min_value=5, max_value=50, value=10, key=f"pg_showcnt_{tname}", label_visibility="collapsed")
                for j, it in enumerate(cand[:show_n]):
                    cols = st.columns([4, 2, 2, 1])
                    cols[0].markdown(f"{it.name} `{it.series or ''}`")
                    cols[1].markdown(f"¥{it.price:,}")
                    dev = (it.price / target - 1) if target else 0
                    cols[2].markdown(f"{dev:+.0%}" if target else "-")
                    if cols[3].button("➕", key=f"pg_add_{tname}_{j}_{it.row_idx}"):
                        pg_session["tier_selections"][tname].append((it.tab, it.row_idx))
                        st.rerun()

        # ラストワン賞のカード選定
        if live_pg_spec.has_last_one and live_pg_spec.last_one_tier:
            t = live_pg_spec.last_one_tier
            cur = pg_session.get("last_one_selection")
            cur_item = inv_by_key_pg.get(cur) if cur else None
            with st.expander(f"ラストワン賞｜目標¥{t.target_price:,} × 1枚｜{'選定済' if cur_item else '未選定'}", expanded=False):
                if cur_item:
                    cols = st.columns([5, 2, 1])
                    cols[0].markdown(f"{cur_item.name} `{cur_item.series or ''}`")
                    cols[1].markdown(f"¥{cur_item.price:,}")
                    if cols[2].button("❌", key="pg_rm_lastone"):
                        pg_session["last_one_selection"] = None
                        st.rerun()
                # 候補
                target = t.target_price
                if live_pg_spec.stock_mode == "no_stock":
                    cand = [it for it in all_inv_pg if (it.tab, it.row_idx) not in used_in_pg]
                else:
                    cand = [it for it in all_inv_pg if it.available_qty > 0 and (it.tab, it.row_idx) not in used_in_pg]
                pg_lo_nameq = st.text_input(
                    "🔍 カード名で絞り込み_lastone", key="pg_nameq_lastone",
                    placeholder="例: リザードン / ルフィ / BOX", label_visibility="collapsed",
                ).strip()
                if pg_lo_nameq:
                    _lnq = pg_lo_nameq.lower()
                    cand = [it for it in cand if _lnq in (it.name or "").lower() or _lnq in (it.series or "").lower()]
                if target > 0:
                    cand.sort(key=lambda x: abs(x.price - target))
                for j, it in enumerate(cand[:10]):
                    cols = st.columns([4, 2, 2, 1])
                    cols[0].markdown(f"{it.name} `{it.series or ''}`")
                    cols[1].markdown(f"¥{it.price:,}")
                    cols[2].markdown(f"{(it.price/target-1):+.0%}" if target else "-")
                    if cols[3].button("➕", key=f"pg_addlast_{j}_{it.row_idx}"):
                        pg_session["last_one_selection"] = (it.tab, it.row_idx)
                        st.rerun()

        if pg_save_btn:
            with st.spinner("保存中..."):
                pid = save_premium_reservation(result_pg)
            st.success(f"✅ 限定ガチャを予約中として保存しました: **{pid}**")
            st.session_state.pg_session = None
            st.balloons()


# ---------- 商品一覧タブ ----------
with tab_products:
    st.subheader("登録済み商品")
    if st.button("🔄 再読込", key="reload_products"):
        st.cache_data.clear()
        st.rerun()

    @st.cache_data(ttl=30)
    def load_products():
        ws = open_inventory().worksheet(config.TAB_DESIGN_SUMMARY)
        values = ws.get_all_values()
        if len(values) < 2:
            return pd.DataFrame(columns=config.DESIGN_SUMMARY_HEADERS)
        return pd.DataFrame(values[1:], columns=values[0])

    dfp = load_products()
    if len(dfp) == 0:
        st.info("まだ商品がありません")
    else:
        # フィルタ
        statuses = sorted(dfp["ステータス"].unique().tolist())
        filter_status = st.multiselect(
            "ステータスでフィルタ", options=statuses,
            default=[s for s in statuses if s in (config.STATUS_RESERVED, config.STATUS_ON_SALE)],
        )
        if filter_status:
            dfp = dfp[dfp["ステータス"].isin(filter_status)]

        st.dataframe(dfp, use_container_width=True, hide_index=True)

        st.markdown("---")
        st.subheader("操作")
        c1, c2 = st.columns([1, 2])
        with c1:
            pids = dfp["商品ID"].tolist()
            target_pid = st.selectbox("商品ID", options=pids) if pids else None
        if target_pid:
            row = dfp[dfp["商品ID"] == target_pid].iloc[0]
            current_status = row["ステータス"]
            c2.markdown(f"**{row['タイトル']}**｜現在: **{current_status}**")

            action_cols = st.columns(3)
            with action_cols[0]:
                if st.button("✅ 承認（予約→販売中）", disabled=current_status != config.STATUS_RESERVED, use_container_width=True):
                    approve(target_pid)
                    st.success("承認しました")
                    st.cache_data.clear()
                    st.rerun()
            with action_cols[1]:
                if st.button("❌ 解除（→ボツ、在庫復活）", disabled=current_status != config.STATUS_RESERVED, use_container_width=True):
                    cancel(target_pid)
                    st.success("解除しました")
                    st.cache_data.clear()
                    st.rerun()
            with action_cols[2]:
                if st.button("🏁 完売（在庫数量を減算）", disabled=current_status != config.STATUS_ON_SALE, use_container_width=True):
                    close_sold_out(target_pid)
                    st.success("完売処理しました")
                    st.cache_data.clear()
                    st.rerun()


# ---------- 改善提案タブ ----------
with tab_suggest:
    st.subheader("🔄 在庫変動ベースの改善提案")
    st.caption("新入荷や在庫変動により、既存商品の選定カードより目標に近いカードが見つかった場合に差し替えを提案します")

    c1, c2, c3 = st.columns([1, 1, 3])
    with c1:
        scan_btn = st.button("🔍 提案を取得", type="primary", use_container_width=True)
    with c2:
        include_on_sale = st.checkbox("販売中の商品も対象", value=False, help="販売中は基本ロックすべきなので注意")
    with c3:
        min_improvement = st.slider(
            "最小改善幅（乖離ポイント）", min_value=0.01, max_value=0.50,
            value=0.05, step=0.01, format="%.2f",
            help="この値以上の改善がある場合のみ提案",
        )

    if scan_btn:
        from suggestions import find_upgrade_suggestions
        with st.spinner("在庫をスキャン中..."):
            sugs = find_upgrade_suggestions(
                min_improvement=min_improvement,
                only_reserved=not include_on_sale,
            )
        st.session_state.suggestions = sugs

    sugs = st.session_state.get("suggestions", None)
    if sugs is None:
        st.info("「🔍 提案を取得」ボタンを押してください")
    elif not sugs:
        st.success("✨ 既存の選定は全て最適です。改善提案はありません。")
    else:
        st.markdown(f"**{len(sugs)}件の改善提案**（改善幅が大きい順）")
        for i, s in enumerate(sugs):
            with st.container(border=True):
                cols = st.columns([3, 1])
                with cols[0]:
                    st.markdown(
                        f"**{s.product_title}**（{s.product_id}｜{s.product_status}） - "
                        f"{s.tier}"
                    )
                    st.markdown(
                        f"目標: **¥{s.target_price:,}**\n\n"
                        f"🔸 現在: {s.old_name} ¥{s.old_price:,}（乖離 {s.old_deviation:+.0%}）\n\n"
                        f"🔹 提案: **{s.new_item.name}** ¥{s.new_item.price:,}（乖離 {s.new_deviation:+.0%}）"
                        f" [{s.new_item.tab}] {s.new_item.series or ''}\n\n"
                        f"✨ 改善幅: **{s.improvement*100:.1f}pt**"
                    )
                with cols[1]:
                    if st.button("✅ 差し替える", key=f"swap_{i}_{s.product_id}_{s.tier}", use_container_width=True):
                        from suggestions import apply_swap
                        try:
                            with st.spinner("差し替え中..."):
                                apply_swap(s)
                            st.success("差し替え完了")
                            st.session_state.suggestions = None
                            st.cache_data.clear()
                            st.rerun()
                        except Exception as e:
                            st.error(f"失敗: {e}")


# ---------- 商品一覧タブ（スニダン相場） ----------
with tab_inventory:
    import pandas as _pd_inv
    st.subheader("🃏 商品一覧（スニダン取得カード・BOX・パックと相場）")
    st.caption("ガチャに入れられるカード/BOX/パックの一覧と相場（スニダン・毎朝自動更新）。『📝 新規設計』の検索と同じデータです。")
    idx = cached_snkrdunk_index()

    def _inv_kind(tab):
        if tab.endswith("BOX"):
            return "BOX"
        if tab.endswith("パック"):
            return "パック"
        if tab.endswith("デッキ"):
            return "デッキ"
        return "シングル"

    def _inv_game(tab):
        return "ワンピ" if tab.startswith("ワンピ") else "ポケカ"

    fc1, fc2, fc3, fc4 = st.columns([3, 2, 2, 2])
    inv_q = fc1.text_input("カード名・型番で検索", key="inv_q", placeholder="例: リザードン / VSTARユニバース / OP01")
    inv_game = fc2.multiselect("カテゴリー", ["ポケカ", "ワンピ"], default=["ポケカ", "ワンピ"], key="inv_game")
    inv_kind = fc3.multiselect("種別", ["シングル", "BOX", "パック", "デッキ"],
                               default=["シングル", "BOX", "パック", "デッキ"], key="inv_kind")
    inv_sort = fc4.selectbox("並び", ["相場が高い順", "相場が安い順", "名前順"], key="inv_sort")

    filtered = [it for it in idx if _inv_game(it.tab) in inv_game and _inv_kind(it.tab) in inv_kind]
    if inv_q:
        _q = inv_q.lower()
        filtered = [it for it in filtered
                    if _q in (it.name or "").lower() or _q in (it.card_no or "").lower()
                    or _q in (it.series or "").lower()]
    if inv_sort == "相場が高い順":
        filtered.sort(key=lambda x: -x.price)
    elif inv_sort == "相場が安い順":
        filtered.sort(key=lambda x: x.price)
    else:
        filtered.sort(key=lambda x: x.name)

    CAP = 800
    st.caption(f"該当 {len(filtered):,} 件（表示は先頭 {min(CAP, len(filtered)):,} 件・検索で絞り込めます）｜相場は2系統表示")
    df_inv = _pd_inv.DataFrame([{
        "カテゴリー": _inv_game(it.tab), "種別": _inv_kind(it.tab), "カード名": it.name,
        "レア": it.grade, "型番": it.card_no,
        "直近取引": (getattr(it, "price_recent", 0) or it.price),
        "相場": (getattr(it, "price_min", 0) or it.price),
        "スニダン": it.snkrdunk_url,
    } for it in filtered[:CAP]])
    st.dataframe(df_inv, use_container_width=True, hide_index=True, column_config={
        "直近取引": st.column_config.NumberColumn("直近取引", format="¥%d"),
        "相場": st.column_config.NumberColumn("相場", format="¥%d"),
        "スニダン": st.column_config.LinkColumn("スニダン", display_text="開く"),
    })
    st.caption("💡 毎朝JST6:00に自動更新。**直近取引=PSA10の直近販売価格／相場=スニダンの相場**。設計は『📝 新規設計』でこのカードから積めます。")
