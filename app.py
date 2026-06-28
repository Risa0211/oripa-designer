"""Streamlit UI — みんなのトレカ オリパ商品設計ツール"""
from __future__ import annotations

from datetime import datetime
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

# ---------- テストモード切替 ----------
if "test_mode" not in st.session_state:
    st.session_state.test_mode = False

mode_col1, mode_col2 = st.columns([3, 1])
with mode_col2:
    new_mode = st.toggle(
        "🧪 テストモード",
        value=st.session_state.test_mode,
        help="ONにするとテスト用スプシ（ポケモン在庫管理（テスト））に読み書きします。本番在庫は変更されません",
    )
    if new_mode != st.session_state.test_mode:
        st.session_state.test_mode = new_mode
        st.cache_data.clear()
        # デザインセッションも初期化（在庫プールが変わるため）
        st.session_state.pop("design_session", None)
        st.session_state.pop("suggestions", None)
        st.rerun()

if st.session_state.test_mode:
    with mode_col1:
        st.warning("🧪 **テストモード中** - テスト用スプシに読み書きします。本番在庫には影響しません。")
else:
    with mode_col1:
        st.info("🔴 **本番モード中** - 操作は本番の在庫スプシに反映されます")


# ---------- サイドバー: 作業者名 + 参考競合 ----------
with st.sidebar:
    st.header("👤 作業者")
    worker_name = st.text_input(
        "あなたの名前", value=st.session_state.get('_worker_name', ''),
        key='_worker_name_input',
        placeholder="例: 山田、ワーカーA",
        help="カード照合や商品設計時の登録に作業者名が記録されます",
    )
    if worker_name.strip():
        st.session_state['_worker_name'] = worker_name.strip()
    if not st.session_state.get('_worker_name'):
        st.warning("⚠️ 名前を入力してください")

    st.markdown("---")
    st.header("① 参考競合を選ぶ")
    refs = cached_references()
    dopa_for_sidebar = []
    try:
        dopa_for_sidebar = cached_dopa_products()
    except Exception:
        pass

    # 簡易検索 (トレカセンター + DOPA)
    search = st.text_input("検索（タイトル・No.・DOPA-ID）", placeholder="例: おつきみ、1758、DOPA-285731")
    filtered = refs
    filtered_dopa = []
    if search:
        s = search.strip().lower()
        filtered = [r for r in refs if s in r.title.lower() or s in str(r.no)]
        filtered_dopa = [d for d in dopa_for_sidebar
                         if s in d.title.lower() or s in d.product_id.lower()
                         or s in str(d.product_id).replace("DOPA-", "")]

    # 表示候補: 検索ありなら結果を統合、なしならトレカセンターのみ
    options = []
    option_items = []  # 元オブジェクト
    for r in filtered[:500]:
        options.append(f"[トレカセンター] No.{r.no}｜{r.title[:50]}（¥{r.price_per_coin}×{r.total_tickets:,}口）")
        option_items.append(("torecacenter", r))
    for d in filtered_dopa[:200]:
        options.append(f"[DOPA] {d.product_id}｜{d.title[:50]}（{d.price}pt×{d.total_tickets:,}口）")
        option_items.append(("dopa", d))

    if not options:
        st.warning("該当なし")
        st.stop()

    idx = st.selectbox("競合", range(len(options)), format_func=lambda i: options[i])
    sel_type, selected_ref = option_items[idx]

    st.markdown("---")
    if sel_type == "torecacenter":
        st.markdown(f"**🎴 {selected_ref.title}**")
        st.markdown(f"- 1回: ¥{selected_ref.price_per_coin:,}")
        st.markdown(f"- 総口数: {selected_ref.total_tickets:,}")
        if selected_ref.sold_date:
            st.markdown(f"- 完売: {selected_ref.sold_date}")
        if selected_ref.url:
            st.markdown(f"- [商品URL]({selected_ref.url})")
        st.markdown("**等構成（参考）**")
        for t, text in selected_ref.tiers.items():
            cnt = count_cards_in_tier(text)
            with st.expander(f"{t}（約{cnt}枚）"):
                st.write(text)
    else:  # dopa
        st.markdown(f"**🎲 {selected_ref.title}**")
        st.markdown(f"- 1回: {selected_ref.price}pt")
        st.markdown(f"- 総口数: {selected_ref.total_tickets:,}")
        st.markdown(f"- 残: {selected_ref.remaining:,}")
        if selected_ref.has_last_one:
            st.markdown(f"- ⭐ ラストワン賞あり")
        if selected_ref.is_new_gacha:
            st.markdown(f"- 🆕 新規限定ガチャ")
        if selected_ref.url:
            st.markdown(f"- [商品ページ]({selected_ref.url})")
        # Reference互換オブジェクトに変換（既存設計フローでも参照可能に）
        from dataclasses import make_dataclass
        # 既存DesignSpec等が選択refを使うため、Reference互換でラップ
        class _DopaRefShim:
            def __init__(self, d):
                self.no = d.product_id
                self.title = d.title
                self.url = d.url
                self.price_per_coin = d.price
                self.total_tickets = d.total_tickets
                self.sold_date = ""
                self.tags = "DOPA"
                self.tiers = {}
        selected_ref = _DopaRefShim(selected_ref)


# ---------- メインタブ ----------
(tab_design, tab_premium, tab_template, tab_rewrite, tab_match,
 tab_torecacenter, tab_dopa_list, tab_paid_list, tab_new_list,
 tab_products, tab_suggest, tab_inventory, tab_markup) = st.tabs([
    "📝 新規設計", "🎰 限定ガチャ",
    "📋 景品設計（競合コピー）",
    "✨ リライト商品案",
    "🖼 カード照合",
    "🎴 トレカセンター商品一覧", "🎲 DOPA商品一覧",
    "🎰 有料ガチャ一覧", "🆕 新規ガチャ一覧",
    "📋 商品一覧", "🔄 改善提案", "📦 在庫", "⚙️ 上乗せ率設定"
])


with tab_design:
    st.subheader("② 在庫モード")
    mode_cols = st.columns([1, 3])
    with mode_cols[0]:
        stock_mode_label = st.radio(
            "モード",
            options=["在庫連動", "無在庫"],
            horizontal=True,
            label_visibility="collapsed",
            help="無在庫: 在庫表の数量を無視して全カードから選択可能。保存しても在庫の予約中数量は増えません",
            key="stock_mode_radio",
        )
    stock_mode = "no_stock" if stock_mode_label == "無在庫" else "linked"
    with mode_cols[1]:
        if stock_mode == "no_stock":
            st.warning("🛒 **無在庫モード**: 全カードから自由に選択可能・在庫スプシの予約中/残数量には影響しません。販売決定後に仕入れる前提。")
        else:
            st.info("📦 **在庫連動モード**: 残数量がある在庫から選択。保存で「予約中」になります。")

    st.subheader("③ 販売パラメータ")
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        total_tickets = st.number_input("総口数", min_value=1, value=selected_ref.total_tickets or 1000, step=100)
    with c2:
        price_per_spin = st.number_input("1回価格（円）", min_value=1, value=selected_ref.price_per_coin or 500, step=100)
    with c3:
        profit_rate = st.number_input("目標粗利率（%）", min_value=0.0, max_value=100.0, value=30.0, step=1.0)
    with c4:
        return_rate = 100.0 - profit_rate
        st.metric("還元率（=100-粗利率）", f"{return_rate:.1f}%")

    total_revenue = total_tickets * price_per_spin
    st.info(f"💰 総売上: ¥{total_revenue:,} ／ 原価予算: ¥{int(total_revenue * return_rate / 100):,}")

    st.subheader("④ モードを選ぶ")
    mode = st.radio(
        "入力方式",
        options=["X", "Y"],
        format_func=lambda x: "案X: 1枚あたり目標相場を直接指定" if x == "X" else "案Y: 還元率＋等級配分比率から自動計算",
        horizontal=True,
    )

    st.subheader("⑤ 等構成")
    # 参考競合の等を初期値にする
    default_tiers = list(selected_ref.tiers.keys()) or ["1等", "2等", "3等"]
    selected_tier_names = st.multiselect("含める等", options=TIER_COLS, default=default_tiers)

    tier_specs: list[TierSpec] = []
    if selected_tier_names:
        # --- 商品全体ベース上乗せ率＋プリセット ---
        st.markdown("**🎯 商品全体の上乗せ率設定**")

        # プリセットを先に読込（コールバックで参照するため）
        from markup import load_presets, save_preset, MarkupPreset
        presets = load_presets()
        preset_names = ["（プリセットを選択）"] + [p.name for p in presets]

        def _apply_preset_main():
            pick = st.session_state.get("preset_pick", "")
            if pick == "（プリセットを選択）" or not pick:
                return
            preset = next((p for p in presets if p.name == pick), None)
            if not preset:
                return
            # コールバックはwidget render前に実行されるのでsession_stateを安全に変更可
            st.session_state["base_markup_rate_input"] = preset.base_rate
            for tname, rate in preset.tier_rates.items():
                st.session_state[f"markup_{tname}"] = rate
            st.session_state["_applied_preset_msg"] = f"✅ プリセット「{preset.name}」を適用"

        base_cols = st.columns([2, 3, 2])
        with base_cols[0]:
            base_markup_rate = st.number_input(
                "商品全体ベース上乗せ率（%）",
                min_value=-1.0, max_value=200.0, step=5.0,
                value=float(st.session_state.get("base_markup_rate_input", 50.0)),
                key="base_markup_rate_input",
                help="`-1`で価格帯別ルール、`50`で全等1.5倍、`0`で上乗せなし。等別の値が指定されればそちらが優先",
            )
        with base_cols[1]:
            preset_pick = st.selectbox(
                "プリセット",
                options=preset_names,
                key="preset_pick",
                help="保存済みのプリセットから上乗せ率を一括ロード",
            )
        with base_cols[2]:
            st.button(
                "✅ プリセット適用",
                disabled=(preset_pick == "（プリセットを選択）"),
                use_container_width=True,
                key="apply_preset_btn",
                on_click=_apply_preset_main,
            )

        if st.session_state.get("_applied_preset_msg"):
            st.success(st.session_state.pop("_applied_preset_msg"))

        st.markdown("---")
        st.markdown("**各等の設定**")

        # 等別上乗せ率の自動提案ボタン
        suggest_col1, suggest_col2 = st.columns([1, 5])
        with suggest_col1:
            suggest_markup_btn = st.button(
                "💡 上乗せ率を自動提案",
                help="各等の目標相場に応じて、価格帯別ルールから上乗せ率を自動入力。手動でも上書き可能",
                key="suggest_markup",
            )

        cols_header = st.columns([1, 1, 2, 1.5, 2])
        cols_header[0].markdown("**等級**")
        cols_header[1].markdown("**当たり数**")
        if mode == "X":
            cols_header[2].markdown("**1枚あたり目標相場（円）**")
        else:
            cols_header[2].markdown("**原価配分比率（%）**")
        cols_header[3].markdown("**上乗せ率（%）**")
        cols_header[4].markdown("**参考**")

        default_prices = {"1等": 200000, "2等": 50000, "3等": 15000, "4等": 5000, "5等": 2000, "6等": 1000, "7等": 500, "キリ番": 10000, "ラストワン": 100000}
        default_ratios = {"1等": 25, "2等": 25, "3等": 20, "4等": 15, "5等": 8, "6等": 4, "7等": 2, "キリ番": 1, "ラストワン": 0}

        # 自動提案ボタン押下時: 各等の目標相場から推奨率を計算してセッションに保存
        if suggest_markup_btn:
            from markup import load_markup_bands, suggest_tier_rate
            bands = load_markup_bands(force=True)
            for tname in selected_tier_names:
                target = st.session_state.get(f"price_{tname}", default_prices.get(tname, 10000))
                rate = suggest_tier_rate(int(target or 0), bands)
                st.session_state[f"markup_{tname}"] = float(rate)
            st.rerun()

        for tname in selected_tier_names:
            ref_text = selected_ref.tiers.get(tname, "")
            default_count = count_cards_in_tier(ref_text) or 1
            row = st.columns([1, 1, 2, 1.5, 2])
            row[0].markdown(f"**{tname}**")
            cnt = row[1].number_input(
                f"count_{tname}", min_value=0, value=default_count, step=1,
                label_visibility="collapsed", key=f"count_{tname}",
            )
            if mode == "X":
                price_each = row[2].number_input(
                    f"price_{tname}", min_value=0, value=default_prices.get(tname, 10000), step=1000,
                    label_visibility="collapsed", key=f"price_{tname}",
                )
                tier_target = price_each
            else:
                ratio = row[2].number_input(
                    f"ratio_{tname}", min_value=0.0, max_value=100.0,
                    value=float(default_ratios.get(tname, 10)), step=1.0,
                    label_visibility="collapsed", key=f"ratio_{tname}",
                )
                tier_target = 0
            # 上乗せ率（等別、空欄なら-1=価格帯ルール）
            markup_key = f"markup_{tname}"
            markup_default = st.session_state.get(markup_key, -1.0)
            markup_rate = row[3].number_input(
                f"markup_input_{tname}",
                min_value=-1.0, max_value=200.0, step=1.0,
                value=float(markup_default),
                label_visibility="collapsed",
                key=markup_key,
                help="-1のままなら価格帯別ルールを使用。0以上を入れると等別の値を使う",
            )
            if mode == "X":
                tier_specs.append(TierSpec(name=tname, count=cnt, target_price=tier_target, markup_rate_pct=markup_rate))
            else:
                tier_specs.append(TierSpec(name=tname, count=cnt, budget_ratio=ratio, markup_rate_pct=markup_rate))
            row[4].caption(ref_text[:60] + ("…" if len(ref_text) > 60 else ""))

        st.caption("💡 上乗せ率: `-1` = 価格帯別ルール（⚙️タブ）を自動適用 / `0以上` = 等別に指定した値を適用")

    # モードY の場合: 合計比率チェック
    if mode == "Y" and tier_specs:
        total_ratio = sum(t.budget_ratio for t in tier_specs)
        if abs(total_ratio - 100) > 0.01:
            st.warning(f"⚠ 配分比率合計: {total_ratio:.1f}%（100%になるよう調整推奨）")

    st.subheader("⑥ 商品情報")
    title = st.text_input("商品タイトル", value=f"{selected_ref.title}参考オリパ")
    note = st.text_area("メモ（任意）", height=80)

    st.markdown("---")

    # セッション状態
    if "design_session" not in st.session_state:
        st.session_state.design_session = None

    c_left, c_mid, c_right = st.columns([1, 1, 2])
    with c_left:
        preview_btn = st.button("🔍 自動提案", type="primary", use_container_width=True)
    with c_mid:
        reset_btn = st.button("♻ 再割当", use_container_width=True, disabled=st.session_state.design_session is None)
    with c_right:
        reserve_btn = st.button("💾 この内容で保存", type="secondary", use_container_width=True, disabled=st.session_state.design_session is None)

    def build_spec():
        return DesignSpec(
            title=title, reference_no=selected_ref.no,
            reference_title=selected_ref.title, mode=mode,
            total_tickets=total_tickets, price_per_spin=price_per_spin,
            target_profit_rate=profit_rate / 100,
            target_return_rate=return_rate / 100,
            tiers=tier_specs, note=note,
            stock_mode=stock_mode,
            base_markup_rate=base_markup_rate,
        )

    if preview_btn or reset_btn:
        if not tier_specs or all(t.count == 0 for t in tier_specs):
            st.error("当たり数が全て0です")
        else:
            spec = build_spec()
            with st.spinner("在庫を読込中..."):
                result = design(spec, reference=selected_ref)
            # session に保存: tier_selections は (tab, row_idx) のリスト
            tier_selections = {
                tr.name: [(it.tab, it.row_idx) for it in tr.selected]
                for tr in result.tier_results
            }
            st.session_state.design_session = {
                "spec": spec,
                "tier_selections": tier_selections,
                "ref": selected_ref,
                "inventory": result.all_inventory,
            }

    session = st.session_state.design_session
    if session:
        # 最新パラメータでspec更新（ユーザーが上のフォーム値を変えた場合の反映）
        live_spec = build_spec()
        # tier_selections は等名ベースなので、フォームの等構成変更にも追従
        for t in live_spec.tiers:
            session["tier_selections"].setdefault(t.name, [])
        # 不要な等を削除
        valid_names = {t.name for t in live_spec.tiers}
        session["tier_selections"] = {k: v for k, v in session["tier_selections"].items() if k in valid_names}
        session["spec"] = live_spec

        result = build_result_from_selections(
            live_spec, session["tier_selections"], session["inventory"], reference=session["ref"],
        )

        # --- 総合サマリ ---
        c1, c2, c3 = st.columns(3)
        c1.metric("売上（円）", f"¥{result.total_revenue:,}")
        c2.metric("仕入れ合計", f"¥{result.total_cost:,}", help="仕入れ価格未入力のカードは相場で代用")
        c3.metric("粗利", f"¥{result.gross_profit:,}", delta=f"{result.actual_profit_rate:.1%}")

        c4, c5, c6 = st.columns(3)
        c4.metric(
            "顧客還元率", f"{result.customer_return_rate:.1%}",
            help="顧客が見る還元率（コイン額面ベース）= コイン合計 / 売上",
        )
        c5.metric(
            "実還元率", f"{result.real_return_rate:.1%}",
            help="運営の本当の還元率 = 仕入れ合計 / 売上",
        )
        c6.metric(
            "上乗せ差分", f"{(result.customer_return_rate - result.real_return_rate)*100:+.1f}pt",
            help="顧客還元率と実還元率の差。コイン上乗せによる見せかけの還元率増分",
        )

        with st.expander("📊 詳細な金額内訳"):
            st.markdown(f"""
| 項目 | 金額 |
|---|---|
| 売上（円・1コイン=1円換算） | ¥{result.total_revenue:,} |
| カード相場合計 | ¥{result.total_market:,} |
| カードコイン額面合計（相場×上乗せ） | ¥{result.total_coin_value:,} |
| カード仕入れ合計（実コスト） | ¥{result.total_cost:,} |
| **粗利（売上 − 仕入れ）** | **¥{result.gross_profit:,}** |
""")

        # --- 警告 ---
        from warnings_gen import group_by_category, severity_counts, CATEGORY_LABELS, SEV_CRITICAL, SEV_WARNING, SEV_INFO
        warns = result.warnings
        if warns:
            sev_c = severity_counts(warns)
            badge = []
            if sev_c.get(SEV_CRITICAL): badge.append(f"🔴 {sev_c[SEV_CRITICAL]}")
            if sev_c.get(SEV_WARNING): badge.append(f"🟡 {sev_c[SEV_WARNING]}")
            if sev_c.get(SEV_INFO): badge.append(f"🔵 {sev_c[SEV_INFO]}")
            has_critical = sev_c.get(SEV_CRITICAL, 0) > 0
            with st.expander(f"⚠ 警告・提案 {'  '.join(badge)}", expanded=has_critical):
                groups = group_by_category(warns)
                for cat_key, cat_warns in groups.items():
                    st.markdown(f"### {CATEGORY_LABELS.get(cat_key, cat_key)}")
                    for w in cat_warns:
                        container = {SEV_CRITICAL: st.error, SEV_WARNING: st.warning}.get(w.severity, st.info)
                        msg = f"**{w.icon} {w.title}**"
                        if w.detail: msg += f"\n\n{w.detail}"
                        if w.suggestion: msg += f"\n\n💡 {w.suggestion}"
                        container(msg)

        # --- 各等の編集UI ---
        st.markdown("### 各等の構成（手動で追加・削除できます）")
        all_inv = session["inventory"]

        # 現在どの (tab, row_idx) が他の等で使われているか把握（同じ設計内での重複回避）
        used_in_design = set()
        for keys in session["tier_selections"].values():
            for k in keys:
                used_in_design.add(k)

        for tspec in live_spec.tiers:
            tname = tspec.name
            current_keys = session["tier_selections"].get(tname, [])
            current_items = []
            inv_by_key = {(it.tab, it.row_idx): it for it in all_inv}
            for k in current_keys:
                it = inv_by_key.get(k)
                if it:
                    current_items.append(it)

            avg = sum(it.price for it in current_items) // len(current_items) if current_items else 0
            dev_badge = ""
            if current_items and tspec.target_price > 0:
                dev = avg / tspec.target_price - 1
                dev_badge = (
                    f" 🔴{dev:+.0%}" if abs(dev) >= 0.3
                    else f" 🟡{dev:+.0%}" if abs(dev) >= 0.1
                    else f" 🟢{dev:+.0%}"
                )

            header = (
                f"{tname}｜目標¥{tspec.target_price:,} × {tspec.count}枚｜"
                f"選定{len(current_items)}枚"
                + (f" 平均¥{avg:,}" if avg else "")
                + dev_badge
                + (f" ⚠不足{tspec.count - len(current_items)}枚" if len(current_items) < tspec.count else "")
                + (f" ⚠超過{len(current_items) - tspec.count}枚" if len(current_items) > tspec.count else "")
            )
            with st.expander(header, expanded=True):
                # --- 現在選定中のカード（削除ボタン付）---
                if current_items:
                    st.markdown("**現在の選定**")
                    for i, it in enumerate(current_items):
                        cols = st.columns([5, 2, 2, 1])
                        cols[0].markdown(f"{it.name} `{it.series or ''}`")
                        cols[1].markdown(f"¥{it.price:,}")
                        dev_per = (it.price / tspec.target_price - 1) if tspec.target_price else 0
                        cols[2].markdown(f"{dev_per:+.0%}" if tspec.target_price else "-")
                        if cols[3].button("❌", key=f"rm_{tname}_{i}"):
                            st.session_state.design_session["tier_selections"][tname].pop(i)
                            st.rerun()
                else:
                    st.info("まだカードが選ばれていません。下の候補から追加してください。")

                # --- 代替候補 ---
                st.markdown("**📋 候補カード**")
                # 目標に近い順でソート
                target = tspec.target_price
                # 無在庫モードは全カード、在庫モードは残数量ありのみ
                if live_spec.stock_mode == "no_stock":
                    available = [
                        it for it in all_inv
                        if (it.tab, it.row_idx) not in used_in_design
                    ]
                else:
                    available = [
                        it for it in all_inv
                        if it.available_qty > 0 and (it.tab, it.row_idx) not in used_in_design
                    ]
                if target > 0:
                    available = sorted(available, key=lambda x: abs(x.price - target))
                else:
                    available = sorted(available, key=lambda x: -x.price)

                # 価格帯フィルタ
                fcol1, fcol2, fcol3 = st.columns(3)
                with fcol1:
                    price_range = st.select_slider(
                        "目標からの乖離",
                        options=["±10%", "±25%", "±50%", "全て"],
                        value="±25%" if target > 0 else "全て",
                        key=f"range_{tname}",
                    )
                with fcol2:
                    filter_tab = st.multiselect(
                        "区分", options=["PSA10", "BOX"],
                        default=["PSA10", "BOX"], key=f"tabf_{tname}",
                    )
                with fcol3:
                    max_show = st.number_input(
                        "表示件数", min_value=5, max_value=200, value=20, step=5, key=f"show_{tname}",
                    )

                # 適用フィルタ
                filtered_candidates = [it for it in available if it.tab in filter_tab]
                if target > 0 and price_range != "全て":
                    tol = {"±10%": 0.10, "±25%": 0.25, "±50%": 0.50}[price_range]
                    filtered_candidates = [
                        it for it in filtered_candidates
                        if (1 - tol) * target <= it.price <= (1 + tol) * target
                    ]

                st.caption(f"候補 {len(filtered_candidates)} 件中、上位 {min(max_show, len(filtered_candidates))} 件を表示")

                for j, it in enumerate(filtered_candidates[:max_show]):
                    cols = st.columns([4, 2, 2, 1, 1])
                    cols[0].markdown(f"{it.name} `{it.series or ''}`")
                    cols[1].markdown(f"¥{it.price:,}")
                    dev_per = (it.price / target - 1) if target else 0
                    cols[2].markdown(f"{dev_per:+.0%}" if target else "-")
                    cols[3].markdown(f"[{it.tab}]")
                    if cols[4].button("➕", key=f"add_{tname}_{j}_{it.row_idx}"):
                        st.session_state.design_session["tier_selections"][tname].append((it.tab, it.row_idx))
                        st.rerun()

        # --- 保存 ---
        if reserve_btn:
            if result.total_cost == 0:
                st.error("カードが1枚も選ばれていません")
            else:
                with st.spinner("保存中..."):
                    pid = save_reservation(result)
                st.success(f"✅ 予約中として保存しました: **{pid}**")
                st.session_state.design_session = None
                st.balloons()


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
        # snkrdunk URL列がない既存stateに後付け
        for c in state["cards"]:
            c.setdefault("snkrdunk URL", "")

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
                "上乗せ倍率": 0.0, "除外": False,
            }])

        # 列順を統一
        col_order = ["賞", "カード名", "レアリティ", "本数", "実価値/枚(円)", "snkrdunk URL", "上乗せ倍率", "除外"]
        for c in col_order:
            if c not in df_init.columns:
                df_init[c] = "" if c == "snkrdunk URL" else 0
        df_init = df_init[col_order]

        edited = st.data_editor(
            df_init,
            num_rows="dynamic",
            use_container_width=True,
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
                is_pack_target = bool(_re.search(r'(パック|BOX|ボックス|箱)', target_name + name))
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
                    is_pack_target = bool(_re.search(r'(パック|BOX|ボックス|箱)', target_name))
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
                    is_pack_target = bool(_re.search(r'(パック|BOX|ボックス|箱)', target_name + name))
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
            "本数": 0, "実価値/枚(円)": 0, "上乗せ倍率": 0.0, "除外": False
        })
        df_calc["本数"] = pd.to_numeric(df_calc["本数"], errors="coerce").fillna(0).astype(int)
        df_calc["実価値/枚(円)"] = pd.to_numeric(df_calc["実価値/枚(円)"], errors="coerce").fillna(0).astype(int)
        df_calc["上乗せ倍率"] = pd.to_numeric(df_calc["上乗せ倍率"], errors="coerce").fillna(0.0).astype(float)

        # 各行の倍率（0なら一括倍率を使う）
        df_calc["適用倍率"] = df_calc["上乗せ倍率"].where(df_calc["上乗せ倍率"] > 0, bulk_markup)
        df_calc["表示PT/枚"] = (df_calc["実価値/枚(円)"] * df_calc["適用倍率"]).round().astype(int)
        df_calc["表示PT合計"] = df_calc["表示PT/枚"] * df_calc["本数"]
        df_calc["実価値合計"] = df_calc["実価値/枚(円)"] * df_calc["本数"]
        # 除外を反映
        active = df_calc[~df_calc["除外"].astype(bool)]

        total_card_qty = int(active["本数"].sum())
        total_real = int(active["実価値合計"].sum())
        total_pt_view = int(active["表示PT合計"].sum())

        revenue = price * total_tickets + charge_amount
        cost = total_real
        gross = revenue - cost
        gross_rate = (gross / revenue) if revenue else 0
        real_return = (cost / revenue) if revenue else 0
        coin_return = (total_pt_view / revenue) if revenue else 0
        markup_diff = coin_return - real_return

        # ---- 結果表示 ----
        st.markdown("##### 📊 計算結果")
        r1 = st.columns(4)
        r1[0].metric("売上", f"¥{revenue:,}", help=f"単価 ¥{price:,} × {total_tickets:,}口" + (f" + 課金 ¥{charge_amount:,}" if charge_amount else ""))
        r1[1].metric("仕入れ合計", f"¥{cost:,}")
        r1[2].metric("粗利", f"¥{gross:,}", f"{gross_rate:.1%}")
        r1[3].metric("カード合計", f"{total_card_qty:,}枚")

        r2 = st.columns(4)
        r2[0].metric("顧客還元率(コイン)", f"{coin_return:.1%}",
                     help="顧客が見る還元率 = 表示PT合計 / 売上")
        r2[1].metric("実還元率(仕入れ)", f"{real_return:.1%}",
                     help="運営の本当の還元率 = 仕入れ合計 / 売上")
        r2[2].metric("上乗せ差分", f"+{markup_diff:.1%}")
        r2[3].metric("総口数 vs カード本数",
                     f"{total_tickets:,} / {total_card_qty:,}",
                     delta=f"差 {total_tickets - total_card_qty:+,}枚" if total_tickets != total_card_qty else "一致",
                     delta_color="off" if total_tickets == total_card_qty else "inverse")

        if total_tickets != total_card_qty and total_card_qty > 0:
            st.warning(f"⚠️ 総口数 {total_tickets:,} と カード本数 {total_card_qty:,} が一致しません")

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

        if coin_return > 1.0:
            st.error(f"❌ 顧客還元率が100%超え（{coin_return:.1%}）。上乗せ倍率を見直してください")
        elif gross_rate < 0:
            st.error(f"❌ 粗利マイナス（仕入れが売上を超過）")

        # ---- 出現率(参考) ----
        if total_card_qty > 0:
            df_calc["出現率"] = (df_calc["本数"] / total_card_qty * 100).round(2).astype(str) + "%"
            with st.expander("📋 行ごとの内訳"):
                show_cols = ["賞", "カード名", "レアリティ", "本数", "出現率",
                             "実価値/枚(円)", "適用倍率", "表示PT/枚", "表示PT合計", "実価値合計", "除外"]
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
                for e in check_items:
                    cm = e['cm']
                    src_low = (cm.source or '').lower()
                    if e.get('src_type') == 'master':
                        label = "🟠 仮採用(カードマスタDB由来=同名同レアで自動マッチ)"
                    elif 'clip' in src_low:
                        label = "🟡 仮採用(CLIP)"
                    elif 'review' in src_low:
                        label = "⏸ ワーカー要確認"
                    elif 'confirmed_by_worker' in src_low or 'manual_ui' in src_low or 'manual_url' in src_low:
                        label = "🟢 画像選定済"
                    else:
                        label = f"❓ {cm.source[:20]}"
                    # トレカセンター画像取得
                    tc_img = _get_tc_image(_tmpl_url, e['cn'], e['rar']) if _tmpl_url else ''
                    ck_cols = st.columns([0.8, 2.2, 1.8, 2, 1, 1.2])
                    with ck_cols[0]:
                        if tc_img:
                            st.image(tc_img, width=80)
                        else:
                            st.caption("画像なし")
                    with ck_cols[1]:
                        st.markdown(f"**{e['row']['賞']} {e['cn']}**")
                        st.caption(f"{e['rar']} ｜ {label}")
                    with ck_cols[2]:
                        if _tmpl_url:
                            st.link_button("🎴 競合商品ページ", _tmpl_url, use_container_width=True)
                        else:
                            st.caption("商品URLなし")
                    with ck_cols[3]:
                        if cm.snkrdunk_url:
                            st.link_button("🔗 スニダンURL", cm.snkrdunk_url, use_container_width=True)
                        else:
                            st.caption("URLなし")
                    with ck_cols[4]:
                        st.markdown(f"**¥{int(cm.buy_price):,}**")
                    with ck_cols[5]:
                        # 作業者名なくても押せるように(未入力なら「設計者」記録)
                        if st.button("✅確認OK", key=f"confirm_{cur_base_no}_{e['cn']}_{e['rar']}_{e['ri']}",
                                      use_container_width=True, type="primary"):
                            if not st.session_state.get('_worker_name'):
                                st.session_state['_worker_name'] = '設計者'
                            _save_card_match(cur_base_no, e['cn'], e['rar'], str(e['row']['賞']), int(e['row']['本数']),
                                             cm.snkrdunk_url, cm.buy_price,
                                             f"設計時確認OK(元={label})",
                                             status='confirmed_by_designer')
                            st.success(f"✅ {e['cn']} を確定登録")
                            st.rerun()
                    st.divider()
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


# ---------- 🖼 カード照合タブ ----------
@st.cache_data(ttl=3600)
def _load_match_data():
    """商品別カード照合 + 商品別カードマスタ(手動採用済みかチェック) を統合読込"""
    import re
    from research import open_research, load_per_product_card_index, clear_per_product_card_cache
    # 採用済みチェック用に商品別カードマスタDB読込
    clear_per_product_card_cache()
    per_db = load_per_product_card_index()
    # 在庫スプシ(ポケモン在庫管理)の「スニダン used URL」列を 依頼者入力済として「確定扱い」
    from sheets_client import get_client as _gc_inv
    inv_url_by_name = {}  # カード名小文字 → {url, price}
    try:
        _ss_inv = _gc_inv().open_by_key(config.get_active_inventory_sheet_id())
        for _tab in ['PSA10在庫登録', 'PSA10在庫登録 のコピー']:
            try:
                _wsi = _ss_inv.worksheet(_tab)
                _rs = _wsi.get_all_values()
                if not _rs: continue
                _h = _rs[0]
                _ci_name = _h.index('カード名') if 'カード名' in _h else -1
                _ci_url = _h.index('スニダン used URL') if 'スニダン used URL' in _h else -1
                _ci_pri = _h.index('相場(1枚)') if '相場(1枚)' in _h else (_h.index('相場(1枚)') if '相場(1枚)' in _h else -1)
                if _ci_name < 0 or _ci_url < 0: continue
                for _r in _rs[1:]:
                    if len(_r) <= max(_ci_name, _ci_url): continue
                    _nm = (_r[_ci_name] or '').strip().lower()
                    _u = (_r[_ci_url] or '').strip()
                    if _nm and _u.startswith('http'):
                        if _nm not in inv_url_by_name:
                            try: _p = int((_r[_ci_pri] or '0').replace(',', '').replace('¥', '')) if _ci_pri >= 0 and len(_r) > _ci_pri else 0
                            except: _p = 0
                            inv_url_by_name[_nm] = {'url': _u, 'price': _p}
            except Exception: pass
    except Exception: pass
    # 確定済(ワーカー対応不要) + CLIP仮採用も「設計時チェック予定」でワーカー対応不要扱い
    DONE_KW = ('manual_ui', 'manual_url', 'manual_exclude', 'confirmed_by_worker', 'confirmed_by_designer', 'provisional_clip')
    manual_done = {k for k, cm in per_db.items() if any(kw in (cm.source or '').lower() for kw in DONE_KW)}
    REVIEW_KW = ('manual_review', 'provisional_review')
    review_keys = {k for k, cm in per_db.items() if any(kw in (cm.source or '').lower() for kw in REVIEW_KW)}
    PROV_KW = ('provisional_clip',)
    provisional_keys = {k for k, cm in per_db.items() if any(kw in (cm.source or '').lower() for kw in PROV_KW)}

    items = []
    try:
        ss = open_research()
        ws_match = ss.worksheet('商品別カード照合')
        # 数式そのものを取得(=HYPERLINK/=IMAGE のURL抽出のため)
        rows = ws_match.get_all_values(value_render_option='FORMULA')
        if rows and len(rows) >= 2:
            h = {col: i for i, col in enumerate(rows[0])}
            def _cell(r, name):
                idx = h.get(name)
                if idx is None or idx >= len(r):
                    return ''
                return str(r[idx] or '').strip()
            for r in rows[1:]:
                if not r or not _cell(r, '商品No'):
                    continue
                tc_img_raw = _cell(r, 'トレカ画像')
                tc_img = ''
                if tc_img_raw and 'IMAGE(' in tc_img_raw:
                    m = re.search(r'IMAGE\("([^"]+)"', tc_img_raw)
                    if m: tc_img = m.group(1)
                cands = []
                for j in range(1, 4):
                    img_raw = _cell(r, f'候補{j}画像')
                    img_url = ''
                    if img_raw and 'IMAGE(' in img_raw:
                        m = re.search(r'IMAGE\("([^"]+)"', img_raw)
                        if m: img_url = m.group(1)
                    url_raw = _cell(r, f'候補{j}URL')
                    url = ''
                    if 'HYPERLINK(' in url_raw:
                        m = re.search(r'HYPERLINK\("([^"]+)"', url_raw)
                        if m: url = m.group(1)
                    name = _cell(r, f'候補{j}名')
                    sim = _cell(r, f'候補{j}類似度')
                    try: sim = float(sim) if sim else 0
                    except: sim = 0
                    if name or img_url:
                        cands.append({'name': name, 'url': url, 'img_url': img_url, 'sim': sim})
                base_link_raw = _cell(r, '商品ページ')
                base_url = ''
                if 'HYPERLINK(' in base_link_raw:
                    m = re.search(r'HYPERLINK\("([^"]+)"', base_link_raw)
                    if m: base_url = m.group(1)
                base_no_v = _cell(r, '商品No')
                card_name_v = _cell(r, 'カード名')
                rarity_v = _cell(r, 'レアリティ')
                # 商品別カードマスタDBで採用済みかチェック
                db_key = f'{base_no_v}|{card_name_v}|{rarity_v}'.lower()
                db_done = db_key in manual_done
                # 登録済みURL/価格/採用方法
                db_url = ''
                db_price = 0
                db_src = ''
                if db_key in per_db:
                    db_url = per_db[db_key].snkrdunk_url
                    db_price = per_db[db_key].buy_price
                    db_src = per_db[db_key].source
                adopt_cell = _cell(r, '採用(1/2/3/手動URL/除外)') if '採用(1/2/3/手動URL/除外)' in h else _cell(r, '採用方法')
                adopt_final = adopt_cell if adopt_cell else ('✅DB済' if db_done else '')
                # 候補URL列(シンプル版照合タブ用)
                if not cands:
                    for j in range(1, 4):
                        if f'候補{j}URL' in h:
                            cand_url_raw = _cell(r, f'候補{j}URL')
                            # 純粋なURL or HYPERLINK 数式
                            url_v = ''
                            if cand_url_raw.startswith('http'):
                                url_v = cand_url_raw
                            elif 'HYPERLINK(' in cand_url_raw:
                                m = re.search(r'HYPERLINK\("([^"]+)"', cand_url_raw)
                                if m: url_v = m.group(1)
                            name_v = _cell(r, f'候補{j}名')
                            sim_v = _cell(r, f'候補{j}類似度')
                            try: sim_v = float(sim_v) if sim_v else 0
                            except: sim_v = 0
                            if url_v or name_v:
                                cands.append({'name': name_v, 'url': url_v, 'img_url': '', 'sim': sim_v})
                review_flag = db_key in review_keys
                prov_flag = db_key in provisional_keys
                # 在庫スプシ(依頼者入力)にURLあれば「確定扱い」
                _name_key = card_name_v.lower()
                if _name_key in inv_url_by_name and not (db_done or review_flag):
                    _iv = inv_url_by_name[_name_key]
                    db_done = True
                    adopt_final = adopt_final or '✅在庫スプシ由来(依頼者入力)'
                    if not db_url:
                        db_url = _iv['url']
                        db_price = _iv['price']
                        db_src = '在庫スプシ(依頼者入力)'
                items.append({
                    'no': _cell(r, 'No'),
                    'base_no': base_no_v,
                    'base_url': base_url,
                    'card_name': card_name_v,
                    'rarity': rarity_v,
                    'tier': _cell(r, '賞'),
                    'qty': _cell(r, '数量'),
                    'tc_image_url': tc_img,
                    'cands': cands,
                    'adopt': adopt_final,
                    'reason': _cell(r, '判定理由') if '判定理由' in h else _cell(r, '備考'),
                    'source_tab': '照合',
                    'db_done': db_done,
                    'db_url': db_url,
                    'db_price': db_price,
                    'db_src': db_src,
                    'review_flag': review_flag,
                    'prov_flag': prov_flag,
                })
    except Exception as e:
        st.warning(f'照合タブ読込失敗: {e}')
    return items


# パック数/枚数/個数 multiplier 検出
import re as _re_mult
MULTIPLIER_PATTERN = _re_mult.compile(
    r'[(（]\s*(\d+)\s*(PACK|パック|枚|個|セット|SET|set)\s*[)）]',
    _re_mult.IGNORECASE,
)


def extract_multiplier_and_base(card_name):
    """カード名から (3PACK) などを検出して multiplier とベース名を返す
    例: 'ブラックボルト(3PACK)' → (3, 'ブラックボルト(PACK)')
        'シャワーズ(5枚)'      → (5, 'シャワーズ(枚)')
        '通常カード'           → (1, '通常カード')
    """
    if not card_name:
        return 1, card_name
    m = MULTIPLIER_PATTERN.search(card_name)
    if m:
        mult = int(m.group(1))
        unit = m.group(2)
        # 数字部分を除去して単位だけ残す
        base = MULTIPLIER_PATTERN.sub(f'({unit})', card_name)
        return mult, base
    return 1, card_name


def _save_card_match(base_no, card_name, rarity, tier, qty, snk_url, price, source_note, status='confirmed_by_worker'):
    """商品別カードマスタに高速append
    status: confirmed_by_worker / confirmed_by_designer / provisional_review / provisional_clip
    """
    from research import open_research, clear_per_product_card_cache
    from datetime import datetime
    import streamlit as _st
    multiplier, _ = extract_multiplier_and_base(card_name)
    final_price = int(price) * multiplier if price else 0
    note_suffix = f' ×{multiplier}={final_price}' if multiplier > 1 else ''
    worker = _st.session_state.get('_worker_name', '不明')
    ss = open_research()
    try:
        ws_per = ss.worksheet('商品別カードマスタ')
    except Exception:
        ws_per = ss.add_worksheet(title='商品別カードマスタ', rows=10000, cols=15)
        ws_per.update([['商品No', 'リライトNo', 'カード名', 'レアリティ', '賞', '数量',
                       'snkrdunk URL', '買取価格(円)', '価格取得元', 'スニダン商品名',
                       '採用方法', '更新日時']], 'A1', value_input_option='USER_ENTERED')
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    # 採用方法に作業者名 + 状態タグを含める
    full_status = f'{status} | {source_note}{note_suffix} | by:{worker}'
    row = [base_no, '', card_name, rarity, tier, qty, snk_url, final_price,
           source_note + note_suffix, '', full_status, now]
    ws_per.append_row(row, value_input_option='USER_ENTERED')
    clear_per_product_card_cache()


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
        # rarity一致しなくても name一致で fallback
        for c in (detail.get('cards') or []):
            if (c.get('name') or '').strip() == nm:
                return c.get('image_url') or ''
    except Exception:
        pass
    return ''


def _fetch_price_for_url(snk_url, card_name, rarity):
    """指定URLから価格取得"""
    import re as _re_fetch
    from snkrdunk_client import fetch_recent_price, fetch_apparel_meta
    meta = fetch_apparel_meta(snk_url.rsplit("/", 1)[-1]) if "/apparels/" in snk_url else None
    target_name = (meta.get("name") or "") if meta else ""
    is_pack = bool(_re_fetch.search(r'(パック|BOX|ボックス|箱)', target_name + card_name))
    grade = "PSA10" if "PSA" in (target_name + rarity).upper() else ""
    try:
        price, msg = fetch_recent_price(snk_url, grade, is_pack=is_pack)
        return price or 0, msg
    except Exception as ex:
        return 0, f'ERR:{str(ex)[:50]}'


with tab_match:
    import re as _re_match
    st.subheader("🖼 商品別カード照合")
    st.caption("同名異版のカード(=シャワーズ マスボミラー151版 vs SV4a版 など)の正しいスニダンURLを選ぶ。採用後は商品別カードマスタDBに保存→ツール各所で自動反映。")

    # 作業者別集計
    with st.expander("📊 作業者別 作業件数", expanded=False):
        if st.button("🔄 集計を再計算", key="match_stats_reload"):
            st.rerun()
        try:
            from research import open_research as _or_stats
            ws_stats = _or_stats().worksheet('商品別カードマスタ')
            stats_rows = ws_stats.get_all_records()
            from collections import Counter
            worker_confirmed = Counter()
            worker_review = Counter()
            for r in stats_rows:
                src = str(r.get('採用方法', ''))
                m = _re_match.search(r'by:([^|]+?)(?:\||$)', src)
                worker = m.group(1).strip() if m else '不明'
                src_low = src.lower()
                if 'confirmed_by_worker' in src_low:
                    worker_confirmed[worker] += 1
                elif 'provisional_review' in src_low or 'manual_review' in src_low:
                    worker_review[worker] += 1
                elif 'manual_ui' in src_low or 'manual_url' in src_low:
                    worker_confirmed[worker] += 1
            st.markdown("**✅ 確定登録**")
            if worker_confirmed:
                import pandas as _pd_stats
                df_stats = _pd_stats.DataFrame([
                    {'作業者': w, '確定件数': n}
                    for w, n in worker_confirmed.most_common()
                ])
                st.dataframe(df_stats, use_container_width=True, hide_index=True)
                st.metric("合計", f"{sum(worker_confirmed.values())}件")
            else:
                st.info("確定登録なし")
            st.markdown("**⏸ 要確認(保留)**")
            if worker_review:
                st.dataframe(
                    _pd_stats.DataFrame([{'作業者': w, '要確認件数': n} for w, n in worker_review.most_common()]),
                    use_container_width=True, hide_index=True,
                )
        except Exception as e:
            st.warning(f"集計取得失敗: {e}")

    # ローカル採用済みセット(スプシ反映を待たずに未対応リストから即座に除外)
    if '_match_done_local' not in st.session_state:
        st.session_state['_match_done_local'] = set()

    btn_cols = st.columns([2, 2, 6])
    with btn_cols[0]:
        if st.button("🔄 データ完全再読込", key="match_reload",
                      help="スプシから読み直し+ローカル採用記録もリセット"):
            _load_match_data.clear()
            st.session_state['_match_done_local'] = set()
            st.session_state['_match_idx'] = 0
            st.rerun()
    with btn_cols[1]:
        st.caption(f"今セッション採用済: {len(st.session_state['_match_done_local'])}件")

    all_items = _load_match_data()
    if not all_items:
        st.info("照合タブが空です。Step Bを先に実行してください。")
    else:
        # フィルタ
        fc = st.columns([2, 1.4, 1, 1, 1])
        with fc[0]:
            f_search = st.text_input("🔍 商品No or カード名", key="match_search")
        with fc[1]:
            f_mode = st.radio("表示", ["未対応のみ", "⏸ 要確認のみ", "🟡 仮採用のみ", "採用済のみ(修正用)", "全件"],
                              key="match_mode", horizontal=True,
                              help="未対応=ワーカー作業対象 / 要確認=後で対応 / 仮採用=設計時確認予定 / 採用済=確定済")
        with fc[2]:
            f_min_sim = st.number_input("候補1類似度 ≥", value=0.0, step=0.05, key="match_min_sim")
        with fc[3]:
            f_max_sim = st.number_input("候補1類似度 <", value=1.01, step=0.05, key="match_max_sim")
        with fc[4]:
            f_sort = st.selectbox("並び", ["No順", "類似度低い順", "類似度高い順"], key="match_sort")

        def _item_key(x):
            return (str(x['base_no']), x['card_name'], x['rarity'])

        filtered = all_items
        if f_search:
            s = f_search.strip().lower()
            filtered = [x for x in filtered if s in x['card_name'].lower() or s in str(x['base_no']).lower() or s in str(x['no']).lower()]
        local_done = st.session_state['_match_done_local']
        # 「📝 修正」ボタンから来た場合は1回だけ全件モードで強制表示
        mode_effective = f_mode
        if st.session_state.pop('_match_force_all', False):
            mode_effective = "全件"
        if mode_effective == "未対応のみ":
            # 未対応 = 採用列なし & ローカル未採用 & 要確認なし & 仮採用なし
            filtered = [x for x in filtered if not x['adopt'].strip()
                        and _item_key(x) not in local_done
                        and not x.get('review_flag')
                        and not x.get('prov_flag')]
        elif mode_effective == "⏸ 要確認のみ":
            filtered = [x for x in filtered if x.get('review_flag')]
        elif mode_effective == "🟡 仮採用のみ":
            filtered = [x for x in filtered if x.get('prov_flag')]
        elif mode_effective == "採用済のみ(修正用)":
            filtered = [x for x in filtered if (x['adopt'].strip() or _item_key(x) in local_done)
                        and not x.get('review_flag')
                        and not x.get('prov_flag')]
        def _top1_sim(x):
            return x['cands'][0]['sim'] if x['cands'] else 0
        filtered = [x for x in filtered if f_min_sim <= _top1_sim(x) < f_max_sim]
        if f_sort == "類似度低い順":
            filtered.sort(key=_top1_sim)
        elif f_sort == "類似度高い順":
            filtered.sort(key=lambda x: -_top1_sim(x))

        st.markdown(f"**{len(filtered):,}件 / 全{len(all_items):,}件**")

        # 採用済/要確認/仮採用モードはリスト表示優先
        if f_mode in ("採用済のみ(修正用)", "⏸ 要確認のみ", "🟡 仮採用のみ") and filtered:
            import pandas as _pd_match
            df_match = _pd_match.DataFrame([{
                "商品No": x['base_no'],
                "賞": x['tier'],
                "カード名": x['card_name'],
                "レア": x['rarity'],
                "数量": x['qty'],
                "登録価格(円)": int(x.get('db_price') or 0),
                "登録URL": x.get('db_url') or '',
                "採用方法": x.get('db_src') or '',
                "競合商品ページ": x.get('base_url') or '',
            } for x in filtered])
            st.caption("⬇️ 行をクリックして選択 → 下に「📝 このカードを修正」ボタンが出ます")
            ev_match = st.dataframe(
                df_match, use_container_width=True, hide_index=True,
                on_select="rerun", selection_mode="single-row",
                column_config={
                    "登録URL": st.column_config.LinkColumn("登録URL"),
                    "競合商品ページ": st.column_config.LinkColumn("競合商品ページ"),
                    "登録価格(円)": st.column_config.NumberColumn("登録価格(円)", format="¥%d"),
                },
                key="match_done_list_table",
            )
            sel_rows = (ev_match.selection or {}).get("rows", [])
            if sel_rows:
                target = filtered[sel_rows[0]]
                cc = st.columns([3, 1])
                with cc[0]:
                    st.success(f"選択中: 商品{target['base_no']} {target['card_name']} ({target['rarity']})")
                with cc[1]:
                    if st.button("📝 このカードを修正", type="primary", use_container_width=True, key="match_edit_btn"):
                        # 全件モードでこのカードに飛ぶ(widget keyは触らず force_all フラグ使用)
                        st.session_state["_match_force_all"] = True
                        # 全件モード時の対象itemのインデックスを正しく特定
                        all_idx = None
                        target_key = _item_key(target)
                        for i, x in enumerate(all_items):
                            if _item_key(x) == target_key:
                                all_idx = i
                                break
                        st.session_state["_match_idx"] = all_idx if all_idx is not None else 0
                        st.rerun()
            st.stop()  # ← 採用済リスト表示時は詳細画面を出さない

        if not filtered:
            st.info("該当なし")
        else:
            # 現在表示インデックス
            if '_match_idx' not in st.session_state:
                st.session_state['_match_idx'] = 0
            idx = max(0, min(st.session_state['_match_idx'], len(filtered) - 1))
            item = filtered[idx]

            nav = st.columns([1, 1, 6, 1, 1])
            with nav[0]:
                if st.button("◀ 前", key=f"match_prev_{idx}", disabled=(idx == 0)):
                    st.session_state['_match_idx'] = idx - 1
                    st.rerun()
            with nav[1]:
                if st.button("次 ▶", key=f"match_next_{idx}", disabled=(idx >= len(filtered) - 1)):
                    st.session_state['_match_idx'] = idx + 1
                    st.rerun()
            with nav[2]:
                st.progress((idx + 1) / len(filtered), text=f"{idx + 1} / {len(filtered)}")
            with nav[3]:
                # key を idx ごとに変えて widget 状態を毎回新規にする(古いidx記憶バグ回避)
                jump = st.number_input(
                    "⮕", min_value=1, max_value=len(filtered), value=idx + 1,
                    label_visibility="collapsed", key=f"match_jump_{idx}",
                )
                if jump - 1 != idx:
                    st.session_state['_match_idx'] = int(jump) - 1
                    st.rerun()
            with nav[4]:
                st.caption(f"件目")

            st.markdown("---")
            # ヘッダ情報
            cur_mult, cur_base = extract_multiplier_and_base(item['card_name'])
            display_name = item['card_name']
            if cur_mult > 1:
                display_name += f"  🔢 ×{cur_mult}"
            st.markdown(f"### {display_name} ({item['rarity']})")
            head_cols = st.columns([3, 2])
            with head_cols[0]:
                if item['base_url']:
                    st.markdown(f"📦 [商品No.{item['base_no']} 競合ページを開く]({item['base_url']})")
                else:
                    st.markdown(f"📦 商品No.{item['base_no']}")
                st.caption(f"賞: {item['tier']} / 数量: {item['qty']} / 照合行No: {item['no']}")
                if cur_mult > 1:
                    st.caption(f"🔢 multiplier={cur_mult} (スニダン単価×{cur_mult}で実価値計算)")
                if item['reason']:
                    st.caption(f"判定理由: {item['reason']}")
            with head_cols[1]:
                if item.get('review_flag'):
                    st.info("⏸ 要確認(保留中)")
                elif item.get('db_done'):
                    st.success("✅ 商品別カードマスタDBに保存済(手動採用)")
                elif item['adopt']:
                    st.success(f"✅ 採用済: {item['adopt']}")
                else:
                    st.warning("⚠️ 未対応")

            # 登録済みURL情報 (DB保存済みの場合)
            if item.get('db_url') or item.get('db_price', 0) > 0:
                with st.container(border=True):
                    db_cols = st.columns([4, 1.5, 1.5, 2])
                    with db_cols[0]:
                        st.markdown(f"**📌 登録済みスニダンURL:** [{item['db_url'] or '(空=除外扱い)'}]({item['db_url']})" if item['db_url'] else "**📌 登録済み:** (URLなし=除外扱い)")
                    with db_cols[1]:
                        st.metric("登録価格", f"¥{int(item['db_price']):,}")
                    with db_cols[2]:
                        st.caption(f"採用方法")
                        st.caption(f"{item['db_src'][:30]}")
                    with db_cols[3]:
                        if item['db_url']:
                            st.link_button("🔗 登録済URLを開く", item['db_url'], use_container_width=True)
                    st.caption("💡 修正するには下の候補から選び直し or 手動URL入力で上書きできます")

            # 同類グループ検出 (同じ商品No + 同じベース名)
            same_base_group = [
                x for x in all_items
                if str(x['base_no']) == str(item['base_no']) and
                   extract_multiplier_and_base(x['card_name'])[1] == cur_base and
                   x['rarity'] == item['rarity'] and
                   _item_key(x) != _item_key(item) and
                   not x.get('db_done') and
                   _item_key(x) not in st.session_state['_match_done_local']
            ]
            if same_base_group:
                with st.expander(f"📚 同類グループ {len(same_base_group)}件: 数違いの同じカード(同じスニダンURLで一括採用可)", expanded=True):
                    for g in same_base_group:
                        gm, _ = extract_multiplier_and_base(g['card_name'])
                        st.caption(f"  ↳ {g['card_name']} (×{gm}) / 賞:{g['tier']} / 数量:{g['qty']}")
                    st.caption("→ 下の「候補N採用」or「手動URL採用」を押すと **この商品の同類カード全て** に同じURLが登録されます")

            # 画像並列表示 (画像URLは都度取得・キャッシュ済み)
            st.markdown("---")
            img_cols = st.columns(4)
            with img_cols[0]:
                st.markdown("##### 🎴 トレカ画像")
                tc_img_url = item.get('tc_image_url') or _get_tc_image(item['base_url'], item['card_name'], item['rarity'])
                if tc_img_url:
                    st.image(tc_img_url, use_column_width=True)
                    st.caption("これが正解")
                else:
                    st.warning("画像なし")

            for j, c in enumerate(item['cands'][:3]):
                with img_cols[j + 1]:
                    sim_label = f"類似度 {c['sim']:.3f}" if c['sim'] > 0 else "類似度不明"
                    st.markdown(f"##### 候補{j+1} ({sim_label})")
                    snk_img = c.get('img_url') or _get_snk_image(c.get('url', ''))
                    if snk_img:
                        st.image(snk_img, use_column_width=True)
                    else:
                        st.warning("画像なし")
                    st.caption(c['name'][:50])
                    btn_cols = st.columns([3, 1])
                    with btn_cols[0]:
                        if st.button(f"✅ 候補{j+1}を採用", key=f"match_adopt_{j}_{item['no']}_{idx}", type="primary", use_container_width=True,
                                       disabled=not st.session_state.get('_worker_name')):
                            with st.spinner("価格取得+DB保存中..."):
                                price, msg = _fetch_price_for_url(c['url'], item['card_name'], item['rarity'])
                                _save_card_match(item['base_no'], item['card_name'], item['rarity'],
                                                 item['tier'], item['qty'], c['url'], price,
                                                 f"候補{j+1} sim={c['sim']:.2f} {msg[:20]}",
                                                 status='confirmed_by_worker')
                                st.session_state['_match_done_local'].add(_item_key(item))
                                for g in same_base_group:
                                    _save_card_match(g['base_no'], g['card_name'], g['rarity'],
                                                     g['tier'], g['qty'], c['url'], price,
                                                     f"同類一括 sim={c['sim']:.2f}",
                                                     status='confirmed_by_worker')
                                    st.session_state['_match_done_local'].add(_item_key(g))
                            extra = f" + 同類{len(same_base_group)}件" if same_base_group else ""
                            st.success(f"候補{j+1}を採用 (パック単価¥{price:,}{extra})")
                            # 採用後 filteredが縮むので idx は維持 → 同じ位置に次の未対応が来る
                            # 安全に min(idx, max_possible) でクランプ
                            st.session_state['_match_idx'] = max(0, min(idx, len(filtered) - 1))
                            st.rerun()
                    with btn_cols[1]:
                        if c['url']:
                            st.link_button("🔗", c['url'], help="スニダンページを開く")

            # 下段: 手動URL入力 / 要確認 / スキップ
            st.markdown("---")
            st.caption("💡 URLを貼り付けて「📝 手動採用」を押してください (Enter不要) ／ わからない場合は「⏸ 要確認」で保留→後で一覧確認")
            manual_cols = st.columns([4, 1, 1, 1])
            manual_key = f"match_manual_{item['no']}_{idx}"
            with manual_cols[0]:
                st.text_input(
                    "📝 手動URL入力 (上記候補に正解がない場合、スニダンURLを貼り付け)",
                    key=manual_key,
                    placeholder="https://snkrdunk.com/apparels/..."
                )
            with manual_cols[1]:
                if st.button("📝 手動採用", key=f"match_manual_btn_{item['no']}_{idx}",
                              disabled=not st.session_state.get('_worker_name')):
                    # 押下時に session_state から最新値を取得
                    url = (st.session_state.get(manual_key, '') or '').strip()
                    if not url:
                        st.warning("URLが空です。スニダンURLを貼り付けてください")
                    elif not url.startswith('http'):
                        st.warning("URLが不正です(https://snkrdunk.com/apparels/... の形式で入力)")
                    else:
                        with st.spinner("価格取得中..."):
                            price, msg = _fetch_price_for_url(url, item['card_name'], item['rarity'])
                        if price <= 0:
                            # 価格0は保存せずエラー → ユーザーにURL再確認を促す
                            st.error(
                                f"❌ 価格取得に失敗しました (¥0)。**保存していません**。\n\n"
                                f"理由: {msg[:80]}\n\n"
                                f"考えられる原因:\n"
                                f"・URLが間違っている (別カードのページ)\n"
                                f"・スニダンに販売履歴がない (極めて稀)\n"
                                f"・スニダン側のAPIエラー(時間を置いて再試行)\n\n"
                                f"対処: URLを再確認して貼り直してください。"
                                f"もし本当に値段がつかないカード(ハズレ枠相当)なら「❌除外」を押してください"
                            )
                        else:
                            with st.spinner("DB保存中..."):
                                _save_card_match(item['base_no'], item['card_name'], item['rarity'],
                                                 item['tier'], item['qty'], url, price,
                                                 f"手動URL {msg[:30]}",
                                                 status='confirmed_by_worker')
                                st.session_state['_match_done_local'].add(_item_key(item))
                                for g in same_base_group:
                                    _save_card_match(g['base_no'], g['card_name'], g['rarity'],
                                                     g['tier'], g['qty'], url, price,
                                                     f"手動URL(同類一括)",
                                                     status='confirmed_by_worker')
                                    st.session_state['_match_done_local'].add(_item_key(g))
                            extra = f" + 同類{len(same_base_group)}件" if same_base_group else ""
                            st.success(f"✅ 手動URLを採用 (パック単価¥{price:,}{extra})")
                            st.session_state.pop(manual_key, None)
                            st.session_state['_match_idx'] = max(0, min(idx, len(filtered) - 1))
                            st.rerun()
            with manual_cols[2]:
                if st.button("⏸ 要確認", key=f"match_review_{item['no']}_{idx}", use_container_width=True,
                              help="保留フラグ。後で「⏸要確認のみ」モードで一覧確認・対応",
                              disabled=not st.session_state.get('_worker_name')):
                    _save_card_match(item['base_no'], item['card_name'], item['rarity'],
                                     item['tier'], item['qty'], '', 0,
                                     '要確認(後で対応)',
                                     status='provisional_review')
                    st.success("⏸ 要確認として保留(後で一覧確認可能)")
                    st.session_state['_match_idx'] = min(idx + 1, len(filtered) - 1)
                    st.rerun()
            with manual_cols[3]:
                if st.button("⏭ スキップ", key=f"match_skip_{item['no']}_{idx}", use_container_width=True,
                              help="今は飛ばす。次回起動時にまた未対応として表示される"):
                    st.session_state['_match_idx'] = min(idx + 1, len(filtered) - 1)
                    st.rerun()


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
        tc_min_price = st.number_input("単価下限(コイン)", min_value=0, value=0, step=100, key="tc_min_price")
    with f_cols[2]:
        tc_max_price = st.number_input("単価上限(コイン)", min_value=0, value=0, step=100,
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

    pg_col_stock, pg_col_info = st.columns([1, 3])
    with pg_col_stock:
        pg_stock_label = st.radio(
            "在庫モード",
            options=["在庫連動", "無在庫"], horizontal=True,
            label_visibility="collapsed",
            key="pg_stock_mode",
        )
    pg_stock_mode = "no_stock" if pg_stock_label == "無在庫" else "linked"
    with pg_col_info:
        if pg_stock_mode == "no_stock":
            st.warning("🛒 無在庫モード: 全カード選択可・在庫スプシ非更新")
        else:
            st.info("📦 在庫連動モード: 残数量から選択・引当する")

    st.markdown("### ① 販売パラメータ")
    pc1, pc2, pc3, pc4 = st.columns(4)
    with pc1:
        pg_title = st.text_input("商品タイトル", value="新規限定ガチャ", key="pg_title")
    with pc2:
        pg_total = st.number_input("総口数", min_value=1, value=8000, step=100, key="pg_total")
    with pc3:
        pg_price = st.number_input("1回コイン消費（pt）", min_value=1, value=2800, step=100, key="pg_price")
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
        pg_base_markup = st.number_input(
            "商品全体ベース上乗せ率（%）",
            min_value=-1.0, max_value=200.0, step=5.0,
            value=float(st.session_state.get("pg_base_markup_input", 30.0)),
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
        tmarkup = cc[3].number_input(f"markup_{i}", min_value=-1.0, max_value=200.0, value=float(default[3]), step=1.0, label_visibility="collapsed", key=f"pg_tmarkup_{i}")
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
            result_pg = design_premium(spec_pg, reference=pg_selected_ref)
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
                   help="顧客が見る還元率（コイン額面ベース、ポイント還元含む）")
        rc5.metric("実還元率", f"{result_pg.real_return_rate:.1%}",
                   help="運営の本当の還元率（カード仕入れ + ポイント実コスト）")
        rc6.metric("コイン上乗せ差分",
                   f"{(result_pg.customer_return_rate - result_pg.real_return_rate)*100:+.1f}pt")

        with st.expander("📊 詳細内訳"):
            st.markdown(f"""
| 項目 | 金額 |
|---|---|
| 売上 | ¥{result_pg.total_revenue:,} |
| カード相場合計 | ¥{result_pg.total_card_market:,} |
| カード仕入れ合計 | ¥{result_pg.total_card_cost:,} |
| ポイント還元（額面） | ¥{result_pg.total_point_value:,} |
| ポイント還元（実コスト × {live_pg_spec.point_real_cost_rate:.0%}） | ¥{result_pg.total_point_real_cost:,} |
| コイン額面合計（顧客視点） | ¥{result_pg.total_coin_value:,} |
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


# ---------- 上乗せ率設定タブ ----------
with tab_markup:
    st.subheader("⚙️ 上乗せ率設定（コインの上乗せ）")
    st.markdown("""
顧客にはコインで価格を表示します。**カード相場 × (1 + 上乗せ率)** がコイン額面（顧客が見る金額）になります。

例: 相場 ¥100,000 のカードに 20% 上乗せ → コイン額面 120,000コイン（=120,000円換算）として表示
""")
    from markup import load_markup_bands, clear_cache as clear_markup_cache
    bands = load_markup_bands(force=True)

    df_bands = pd.DataFrame([
        {"価格下限": b.lower, "価格上限": b.upper, "上乗せ率（%）": b.rate_pct}
        for b in bands
    ])
    edited = st.data_editor(
        df_bands, num_rows="dynamic", use_container_width=True, hide_index=True,
        column_config={
            "価格下限": st.column_config.NumberColumn(format="%d", min_value=0),
            "価格上限": st.column_config.NumberColumn(format="%d", min_value=0),
            "上乗せ率（%）": st.column_config.NumberColumn(format="%.1f", min_value=0, max_value=100),
        },
    )

    save_col, _ = st.columns([1, 4])
    with save_col:
        if st.button("💾 設定を保存", type="primary", use_container_width=True):
            inv = open_inventory()
            ws = inv.worksheet(config.TAB_MARKUP)
            new_rows = []
            for _, row in edited.iterrows():
                if pd.isna(row["価格下限"]) or pd.isna(row["価格上限"]) or pd.isna(row["上乗せ率（%）"]):
                    continue
                new_rows.append([int(row["価格下限"]), int(row["価格上限"]), float(row["上乗せ率（%）"]), ""])
            ws.clear()
            ws.update([config.MARKUP_HEADERS] + new_rows, "A1", value_input_option="USER_ENTERED")
            clear_markup_cache()
            st.success("✅ 保存しました")
            st.rerun()

    st.markdown("---")
    st.markdown("### 動作確認")
    test_price = st.number_input("テスト用相場（円）", min_value=0, value=50000, step=1000)
    from markup import find_markup_rate, coin_price_for
    rate = find_markup_rate(int(test_price), bands)
    coin = coin_price_for(int(test_price), bands)
    st.info(f"相場 ¥{int(test_price):,} → 上乗せ {rate}% → コイン額面 **{coin:,} コイン**（=¥{coin:,}換算）")

    st.markdown("---")
    st.subheader("📋 上乗せ率プリセット")
    st.markdown("""
商品ごとに使い回せる上乗せ率パターンを保存します。商品設計画面の「プリセット適用」から呼び出せます。

| 列 | 意味 |
|---|---|
| ベース上乗せ率（%） | 商品全体に適用される倍率（-1なら価格帯別ルール） |
| 各等の列（1等/2等/.../S賞/...） | 等別の上書き値（-1ならベース倍率を使う、0以上ならその値で上書き） |
""")
    from markup import load_presets, save_preset, MarkupPreset, clear_cache as _mclear
    presets_ui = load_presets(force=True)
    df_presets = pd.DataFrame([
        {
            "プリセット名": p.name,
            "ベース上乗せ率（%）": p.base_rate,
            **p.tier_rates,
            "備考": p.note,
        } for p in presets_ui
    ])
    edited_ps = st.data_editor(
        df_presets,
        num_rows="dynamic",
        use_container_width=True,
        hide_index=True,
        key="preset_editor",
    )

    if st.button("💾 プリセットを保存", type="primary", key="save_presets"):
        # 全行を書き戻し
        inv = open_inventory()
        ws_p = inv.worksheet(config.TAB_MARKUP_PRESETS)
        headers_p = ws_p.row_values(1) or config.PRESET_HEADERS
        rows = []
        for _, row in edited_ps.iterrows():
            if pd.isna(row.get("プリセット名")) or not str(row.get("プリセット名", "")).strip():
                continue
            r = []
            for h in headers_p:
                v = row.get(h, "")
                if pd.isna(v) or v == "":
                    r.append(-1 if h not in ("プリセット名", "ベース上乗せ率（%）", "備考") else "")
                else:
                    r.append(v)
            rows.append(r)
        ws_p.clear()
        ws_p.update([headers_p] + rows, "A1", value_input_option="USER_ENTERED")
        _mclear()
        st.success("✅ プリセットを保存しました")
        st.rerun()


# ---------- 在庫タブ ----------
with tab_inventory:
    st.subheader("📦 在庫一覧と相場更新")

    btn_cols = st.columns([1, 1, 4])
    with btn_cols[0]:
        if st.button("🔄 再読込", key="reload_inv", use_container_width=True):
            st.cache_data.clear()
            st.rerun()
    with btn_cols[1]:
        bulk_update = st.button("📈 全在庫の相場を一括更新", type="primary", use_container_width=True,
                                help="snkrdunk URL がある全カードの相場を取得して更新します（1〜数分かかる場合あり）")

    if bulk_update:
        from snkrdunk_client import fetch_recent_price
        from inventory import update_market_price as _update_price
        items_for_update = [it for it in load_all_inventory() if it.snkrdunk_url]
        if not items_for_update:
            st.warning("snkrdunk URL付きの在庫がありません")
        else:
            progress = st.progress(0)
            status = st.empty()
            ok = 0
            fail = 0
            for i, it in enumerate(items_for_update):
                status.text(f"[{i+1}/{len(items_for_update)}] {it.name} を取得中...")
                price, msg = fetch_recent_price(it.snkrdunk_url, grade=it.grade)
                if price and price > 0:
                    try:
                        _update_price(it.tab, it.row_idx, price, note=msg.split("／")[0][:30])
                        ok += 1
                    except Exception as e:
                        fail += 1
                else:
                    fail += 1
                progress.progress((i + 1) / len(items_for_update))
            status.empty()
            progress.empty()
            st.success(f"✅ 完了: 成功 {ok}件 / 失敗 {fail}件")
            st.cache_data.clear()
            st.rerun()

    @st.cache_data(ttl=30)
    def load_inv_df():
        items = load_all_inventory()
        return items

    items = load_inv_df()
    df = pd.DataFrame([
        {
            "区分": it.tab, "カード名": it.name, "シリーズ": it.series,
            "グレード": it.grade,
            "数量": it.qty, "予約中": it.reserved_qty, "販売中": it.on_sale_qty,
            "残数量": it.remaining_qty,
            "相場": it.price, "仕入れ価格": it.purchase_price,
            "相場更新": it.price_updated or "-",
            "snk URL": it.snkrdunk_url,
            "引当先": it.allocation_product or "",
            "row_idx": it.row_idx,
        } for it in items
    ])

    fc1, fc2, fc3, fc4 = st.columns(4)
    with fc1:
        f_tab = st.multiselect("区分", options=["PSA10", "BOX"], default=["PSA10", "BOX"])
    with fc2:
        only_available = st.checkbox("残数量あり", value=True)
    with fc3:
        only_no_purchase = st.checkbox("仕入れ価格未入力のみ", value=False)
    with fc4:
        f_search = st.text_input("カード名で絞込")

    df_show = df[df["区分"].isin(f_tab)]
    if only_available:
        df_show = df_show[df_show["残数量"] > 0]
    if only_no_purchase:
        df_show = df_show[df_show["仕入れ価格"] == 0]
    if f_search.strip():
        df_show = df_show[df_show["カード名"].str.contains(f_search.strip(), na=False)]

    st.caption(f"{len(df_show)}件")
    st.dataframe(
        df_show.drop(columns=["row_idx"]),
        use_container_width=True, hide_index=True,
    )

    st.markdown("---")
    st.markdown("### 個別操作（相場更新・仕入れ価格入力）")
    st.caption("カード名で検索してから1つ選んで、相場をsnkrdunkから取得 or 仕入れ価格を入力できます")

    if len(df_show) > 0:
        op_search = st.text_input(
            "🔍 カード名で検索",
            placeholder="例: リーリエ / ロイヤル / ピカチュウ",
            key="op_search",
        )
        df_op = df_show
        if op_search.strip():
            df_op = df_op[df_op["カード名"].str.contains(op_search.strip(), na=False)]

        if len(df_op) == 0:
            st.warning("該当するカードがありません")
        else:
            st.caption(f"候補: {len(df_op)}件")
            sel_idx_label = st.selectbox(
                "カードを選択",
                options=df_op.index.tolist(),
                format_func=lambda i: (
                    f"[{df_op.loc[i, '区分']}] {df_op.loc[i, 'カード名']} "
                    f"({str(df_op.loc[i, 'グレード']) or '-'}, "
                    f"相場¥{int(df_op.loc[i, '相場'] or 0):,}, 仕入¥{int(df_op.loc[i, '仕入れ価格'] or 0):,})"
                ),
            )
            sel = df_op.loc[sel_idx_label]
            sel_url = str(sel["snk URL"]) if pd.notna(sel["snk URL"]) else ""
            sel_grade = str(sel["グレード"]) if pd.notna(sel["グレード"]) else ""

            op_cols = st.columns([2, 2, 2])
            with op_cols[0]:
                if st.button(
                    "📈 このカードの相場をsnkrdunkから更新",
                    disabled=not sel_url,
                    use_container_width=True,
                ):
                    from snkrdunk_client import fetch_recent_price
                    from inventory import update_market_price as _update_price
                    with st.spinner("取得中..."):
                        price, msg = fetch_recent_price(sel_url, grade=sel_grade)
                    if price:
                        _update_price(str(sel["区分"]), int(sel["row_idx"]), price, note=msg.split("／")[0][:30])
                        st.success(f"✅ 相場 ¥{int(sel['相場']):,} → ¥{price:,} に更新（{msg}）")
                        st.cache_data.clear()
                        st.rerun()
                    else:
                        st.error(f"取得失敗: {msg}")
            with op_cols[1]:
                new_purchase = st.number_input(
                    "仕入れ価格（円）", min_value=0,
                    value=int(sel["仕入れ価格"] or 0), step=1000,
                    key=f"pp_{sel_idx_label}",
                )
            with op_cols[2]:
                if st.button("💾 仕入れ価格を保存", use_container_width=True):
                    from inventory import update_purchase_price
                    update_purchase_price(str(sel["区分"]), int(sel["row_idx"]), int(new_purchase))
                    st.success(f"✅ 仕入れ価格 ¥{int(new_purchase):,} を保存")
                    st.cache_data.clear()
                    st.rerun()
