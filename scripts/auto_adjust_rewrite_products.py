"""リライト商品100件の利益率を自動調整して全件を45-50%範囲に収める

方針:
- 上位(1等+2等)は競合と同じ大当たり維持
- 下位(3等+4等+5等)を圧縮率で調整して目標仕入まで削減 or 増量
- 圧縮率で届かない場合は設計単価を追加調整 (上乗せ率も更新)

目標利益率: 47.5% (=45-50%の中央値)

実行: python3 scripts/auto_adjust_rewrite_products.py [--dry-run]
"""
from __future__ import annotations
import sys, os, argparse
from collections import defaultdict
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from research import open_research
from sheets_client import get_client
import config


TARGET_PROFIT_MIN = 45.0
TARGET_PROFIT_MAX = 50.0
TARGET_PROFIT_MID = 47.5
UPPER_TIERS = {'1等', '2等'}
LOWER_TIERS = {'3等', '4等', '5等'}


def parse_int(v) -> int:
    if v is None or v == '': return 0
    s = str(v).replace(',', '').replace('¥', '').replace('%', '').strip()
    try: return int(float(s))
    except: return 0


def parse_float(v) -> float:
    if v is None or v == '': return 0.0
    s = str(v).replace(',', '').replace('¥', '').replace('%', '').strip()
    try: return float(s)
    except: return 0.0


def classify(profit_rate: float) -> tuple[str, str]:
    if profit_rate >= 50: return '✅ 高利益', 'high_profit'
    if 45 <= profit_rate < 50: return '✅ 達成', 'achieved'
    if 40 <= profit_rate < 45: return '⚠️ 近い', 'near'
    if profit_rate < 0: return '❌ 赤字', 'failed'
    return '⚠️ 低利益', 'low'


def adjust_one(base_no: str, cards: list, design_unit: int, total_tickets: int, orig_markup: float):
    """1件の再設計。戻り値: (new_unit, new_tickets, new_markup, new_cost, new_profit, lower_ratio, note)"""
    upper_cost = sum(c['qty'] * c['price'] for c in cards if c['tier'] in UPPER_TIERS)
    lower_cost = sum(c['qty'] * c['price'] for c in cards if c['tier'] in LOWER_TIERS)

    # フェーズ1: 総売上固定、下位圧縮率で調整
    sales = design_unit * total_tickets
    if sales <= 0:
        return design_unit, total_tickets, orig_markup, 0, 0, 1.0, 'sales=0 スキップ'

    target_cost = sales * (1 - TARGET_PROFIT_MID / 100)
    target_lower = target_cost - upper_cost

    # 下位圧縮率 (0.05〜3.0)
    if lower_cost > 0:
        raw_ratio = target_lower / lower_cost
    else:
        raw_ratio = 1.0
    lower_ratio = max(0.05, min(3.0, raw_ratio))
    new_cost_phase1 = upper_cost + lower_cost * lower_ratio
    new_profit_phase1 = (sales - new_cost_phase1) / sales * 100

    if TARGET_PROFIT_MIN <= new_profit_phase1 <= TARGET_PROFIT_MAX:
        # フェーズ1で達成
        return design_unit, total_tickets, orig_markup, int(new_cost_phase1), new_profit_phase1, lower_ratio, f'下位圧縮のみで達成 (率={lower_ratio:.2f})'

    # フェーズ2: 圧縮率頭打ち → 設計単価スケール
    # 目標総売上を上方修正
    #   利益 = 新総売上 - 新実仕入
    #   新実仕入 = 上位 + 下位×ratio (ratioは0.05 or 3.0 で頭打ち)
    #   47.5% = (新総売上 - 新実仕入) / 新総売上
    #   新総売上 = 新実仕入 / 0.525
    new_cost_final = upper_cost + lower_cost * lower_ratio
    target_sales = new_cost_final / 0.525
    # 単価を上げるか、総口数を上げるか
    # 単価優先(coinの整数倍で調整)、総口数変えない
    if total_tickets > 0:
        new_unit = max(1, round(target_sales / total_tickets))
    else:
        new_unit = design_unit
    new_sales = new_unit * total_tickets
    new_profit = (new_sales - new_cost_final) / new_sales * 100 if new_sales > 0 else 0

    # 単価変化率で上乗せ率も比例更新
    scale = new_unit / design_unit if design_unit > 0 else 1.0
    new_markup = round(orig_markup * scale, 2) if orig_markup > 0 else scale

    return new_unit, total_tickets, new_markup, int(new_cost_final), new_profit, lower_ratio, \
        f'圧縮率{lower_ratio:.2f}+単価{design_unit}→{new_unit}({scale:.2f}x)'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    print('★ カードマスタDB 読込中...')
    ss_r = open_research()
    ws_master = ss_r.worksheet('商品別カードマスタ')
    master_rows = ws_master.get_all_values()
    by_base = defaultdict(list)
    for r in master_rows[1:]:
        if len(r) < 12: continue
        bn = (r[0] or '').strip()
        if not bn: continue
        by_base[bn].append({
            'name': r[2], 'rarity': r[3], 'tier': r[4],
            'qty': parse_int(r[5]), 'price': parse_int(r[7]),
        })

    print('★ 在庫スプシ 読込中...')
    ss_i = get_client().open_by_key(config.get_active_inventory_sheet_id())
    ws_rw = ss_i.worksheet(config.TAB_REWRITE_CANDIDATES)
    ws_rep = ss_i.worksheet('100件設計レポート')
    rw_rows = ws_rw.get_all_values()
    rep_rows = ws_rep.get_all_values()
    rw_h = {c: i for i, c in enumerate(rw_rows[0])}
    rep_h = {c: i for i, c in enumerate(rep_rows[0])}
    print(f'  リライト商品案 {len(rw_rows)-1}件 / 100件設計レポート {len(rep_rows)-1}件')

    updates_rep = []
    updates_rw = []
    summary = {'achieved': 0, 'high_profit': 0, 'near': 0, 'failed': 0, 'low': 0}
    changed_cnt = 0
    samples = []

    for i, r in enumerate(rep_rows[1:], start=2):
        if len(r) < 3: continue
        rewrite_no = (r[rep_h.get('リライトNo', 0)] or '').strip()
        base_no = (r[rep_h.get('ベースNo', 1)] or '').strip()
        total_tickets = parse_int(r[rep_h.get('総口数', 5)])
        design_unit = parse_int(r[rep_h.get('設計単価(coin)', 4)])
        orig_markup = parse_float(r[rep_h.get('上乗せ率', 10)])
        cards = by_base.get(base_no, [])
        if not cards or not total_tickets or not design_unit: continue

        new_unit, new_tickets, new_markup, new_cost, new_profit, lower_ratio, note = \
            adjust_one(base_no, cards, design_unit, total_tickets, orig_markup)
        new_sales = new_unit * new_tickets
        new_return = (new_cost / new_sales * 100) if new_sales > 0 else 0
        status_label, status_key = classify(new_profit)
        summary[status_key] = summary.get(status_key, 0) + 1

        if (new_unit != design_unit) or (new_markup != orig_markup):
            changed_cnt += 1

        # レポート更新: C=ステータス, D=判定, E=設計単価, G=売上, H=実仕入, I=実利益率, J=顧客還元率, K=上乗せ率
        updates_rep.append({'range': f'C{i}', 'values': [[status_label]]})
        updates_rep.append({'range': f'D{i}', 'values': [['達成' if status_key in ('achieved', 'high_profit') else '要調整']]})
        updates_rep.append({'range': f'E{i}', 'values': [[new_unit]]})
        updates_rep.append({'range': f'G{i}', 'values': [[new_sales]]})
        updates_rep.append({'range': f'H{i}', 'values': [[new_cost]]})
        updates_rep.append({'range': f'I{i}', 'values': [[f'{new_profit:.2f}%']]})
        updates_rep.append({'range': f'J{i}', 'values': [[f'{new_return:.2f}%']]})
        updates_rep.append({'range': f'K{i}', 'values': [[round(new_markup, 2)]]})

        if len(samples) < 8:
            samples.append((rewrite_no, base_no, design_unit, new_unit, new_cost, new_profit, status_label, note))

    # リライト商品案側 (Column: N=設計単価, O=売上, P=実仕入, Q=実利益率, R=上乗せ率, S=調整ステータス)
    for i, r in enumerate(rw_rows[1:], start=2):
        base_no = (r[rw_h.get('ベースNo', 2)] or '').strip()
        total_tickets = parse_int(r[rw_h.get('総口数', 5)])
        design_unit = parse_int(r[rw_h.get('設計単価(coin)', 13)])
        orig_markup = parse_float(r[rw_h.get('上乗せ率', 17)])
        cards = by_base.get(base_no, [])
        if not cards or not total_tickets or not design_unit: continue
        new_unit, new_tickets, new_markup, new_cost, new_profit, _, _ = \
            adjust_one(base_no, cards, design_unit, total_tickets, orig_markup)
        new_sales = new_unit * new_tickets
        status_label, status_key = classify(new_profit)
        updates_rw.append({'range': f'N{i}', 'values': [[new_unit]]})
        updates_rw.append({'range': f'O{i}', 'values': [[new_sales]]})
        updates_rw.append({'range': f'P{i}', 'values': [[new_cost]]})
        updates_rw.append({'range': f'Q{i}', 'values': [[f'{new_profit:.2f}%']]})
        updates_rw.append({'range': f'R{i}', 'values': [[round(new_markup, 2)]]})
        updates_rw.append({'range': f'S{i}', 'values': [[status_key]]})

    print(f'\n=== 自動調整結果 ===')
    print(f'  ✅ 高利益(>50%): {summary.get("high_profit",0)}件')
    print(f'  ✅ 達成(45-50%): {summary.get("achieved",0)}件')
    print(f'  ⚠️ 近い(40-45%): {summary.get("near",0)}件')
    print(f'  ⚠️ 低利益(0-40%): {summary.get("low",0)}件')
    print(f'  ❌ 赤字(<0%): {summary.get("failed",0)}件')
    print(f'  🔄 単価/上乗せ調整: {changed_cnt}件')
    print(f'\n=== 調整サンプル8件 ===')
    for rw, bn, du, nu, cost, pr, st, note in samples:
        print(f'  リライト{rw} ベース{bn} | 単価 {du}→{nu} | 実仕入¥{cost:,} | {pr:.1f}% | {st} | {note}')

    print(f'\n★ 更新セル数: レポート={len(updates_rep)}, リライト案={len(updates_rw)}')

    if args.dry_run:
        print('  --dry-run 指定なのでDB書き込みスキップ')
        return

    import time
    def batch_write(ws, updates, tab_name):
        chunk = 500
        for start in range(0, len(updates), chunk):
            for attempt in range(6):
                try:
                    ws.batch_update(updates[start:start + chunk], value_input_option='USER_ENTERED')
                    break
                except Exception as ex:
                    if '429' in str(ex) or 'quota' in str(ex).lower():
                        wait = 20 * (attempt + 1)
                        print(f'  ⚠️ [{tab_name}] 429 → {wait}秒待機 (attempt {attempt+1}/6)')
                        time.sleep(wait)
                        continue
                    raise
            print(f'  [{tab_name}] batch {start}〜{start+chunk-1} 送信')
            time.sleep(1.5)

    print('\n★ 100件設計レポート更新中...')
    batch_write(ws_rep, updates_rep, 'レポート')
    print('\n★ リライト商品案更新中...')
    batch_write(ws_rw, updates_rw, 'リライト案')
    print('\n✅ 完了')


if __name__ == '__main__':
    main()
