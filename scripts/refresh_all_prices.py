"""商品別カードマスタDBの全ユニークURLに対して最新スニダン価格を取得しDBを更新

- 並列取得(ThreadPoolExecutor)で767件を高速化
- DBの買取価格(H列) と 更新日時(L列) を batch_update
- 更新前後のサマリを表示

実行: python3 scripts/refresh_all_prices.py [--dry-run]
"""
from __future__ import annotations
import sys, os, re, argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta

JST = timezone(timedelta(hours=9))

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from research import open_research, clear_per_product_card_cache
from snkrdunk_client import fetch_recent_price
import config
from sheets_client import open_inventory


# app.py の extract_multiplier_and_base と同ロジック(BOX/PACK区別なく単純×N)
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
    """スニダン価格は「1アイテム=1BOX or 1PACK」の価格を返す約束のため単純×N"""
    m = MULT_PAT.search(card_name or '')
    if not m:
        return 1
    return int(m.group(1))


def is_pack_or_box(name: str, rarity: str) -> bool:
    return bool(re.search(r'(パック|PACK|BOX|ボックス|箱)', (name or '') + (rarity or ''), re.IGNORECASE))


def fetch_for_row(row_meta):
    """代表 name/rarity で URL の価格を取得"""
    url, name, rarity = row_meta['url'], row_meta['name'], row_meta['rarity']
    is_pack = is_pack_or_box(name, rarity)
    grade = 'PSA10' if ('PSA' in (name + rarity).upper() and not is_pack) else ''
    try:
        price, msg = fetch_recent_price(url, grade, is_pack=is_pack, item_name=name)
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
    now = datetime.now(JST).strftime('%Y-%m-%d %H:%M:%S')
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
        # dry-run でも在庫タブの更新プレビューは表示
        try:
            refresh_inventory_tabs(new_prices, dry_run=True)
        except Exception as ex:
            print(f'⚠️ 在庫タブ dry-run 失敗: {ex}')
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

    # 最終一括更新スタンプ (Streamlit全体ページ表示用)
    # value_input_option='RAW' で文字列保持 (USER_ENTEREDだと "01:55:17"→"1:55:17"に変換されstrptime破綻)
    try:
        ws.batch_update([
            {'range': 'P1', 'values': [['最終一括更新日時']]},
            {'range': 'P2', 'values': [[now]]},
            {'range': 'P3', 'values': [[f'変更{changed_count}行 / 対象{len(per_url_meta)}URL']]},
        ], value_input_option='RAW')
        print(f'  📝 スタンプ書込 P1:P3 = {now} / 変更{changed_count}行')
    except Exception as ex:
        print(f'  ⚠️ スタンプ書込失敗: {ex}')

    print(f'\n✅ 完了: {changed_count}行更新')

    # ========== 在庫タブ(PSA10在庫登録/ボックス在庫記録)も同じ価格で更新 ==========
    try:
        refresh_inventory_tabs(new_prices, dry_run=args.dry_run)
    except Exception as ex:
        # マスタ更新は成功済みなので、在庫失敗でも cron 自体は成功扱い(warning)
        print(f'⚠️ 在庫タブ更新失敗(マスタは成功済み): {ex}')


def _cell(col_idx_0: int, row: int) -> str:
    """0-based col → A1 表記"""
    s = ''
    c = col_idx_0
    while True:
        s = chr(ord('A') + c % 26) + s
        c = c // 26 - 1
        if c < 0:
            break
    return f'{s}{row}'


def refresh_inventory_tabs(new_prices: dict, dry_run: bool = False):
    """在庫スプシ(PSA10在庫登録/ボックス在庫記録)の相場を最新価格で更新

    - new_prices: {url: price} 商品別カードマスタ側で取得済みの URL→価格
    - マスタに無い URL は追加取得(並列)
    """
    import time
    JST_ = timezone(timedelta(hours=9))
    now_jst = datetime.now(JST_).strftime('%Y-%m-%d %H:%M')

    print('\n★ 在庫タブ 相場更新 開始')
    inv = open_inventory()

    tab_info = []  # [(tab_label, tab_name, ws, headers, c_url, c_price, c_updated, rows)]
    extra_urls = set()

    for tab_label, tab_name in [('PSA10', config.TAB_PSA10), ('BOX', config.TAB_BOX)]:
        try:
            ws = inv.worksheet(tab_name)
        except Exception as ex:
            print(f'  ⚠️ {tab_name}: 開けず → スキップ ({ex})')
            continue
        headers = ws.row_values(1)
        try:
            c_url = headers.index('スニダン used URL')
        except ValueError:
            print(f'  {tab_name}: スニダン used URL 列が無い → スキップ')
            continue
        try:
            c_price = headers.index('相場（1枚）')
        except ValueError:
            print(f'  {tab_name}: 相場（1枚）列が無い → スキップ')
            continue
        c_updated = headers.index(config.COL_PRICE_UPDATED) if config.COL_PRICE_UPDATED in headers else -1
        rows_inv = ws.get_all_values()
        tab_info.append((tab_label, tab_name, ws, headers, c_url, c_price, c_updated, rows_inv))
        for r in rows_inv[1:]:
            u = ((r[c_url] if c_url < len(r) else '') or '').strip()
            if u.startswith('http') and u not in new_prices:
                extra_urls.add(u)

    if not tab_info:
        print('  在庫タブが1つも見つからず → 終了')
        return

    # マスタに無かった URL は追加取得(並列)
    extra_prices = {}
    if extra_urls:
        print(f'  ⏳ マスタに無い {len(extra_urls)} URL を追加取得...')
        def _fetch_extra(u):
            try:
                p, _msg = fetch_recent_price(u, '', is_pack=False)
                return u, int(p or 0)
            except Exception:
                return u, 0
        with ThreadPoolExecutor(max_workers=8) as ex:
            futs = {ex.submit(_fetch_extra, u): u for u in extra_urls}
            for fut in as_completed(futs):
                u, p = fut.result()
                extra_prices[u] = p
        ok_extra = sum(1 for p in extra_prices.values() if p > 0)
        print(f'  追加取得: 成功{ok_extra}件 / 失敗{len(extra_prices)-ok_extra}件')

    # タブ別に batch_update
    total_changed = 0
    for tab_label, tab_name, ws, headers, c_url, c_price, c_updated, rows_inv in tab_info:
        batch = []
        changed = 0
        for i, r in enumerate(rows_inv[1:], start=2):
            u = ((r[c_url] if c_url < len(r) else '') or '').strip()
            if not u.startswith('http'):
                continue
            new_price = new_prices.get(u) or extra_prices.get(u) or 0
            if new_price <= 0:
                continue
            try:
                old_price = int(((r[c_price] if c_price < len(r) else '0') or '0').replace(',', ''))
            except Exception:
                old_price = 0
            if new_price == old_price:
                continue
            batch.append({'range': _cell(c_price, i), 'values': [[new_price]]})
            if c_updated >= 0:
                batch.append({'range': _cell(c_updated, i), 'values': [[f'{now_jst}（cron自動）']]})
            changed += 1

        if not batch:
            print(f'  {tab_name}: 変更なし')
            continue

        if dry_run:
            print(f'  {tab_name}: [dry-run] {changed}行 更新予定')
            continue

        print(f'  {tab_name}: {changed}行 batch_update 実行中...')
        chunk_ = 500
        for start in range(0, len(batch), chunk_):
            for attempt in range(6):
                try:
                    ws.batch_update(batch[start:start + chunk_], value_input_option='USER_ENTERED')
                    break
                except Exception as ex:
                    if '429' in str(ex) or 'quota' in str(ex).lower():
                        wait = 20 * (attempt + 1)
                        print(f'  ⚠️ 429 quota → {wait}秒待機 (attempt {attempt+1}/6)')
                        time.sleep(wait)
                        continue
                    raise
            time.sleep(1.5)
        total_changed += changed

    print(f'✅ 在庫タブ更新完了: {total_changed}行')


if __name__ == '__main__':
    main()
