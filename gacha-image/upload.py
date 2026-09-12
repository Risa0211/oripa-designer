# -*- coding: utf-8 -*-
"""④ 採用画像を保管庫へアップロードし、台帳に追記する。

入力 : {work}/final.json の upload=True のもの
出力 : {work}/uploaded.json ＋ 保管庫（WordPress）＋ 台帳

ファイル名 : {型番のハイフン化}_minnanotoreca-{apparel ID}.webp
タイトル   : カード名（レアリティ）[型番]

★綴りは minnanotoreca … n2つ・末尾 c。ドメイン（minnano-toreka.com）は k なので混同しない。
★台帳は下の全ファイルに追記する。1つでも漏れると画像URLが404になる。
   gacha-csv-builder/master_db_added.csv ＋ thumb_map.csv
   （MINNATORECA_CSV_DIR があれば、そちらの同名ファイルにも）

まず --dry-run で対象と行き先を確かめてから、--go を付けて実行すること。
"""
import csv
import io
import json
import re
import time
import warnings

warnings.filterwarnings('ignore')

import requests
from PIL import Image

import paths
from imgmatch import trim

import wp_client as W

H = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                   'AppleWebKit/537.36 Chrome/128 Safari/537.36'}

RARITIES = ('SR', 'SEC', 'HR', 'RR', 'SSR', 'UR', 'MUR', 'SAR', 'AR', 'CHR', 'CSR', 'R', 'C', 'UC', 'P', 'L')


def compress_webp(data, max_w=800, quality=88):
    """保管庫へ上げる前に webp へ変換する。失敗したら元データをそのまま返す。"""
    try:
        im = Image.open(io.BytesIO(data))
        if im.mode in ('P', 'LA'):
            im = im.convert('RGBA')
        elif im.mode == 'CMYK':
            im = im.convert('RGB')
        if im.width > max_w:
            im = im.resize((max_w, round(im.height * max_w / im.width)), Image.LANCZOS)
        out = io.BytesIO()
        im.save(out, 'WEBP', quality=quality, method=6)
        return out.getvalue()
    except Exception:
        return data


def parts(design):
    """設計名から (カード名, 型番, レアリティ) を取り出す。"""
    m = re.search(r'\[([^\]]+)\]\s*$', design)
    ban = m.group(1).strip() if m else ''
    name = design[:m.start()].strip() if m else design
    mm = re.search(r'\s(' + '|'.join(RARITIES) + r')[\s-]', ' ' + name + ' ')
    return name, ban, (mm.group(1) if mm else '')


def slug(ban):
    return re.sub(r'[^0-9A-Za-z\-]', '-', ban.replace('/', '-').replace(' ', '-')).strip('-')


def medium_url(mid, user, pw):
    j = json.loads(W._req(f'{W.WP_BASE}/wp-json/wp/v2/media/{mid}?_fields=source_url,media_details',
                          headers={'Authorization': W.auth_header(user, pw)}).decode('utf-8', 'replace'))
    sizes = (j.get('media_details') or {}).get('sizes') or {}
    return (sizes.get('medium') or {}).get('source_url') or j['source_url']


def referer_for(url):
    if 'cardrush-op' in url:
        return 'https://www.cardrush-op.jp/'
    if 'cardrush-pokemon' in url:
        return 'https://www.cardrush-pokemon.jp/'
    return ''


def main():
    def extra(ap):
        ap.add_argument('--go', action='store_true', help='実際にアップする（付けないと空撃ち）')
        ap.add_argument('--label', default='', help='台帳の備考に入れる案件名')

    args = paths.parse_args('採用画像を保管庫へアップし、台帳に追記する', extra)
    work = args.work
    FINAL = json.load(open(paths.need(work, 'final.json'), encoding='utf-8'))
    todo = [(a, v) for a, v in FINAL.items() if v.get('upload')]

    added_paths = paths.append_targets('master_db_added.csv')
    thumb_paths = paths.append_targets('thumb_map.csv')

    print(f'アップ対象 {len(todo)}種')
    print('台帳の追記先:')
    for p in added_paths + thumb_paths:
        print('   ', p)
    if paths.EXTRA_CSV_DIR is None:
        print('  （外部の台帳ディレクトリは見つかりませんでした。リポジトリ内のみ更新します）')

    if not args.go:
        print('\n--- 空撃ち（--go を付けると実行します）---')
        for a, v in todo:
            name, ban, rar = parts(v['design'])
            print(f'  {slug(ban)}_minnanotoreca-{a}.webp  ← {v["src"]}  {v["design"][:40]}')
        return

    user, pw = paths.wp_credentials()
    done = {}
    for a, v in todo:
        name, ban, rar = parts(v['design'])
        try:
            raw = requests.get(v['url'], headers={**H, 'Referer': referer_for(v['url'])}, timeout=60).content
            if v['src'] == 'snkrdunk':
                im = trim(Image.open(io.BytesIO(raw)))
                buf = io.BytesIO()
                im.convert('RGB').save(buf, 'WEBP', quality=90, method=6)
                data = buf.getvalue()
            else:
                data = compress_webp(raw)
            fn = f'{slug(ban)}_minnanotoreca-{a}.webp'
            title = f'{name}（{rar}）[{ban}]' if rar else f'{name} [{ban}]'
            mid, url = W.upload_media(fn, data, title, user=user, app_pass=pw)
            th = medium_url(mid, user, pw)
            done[a] = dict(design=v['design'], name=name, ban=ban, rar=rar,
                           wp_id=mid, url=url, thumb=th, src=v['src'])
            print(f'  OK {mid:>6} {v["design"][:36]:<38} {url.split("/")[-1]}')
        except Exception as e:
            print(f'  ★NG {v["design"][:36]:<38} {type(e).__name__}: {str(e)[:90]}')
        time.sleep(0.3)

    rec = work / 'uploaded.json'
    prev = json.load(open(rec, encoding='utf-8')) if rec.exists() else {}
    json.dump({**prev, **done}, open(rec, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print(f'\nアップ完了 {len(done)}/{len(todo)}種  → {rec}')

    if not done:
        return
    note = args.label or work.name
    for path in added_paths:
        with open(path, 'a', encoding='utf-8-sig', newline='') as fp:
            csv.writer(fp).writerows(
                [[d['name'], d['rar'], d['ban'], d['url'], d['wp_id'], f'{note}（{d["src"]}）']
                 for d in done.values()])
    for path in thumb_paths:
        with open(path, 'a', encoding='utf-8-sig', newline='') as fp:
            csv.writer(fp).writerows([[d['url'], d['thumb']] for d in done.values()])
    print(f'台帳に追記しました（master_db_added.csv ×{len(added_paths)} / thumb_map.csv ×{len(thumb_paths)}）')


if __name__ == '__main__':
    main()
