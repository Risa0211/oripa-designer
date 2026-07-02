"""商品別カードマスタDBの全ユニークURLに対して最新スニダン価格を取得しDBを更新

- 並列取得(ThreadPoolExecutor)で767件を高速化
- DBの買取価格(H列) と 更新日時(L列) を batch_update
- 更新前後のサマリを表示

実行: python3 scripts/refresh_all_prices.py [--dry-run]
"""
from __future__ import annotations
import sys, os, re, argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from research import open_research, clear_per_product_card_cache
from snkrdunk_client import fetch_recent_price


# app.py の extract_multiplier_and_base と同ロジック(BOX=30換算)
_UNIT_NORM = {
    'パック': 'pack', 'pack': 'pack',
    'ボックス': 'box', 'box': 'box', '箱': 'box',
    'セット': 'set', 'set': 'set',
    '枚': 'mai', '個': 'ko',
}
MULT_PAT = re.compile(
    r'[(（]?\s*(\d+)\s*(PACK|パック|枚|個|セット|SET|set|BOX|ボックス|箱)\s*[)）]?',
    re.IGNORECASE,
)


def get_multiplier(card_name: str) -> int:
    m = MULT_PAT.search(card_name or '')
    if not m:
        return 1
    mult = int(m.group(1))
    unit_norm = _UNIT_NORM.get(m.group(2).lower(), m.group(2).lower())
    if unit_norm == 'box':
        mult *= 30
    return mult


def is_pack_or_box(name: str, rarity: str) -> bool:
    return bool(re.search(r'(パック|PACK|BOX|ボックス|箱)', (name or '') + (rarity or ''), re.IGNORECASE))


def fetch_for_row(row_meta):
    """代表 name/rarity で URL の価格を取得"""
    url, name, rarity = row_meta['url'], row_meta['name'], row_meta['rarity']
    is_pack = is_pack_or_box(name, rarity)
    grade = 'PSA10' if ('PSA' in (name + rarity).upper() and not is_pack) else ''
    try:
        price, msg = fetch_recent_price(url, grade, is_pack=is_pack)
        return url, int(price or 0), msg
    except Exception as ex:
        return url, 0, f'ERR:{str(ex)[:60]}'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true', help='DB書き込まず取得だけ')
    ap.add_argument('--workers', type=int, default=8)
    args = ap.parse_args()

    print('★ 商品別カードマスタ 読み込み中...')
    ss = open_research()
    ws = ss.worksheet('商品別カードマスタ')
    rows = ws.get_all_values()
    header = rows[0]
    print(f'  全レコード: {len(rows)-1}件')

    # ユニークURL → 代表 name/rarity
    per_url_meta = {}
    for r in rows[1:]:
        if len(r) < 12:
            continue
        u = (r[6] or '').strip()
        if not u.startswith('http'):
            continue
        if u not in per_url_meta:
            per_url_meta[u] = {'url': u, 'name': r[2], 'rarity': r[3]}
    print(f'  ユニークURL: {len(per_url_meta)}件')

    # 並列取得
    print(f'\n★ 並列取得 (workers={args.workers})...')
    new_prices = {}  # url → new_price
    fetch_msgs = {}
    completed = 0
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(fetch_for_row, m): m for m in per_url_meta.values()}
        for fut in as_completed(futs):
            url, price, msg = fut.result()
            new_prices[url] = price
            fetch_msgs[url] = msg
            completed += 1
            if completed % 50 == 0:
                print(f'  進捗: {completed}/{len(per_url_meta)}')
    print(f'  取得完了: {completed}件')

    # 取得失敗集計
    fail_urls = [u for u, p in new_prices.items() if p <= 0]
    print(f'  ✅ 成功: {len(new_prices) - len(fail_urls)}件 / ❌ 失敗: {len(fail_urls)}件')
    if fail_urls[:5]:
        print('  失敗例:')
        for u in fail_urls[:5]:
            m = per_url_meta[u]
            print(f'    {m["name"][:25]} ({m["rarity"]}) | {fetch_msgs[u][:80]}')

    # 変更行の集計 (multiplier 適用後の final_price で比較)
    updates_h = []  # (row_idx_1based, new_value) 買取価格
    updates_l = []  # 更新日時
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    price_source_map = {}
    changed_count = 0
    for i, r in enumerate(rows[1:], start=2):  # 2行目からデータ
        if len(r) < 12:
            continue
        u = (r[6] or '').strip()
        if not u.startswith('http'):
            continue
        new_unit = new_prices.get(u, 0)
        if new_unit <= 0:
            continue
        mult = get_multiplier(r[2] or '')
        new_final = new_unit * mult
        old_price = 0
        try:
            old_price = int((r[7] or '0').replace(',', ''))
        except Exception:
            pass
        if new_final == old_price:
            continue
        updates_h.append({'range': f'H{i}', 'values': [[new_final]]})
        # 価格取得元(I列) & 更新日時(L列)も更新
        note = f'一括再取得 {fetch_msgs.get(u,"")[:60]}'
        if mult > 1:
            note = f'{note} ×{mult}={new_final}'
        updates_l.append({'range': f'I{i}', 'values': [[note]]})
        updates_l.append({'range': f'L{i}', 'values': [[now]]})
        changed_count += 1

    print(f'\n★ 変更対象: {changed_count}行')

    if args.dry_run:
        print('  --dry-run 指定なのでDB書き込みスキップ')
        # 変更サンプル10件
        for i, upd in enumerate(updates_h[:10]):
            row_num = int(upd['range'][1:])
            r = rows[row_num - 1]
            print(f'  行{row_num}: 商品{r[0]} | {r[2][:25]} | {r[3]} | 旧¥{r[7]} → 新¥{upd["values"][0][0]}')
        return

    print(f'★ Sheets batch_update 実行中 ({len(updates_h) + len(updates_l)}セル)...')
    all_updates = updates_h + updates_l
    # 1リクエストにまとめられるだけまとめる (chunk大きく、sleep長め→APIクォータ順守)
    import time
    chunk = 500
    for start in range(0, len(all_updates), chunk):
        # 429 リトライ内蔵
        for attempt in range(6):
            try:
                ws.batch_update(all_updates[start:start + chunk], value_input_option='USER_ENTERED')
                break
            except Exception as ex:
                if '429' in str(ex) or 'quota' in str(ex).lower():
                    wait = 20 * (attempt + 1)  # 20,40,60,80,100,120秒
                    print(f'  ⚠️ 429 quota → {wait}秒待機して再試行 (attempt {attempt+1}/6)')
                    time.sleep(wait)
                    continue
                raise
        print(f'  batch {start}〜{start+chunk-1} 送信')
        time.sleep(1.5)  # ペースダウン

    clear_per_product_card_cache()
    print(f'\n✅ 完了: {changed_count}行更新')


if __name__ == '__main__':
    main()
