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
tab_design, tab_products, tab_suggest, tab_inventory, tab_markup = st.tabs([
    "📝 新規設計", "📋 商品一覧", "🔄 改善提案", "📦 在庫", "⚙️ 上乗せ率設定"
])


with tab_design:
    st.subheader("② 販売パラメータ")
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

    st.subheader("③ モードを選ぶ")
    mode = st.radio(
        "入力方式",
        options=["X", "Y"],
        format_func=lambda x: "案X: 1枚あたり目標相場を直接指定" if x == "X" else "案Y: 還元率＋等級配分比率から自動計算",
        horizontal=True,
    )

    st.subheader("④ 等構成")
    # 参考競合の等を初期値にする
    default_tiers = list(selected_ref.tiers.keys()) or ["1等", "2等", "3等"]
    selected_tier_names = st.multiselect("含める等", options=TIER_COLS, default=default_tiers)

    tier_specs: list[TierSpec] = []
    if selected_tier_names:
        st.markdown("各等の設定")
        cols_header = st.columns([1, 1, 2, 2] if mode == "X" else [1, 1, 2, 2])
        cols_header[0].markdown("**等級**")
        cols_header[1].markdown("**当たり数**")
        if mode == "X":
            cols_header[2].markdown("**1枚あたり目標相場（円）**")
            cols_header[3].markdown("**参考**")
        else:
            cols_header[2].markdown("**原価配分比率（%）**")
            cols_header[3].markdown("**参考**")

        # デフォルト値: 参考競合のカード数と、等級ごとに階段状の目標相場
        default_prices = {"1等": 200000, "2等": 50000, "3等": 15000, "4等": 5000, "5等": 2000, "6等": 1000, "7等": 500, "キリ番": 10000, "ラストワン": 100000}
        default_ratios = {"1等": 25, "2等": 25, "3等": 20, "4等": 15, "5等": 8, "6等": 4, "7等": 2, "キリ番": 1, "ラストワン": 0}

        for tname in selected_tier_names:
            ref_text = selected_ref.tiers.get(tname, "")
            default_count = count_cards_in_tier(ref_text) or 1
            row = st.columns([1, 1, 2, 2])
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
                tier_specs.append(TierSpec(name=tname, count=cnt, target_price=price_each))
            else:
                ratio = row[2].number_input(
                    f"ratio_{tname}", min_value=0.0, max_value=100.0,
                    value=float(default_ratios.get(tname, 10)), step=1.0,
                    label_visibility="collapsed", key=f"ratio_{tname}",
                )
                tier_specs.append(TierSpec(name=tname, count=cnt, budget_ratio=ratio))
            row[3].caption(ref_text[:80] + ("…" if len(ref_text) > 80 else ""))

    # モードY の場合: 合計比率チェック
    if mode == "Y" and tier_specs:
        total_ratio = sum(t.budget_ratio for t in tier_specs)
        if abs(total_ratio - 100) > 0.01:
            st.warning(f"⚠ 配分比率合計: {total_ratio:.1f}%（100%になるよう調整推奨）")

    st.subheader("⑤ 商品情報")
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
                price, msg = fetch_recent_price(it.snkrdunk_url)
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
    st.caption("カードを1つ選んで、相場をsnkrdunkから取得 or 仕入れ価格を入力できます")

    if len(df_show) > 0:
        sel_idx = st.selectbox(
            "カードを選択", options=range(len(df_show)),
            format_func=lambda i: f"[{df_show.iloc[i]['区分']}] {df_show.iloc[i]['カード名']} (相場¥{df_show.iloc[i]['相場']:,}, 仕入¥{df_show.iloc[i]['仕入れ価格']:,})",
        )
        sel = df_show.iloc[sel_idx]
        op_cols = st.columns([2, 2, 2])
        with op_cols[0]:
            if st.button(
                "📈 このカードの相場をsnkrdunkから更新",
                disabled=not sel["snk URL"],
                use_container_width=True,
            ):
                from snkrdunk_client import fetch_recent_price
                from inventory import update_market_price as _update_price
                with st.spinner("取得中..."):
                    price, msg = fetch_recent_price(sel["snk URL"])
                if price:
                    _update_price(sel["区分"], int(sel["row_idx"]), price, note=msg.split("／")[0][:30])
                    st.success(f"✅ 相場 ¥{sel['相場']:,} → ¥{price:,} に更新（{msg}）")
                    st.cache_data.clear()
                    st.rerun()
                else:
                    st.error(f"取得失敗: {msg}")
        with op_cols[1]:
            new_purchase = st.number_input(
                "仕入れ価格（円）", min_value=0, value=int(sel["仕入れ価格"]), step=1000,
                key=f"pp_{sel_idx}",
            )
        with op_cols[2]:
            if st.button("💾 仕入れ価格を保存", use_container_width=True):
                from inventory import update_purchase_price
                update_purchase_price(sel["区分"], int(sel["row_idx"]), int(new_purchase))
                st.success(f"✅ 仕入れ価格 ¥{new_purchase:,} を保存")
                st.cache_data.clear()
                st.rerun()
