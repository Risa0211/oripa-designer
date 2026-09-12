# -*- coding: utf-8 -*-
"""① スニダンの正典画像URLを取る。

入力 : {work}/rows.json   採用カードの一覧（設計側の道具が出す。各行に aid と name）
出力 : {work}/snkr.json

★商品ページはブラウザ側で描画されるため img タグが出ない。
  検索ページ /search?keywords= はサーバー側で描画されるので、そこからタイルを拾う。
★全件は取れない。取れなかったものは目視のあと手で調達する。
"""
import json
import re
import threading
import urllib.parse
import warnings
from concurrent.futures import ThreadPoolExecutor

warnings.filterwarnings('ignore')

import requests

import paths
from snkrdunk_client import fetch_apparel_meta

H = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)', 'Accept': 'text/html'}
TILE = re.compile(r'/apparels/(\d+)"(.{0,1200}?)src="(https://cdn\.snkrdunk\.com/upload[^"]+)"', re.S)


def search(q):
    try:
        html = requests.get('https://snkrdunk.com/search?keywords=' + urllib.parse.quote(q),
                            headers=H, timeout=30).text
    except requests.RequestException:
        return {}
    return {m.group(1): m.group(3).split('?')[0] for m in TILE.finditer(html)}


def main():
    args = paths.parse_args('スニダンの正典画像URLを取る')
    rows = json.load(open(paths.need(args.work, 'rows.json'), encoding='utf-8'))

    cards = {}
    for rs in rows.values():
        for r in rs:
            if r.get('aid'):
                cards.setdefault(str(r['aid']), r['name'])

    out, lock = {}, threading.Lock()

    def work(item):
        aid, design = item
        meta = fetch_apparel_meta(aid) or {}
        localized = meta.get('name') or design
        img = None
        base = re.sub(r'\s*\[[^\]]*\]\s*$', '', design)        # 型番を落とした名前
        for q in (base, re.sub(r'\s*[:：].*$', '', base), design):
            hit = search(q)
            if aid in hit:
                img = hit[aid]
                break
        with lock:
            out[aid] = dict(design=design, localized=localized, img=img)

    with ThreadPoolExecutor(max_workers=5) as ex:
        list(ex.map(work, sorted(cards.items())))

    dest = args.work / 'snkr.json'
    json.dump(out, open(dest, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)

    ng = [v['design'] for v in out.values() if not v['img']]
    print(f'取得 {len(out) - len(ng)}種 / 失敗 {len(ng)}種  → {dest}')
    for n in ng:
        print('  ★', n)
    if ng:
        print('\n★失敗したものは目視のあと手で調達します（HANDOFF_景品画像とCSV.md §4）。')


if __name__ == '__main__':
    main()
