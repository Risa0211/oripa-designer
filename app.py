"""Streamlit UI — みんなのトレカ オリパ商品設計ツール"""
from __future__ import annotations

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


# ---------- サイドバー: 参考競合 ----------
with st.sidebar:
    st.header("① 参考競合を選ぶ")
    refs = load_all_references()

    # 簡易検索
    search = st.text_input("検索（タイトル・No.）", placeholder="例: おつきみ、1758")
    filtered = refs
    if search:
        s = search.strip().lower()
        filtered = [r for r in refs if s in r.title.lower() or s in r.no]
    if not filtered:
        st.warning("該当なし")
        st.stop()

    options = [f"No.{r.no}｜{r.title}（¥{r.price_per_coin}×{r.total_tickets:,}口）" for r in filtered[:500]]
    idx = st.selectbox("競合", range(len(options)), format_func=lambda i: options[i])
    selected_ref = filtered[idx]

    st.markdown("---")
    st.markdown(f"**{selected_ref.title}**")
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


# ---------- メインタブ ----------
tab_design, tab_premium, tab_products, tab_suggest, tab_inventory, tab_markup = st.tabs([
    "📝 新規設計", "🎰 限定ガチャ", "📋 商品一覧", "🔄 改善提案", "📦 在庫", "⚙️ 上乗せ率設定"
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
