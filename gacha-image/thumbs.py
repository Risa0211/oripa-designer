# -*- coding: utf-8 -*-
"""⑥ サムネ依頼用の画像フォルダを作る（デザイナーへ渡す一式）。

入力 : {work}/rows.json / {work}/meta.json / {work}/final.json
出力 : {work}/thumbs/{ガチャ名 訴求}/
         _一覧.txt
         01_1等_カード名 [型番].webp
         02_ラストワン賞_…
         03_2等_…

- 対象は **apparel ID のある実カードだけ**（PT交換専用・最低保証は入れない）
- 並びは設計の順（1等 → ラストワン賞 → 2等 → …）
- フォルダ名はガチャ名から `!!` を外し、`/` を `-` にしたもの

    python3 thumbs.py --work ~/work/batch12
    python3 thumbs.py --work ~/work/batch12 --out ~/Downloads/第12陣_サムネ用当たりカード
"""
import io
import json
import re

import requests
from PIL import Image

import paths

H = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                   'AppleWebKit/537.36 Chrome/128 Safari/537.36'}


def referer_for(url):
    if 'cardrush-op' in url:
        return 'https://www.cardrush-op.jp/'
    if 'cardrush-pokemon' in url:
        return 'https://www.cardrush-pokemon.jp/'
    return ''


def safe(name):
    """フォルダ名・ファイル名に使えない文字を落とす。`!!` は外し、`/` は `-` にする。"""
    s = name.replace('!!', '').replace('！！', '')
    s = s.replace('/', '-').replace('／', '-')
    s = re.sub(r'[<>:"\\|?*\n\r\t]', '', s)
    return re.sub(r'\s+', ' ', s).strip()


def to_webp(data, max_w=900, quality=90):
    try:
        im = Image.open(io.BytesIO(data))
        if im.mode in ('P', 'LA'):
            im = im.convert('RGBA')
        elif im.mode == 'CMYK':
            im = im.convert('RGB')
        if im.width > max_w:
            im = im.resize((max_w, round(im.height * max_w / im.width)), Image.LANCZOS)
        out = io.BytesIO()
        im.convert('RGB').save(out, 'WEBP', quality=quality, method=6)
        return out.getvalue()
    except Exception:
        return data


def main():
    def extra(ap):
        ap.add_argument('--out', default='', help='出力先（既定: {work}/thumbs）')

    args = paths.parse_args('サムネ依頼用の画像フォルダを作る', extra)
    work = args.work
    rows = json.load(open(paths.need(work, 'rows.json'), encoding='utf-8'))
    meta = json.load(open(paths.need(work, 'meta.json'), encoding='utf-8'))
    F = json.load(open(paths.need(work, 'final.json'), encoding='utf-8'))

    root = (work / 'thumbs') if not args.out else __import__('pathlib').Path(args.out).expanduser()
    root.mkdir(parents=True, exist_ok=True)

    total_books = total_cards = 0
    for k, rs in rows.items():
        title = (meta.get(k) or {}).get('title') or k
        folder = root / safe(title)
        folder.mkdir(parents=True, exist_ok=True)

        lines = [title]
        n = 0
        seen = set()
        for r in rs:
            aid = str(r.get('aid') or '')
            if not aid:              # PT交換専用・最低保証など実カードでないもの
                continue
            if aid in seen:          # 同じカードが複数行にある場合（演出違いなど）
                continue
            seen.add(aid)
            v = F.get(aid)
            if not v:
                print(f'  ★{title}: {r["name"][:36]} の採用画像がありません（final を先に）')
                continue
            n += 1
            rank = str(r.get('rank') or '')
            qty = r.get('n') or 1
            lines.append(f'{rank} {r["name"]} ×{qty}')

            fn = safe(f'{n:02d}_{rank}_{r["name"]}') + '.webp'
            try:
                raw = requests.get(v['url'], headers={**H, 'Referer': referer_for(v['url'])},
                                   timeout=60).content
                (folder / fn).write_bytes(to_webp(raw))
            except Exception as e:
                print(f'  ★NG {fn[:50]} {type(e).__name__}')
                continue

        (folder / '_一覧.txt').write_text('\n'.join(lines) + '\n', encoding='utf-8')
        print(f'  {safe(title)[:44]:<46} {n}枚')
        total_books += 1
        total_cards += n

    print(f'\n{total_books}本 / 計{total_cards}枚  → {root}')
    print('★フォルダごとデザイナーへ渡します。_一覧.txt に等級と枚数が入っています。')


if __name__ == '__main__':
    main()
