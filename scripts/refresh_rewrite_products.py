"""リライト商品100件の実仕入/実利益率/顧客還元率を最新カードマスタDBから再計算

- 100件設計レポート、リライト商品案 両方のタブを更新
- ステータス判定: achieved(45-50%), high_profit(>50%), near(40-45%), failed(<40%)

実行: python3 scripts/refresh_rewrite_products.py [--dry-run]
"""
from __future__ import annotations
import sys, os, argparse
from collections import defaultdict
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from research import open_research
from sheets_client import get_client
import config


def parse_int(v) -> int:
    if v is None or v == '':
        return 0
    s = str(v).replace(',', '').replace('¥', '').replace('%', '').strip()
    try:
        return int(float(s))
    except Exception:
        return 0


def parse_float(v) -> float:
    if v is None or v == '':
        return 0.0
    s = str(v).replace(',', '').replace('¥', '').replace('%', '').strip()
    try:
        return float(s)
    except Exception:
        return 0.0


def classify_status(profit_rate: float) -> tuple[str, str]:
    """(ステータス, 判定) を返す"""
    if profit_rate >= 50:
        return '✅ 高利益', 'high_profit'
    if 45 <= profit_rate < 50:
        return '✅ 達成', 'achieved'
    if 40 <= profit_rate < 45:
        return '⚠️ 近い', 'near'
    if profit_rate < 0:
        return '❌ 赤字', 'failed'
    return '⚠️ 低利益', 'near'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    print('★ 商品別カードマスタDB 読み込み中...')
    ss_r = open_research()
    ws_master = ss_r.worksheet('商品別カードマスタ')
    master_rows = ws_master.get_all_values()
    # 商品No → 賞ごとの (qty, price, name, rarity) リスト
    by_base = defaultdict(list)
    for r in master_rows[1:]:
        if len(r) < 12:
            continue
        base_no = (r[0] or '').strip()
        if not base_no:
            continue
        by_base[base_no].append({
            'name': r[2], 'rarity': r[3], 'tier': r[4],
            'qty': parse_int(r[5]),
            'price': parse_int(r[7]),
            'url': r[6],
        })
    print(f'  カードマスタ 商品Noユニーク: {len(by_base)}')

    print('★ 在庫スプシ「リライト商品案」+「100件設計レポート」読込中...')
    ss_i = get_client().open_by_key(config.get_active_inventory_sheet_id())
    ws_rw = ss_i.worksheet(config.TAB_REWRITE_CANDIDATES)
    ws_rep = ss_i.worksheet('100件設計レポート')
    rw_rows = ws_rw.get_all_values()
    rep_rows = ws_rep.get_all_values()
    print(f'  リライト商品案 {len(rw_rows)-1}件 / 100件設計レポート {len(rep_rows)-1}件')

    # リライト商品案のヘッダ
    rw_h = {c: i for i, c in enumerate(rw_rows[0])}
    rep_h = {c: i for i, c in enumerate(rep_rows[0])}

    # 100件設計レポートを主軸に再計算 (リライトNo, ベースNo, 総口数, 設計単価(coin), 上乗せ率)
    updates_rep = []  # {'range': ..., 'values': ...}
    updates_rw = []
    summary = {'achieved': 0, 'high_profit': 0, 'near': 0, 'failed': 0}

    detail_examples = []

    for i, r in enumerate(rep_rows[1:], start=2):
        if len(r) < 3:
            continue
        rewrite_no = (r[rep_h.get('リライトNo', 0)] or '').strip()
        base_no = (r[rep_h.get('ベースNo', 1)] or '').strip()
        total_tickets = parse_int(r[rep_h.get('総口数', 5)])
        design_unit = parse_int(r[rep_h.get('設計単価(coin)', 4)])
        markup = parse_float(r[rep_h.get('上乗せ率', 10)])
        if not base_no or not total_tickets or not design_unit:
            continue

        # ベース商品のカード全集計
        cards = by_base.get(base_no, [])
        if not cards:
            continue

        # 実仕入 = Σ(qty × price)
        actual_cost = sum(c['qty'] * c['price'] for c in cards)
        zero_cnt = sum(1 for c in cards if c['price'] == 0)

        # 売上 = 設計単価(coin) × 総口数
        sales = design_unit * total_tickets

        # 実利益率 = (売上 - 実仕入) / 売上 × 100
        profit_rate = ((sales - actual_cost) / sales * 100) if sales > 0 else 0
        return_rate = (actual_cost / sales * 100) if sales > 0 else 0
        status_label, status_key = classify_status(profit_rate)
        summary[status_key] = summary.get(status_key, 0) + 1

        # 100件設計レポート更新
        updates_rep.append({'range': f'C{i}', 'values': [[status_label]]})
        updates_rep.append({'range': f'D{i}', 'values': [[
            '達成' if status_key in ('achieved', 'high_profit') else '要調整'
        ]]})
        updates_rep.append({'range': f'G{i}', 'values': [[sales]]})
        updates_rep.append({'range': f'H{i}', 'values': [[actual_cost]]})
        updates_rep.append({'range': f'I{i}', 'values': [[f'{profit_rate:.2f}%']]})
        updates_rep.append({'range': f'J{i}', 'values': [[f'{return_rate:.2f}%']]})
        updates_rep.append({'range': f'L{i}', 'values': [[zero_cnt]]})
        updates_rep.append({'range': f'M{i}', 'values': [[len(cards)]]})

        # サンプル出力
        if len(detail_examples) < 5:
            detail_examples.append({
                'rw': rewrite_no, 'base': base_no,
                'unit': design_unit, 'tickets': total_tickets, 'sales': sales,
                'cost': actual_cost, 'profit': profit_rate, 'return': return_rate,
                'status': status_label, 'cards': len(cards), 'zero': zero_cnt,
            })

    print(f'\n=== 再計算結果 ===')
    print(f'  ✅ 高利益(>50%): {summary["high_profit"]}件')
    print(f'  ✅ 達成(45-50%): {summary["achieved"]}件')
    print(f'  ⚠️ 近い(40-45%): {summary["near"]}件')
    print(f'  ❌ 赤字(<0%): {summary["failed"]}件')
    print(f'\n=== サンプル5件 ===')
    for d in detail_examples:
        print(f'  リライト{d["rw"]} ベース{d["base"]} | 単価{d["unit"]}c×{d["tickets"]:,}口=売上¥{d["sales"]:,} | 実仕入¥{d["cost"]:,} | 利益{d["profit"]:.1f}% 還元{d["return"]:.1f}% | {d["status"]} ({d["cards"]}枚, 価格0:{d["zero"]})')

    # リライト商品案タブも実仕入(P列)/実利益率(Q列)/調整ステータス(S列)更新
    for i, r in enumerate(rw_rows[1:], start=2):
        no = (r[rw_h.get('No', 0)] or '').strip()
        base_no = (r[rw_h.get('ベースNo', 2)] or '').strip()
        total_tickets = parse_int(r[rw_h.get('総口数', 5)])
        design_unit = parse_int(r[rw_h.get('設計単価(coin)', 13)])
        if not base_no or not total_tickets or not design_unit:
            continue
        cards = by_base.get(base_no, [])
        if not cards:
            continue
        actual_cost = sum(c['qty'] * c['price'] for c in cards)
        sales = design_unit * total_tickets
        profit_rate = ((sales - actual_cost) / sales * 100) if sales > 0 else 0
        status_label, status_key = classify_status(profit_rate)
        # リライト商品案の各列: 設計売上(円)=O, 実仕入(円)=P, 実利益率=Q, 調整ステータス=S
        # (0-index: 14, 15, 16, 18)
        updates_rw.append({'range': f'O{i}', 'values': [[sales]]})
        updates_rw.append({'range': f'P{i}', 'values': [[actual_cost]]})
        updates_rw.append({'range': f'Q{i}', 'values': [[f'{profit_rate:.2f}%']]})
        updates_rw.append({'range': f'S{i}', 'values': [[status_key]]})

    print(f'\n★ 更新セル: レポート={len(updates_rep)}, リライト案={len(updates_rw)}')

    if args.dry_run:
        print('  --dry-run 指定なのでDB書き込みスキップ')
        return

    # Sheets batch_update (chunk+sleep+retry)
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
