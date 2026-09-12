# -*- coding: utf-8 -*-
"""③ 採用を確定し、目視シートを作る。

入力 : {work}/match.json / {work}/snkr.json / {work}/rows.json
       {work}/pick.json （任意・目視の結果を人が書く）
出力 : {work}/final.json
       {work}/sheets/final_{本}.png  （左=スニダン正典 / 右=採用画像）

pick.json の書き方（apparel ID をキーにする）:
  {
    "159664": {"reuse": "https://minnano-toreka.com/.../xxx.webp", "wp_id": 32579},
    "766293": {"url": "https://www.cardrush-op.jp/data/cardrush-op/product/xxx.jpg",
               "src": "cardrush-op", "note": "上位記念品"},
    "128147": {"url": "snkr", "src": "snkrdunk", "note": "スニダン原寸をトリム"}
  }
  reuse … 保管庫に既に正しい版がある（再アップしない）
  url   … 新しく調達した画像（保管庫へアップする）
  url が "snkr" のときは {work}/cand/snkr_{ID}.png を読む

pick.json に無いカードは match の1位をそのまま採用する。
"""
import io
import json
import os

import requests
from PIL import Image, ImageDraw, ImageFont

import imgmatch
import paths

H = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                   'AppleWebKit/537.36 Chrome/128 Safari/537.36'}

# 調達元ごとの Referer（付けないと弾かれる）
REFERERS = {'cardrush-op': 'https://www.cardrush-op.jp/',
            'cardrush-pokemon': 'https://www.cardrush-pokemon.jp/'}


def referer_for(url):
    for key, ref in REFERERS.items():
        if key in url:
            return ref
    return ''


def fetch_image(url, ref=''):
    r = requests.get(url, headers={**H, 'Referer': ref}, timeout=40)
    r.raise_for_status()
    return Image.open(io.BytesIO(r.content))


def load_font(size=13):
    for p in ('/System/Library/Fonts/ヒラギノ角ゴシック W4.ttc',
              '/System/Library/Fonts/Hiragino Sans GB.ttc'):
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


def main():
    args = paths.parse_args('採用を確定し、目視シートを作る')
    work = args.work
    M = json.load(open(paths.need(work, 'match.json'), encoding='utf-8'))
    S = json.load(open(paths.need(work, 'snkr.json'), encoding='utf-8'))
    rows = json.load(open(paths.need(work, 'rows.json'), encoding='utf-8'))
    pick_path = work / 'pick.json'
    PICK = json.load(open(pick_path, encoding='utf-8')) if pick_path.exists() else {}
    cand_dir = work / 'cand'

    need = {}
    for rs in rows.values():
        for r in rs:
            if r.get('aid'):
                need.setdefault(str(r['aid']), r['name'])

    F = {}
    for a, name in need.items():
        p = PICK.get(a)
        if p and p.get('reuse'):
            im = fetch_image(p['reuse'])
            F[a] = dict(design=name, url=p['reuse'], src='保管庫(既存・目視OK)',
                        w=im.width, h=im.height, d=-1, upload=False,
                        note=f'既存 wp{p.get("wp_id", "")}', wp_id=p.get('wp_id'))
        elif p and p.get('url'):
            if p['url'] == 'snkr':
                local = cand_dir / f'snkr_{a}.png'
                if not local.exists():
                    raise SystemExit(f'{local} がありません。スニダン原寸を保存してから実行してください。')
                im = imgmatch.trim(Image.open(local))
                url = S[a]['img']
            else:
                url = p['url']
                im = fetch_image(url, referer_for(url))
            F[a] = dict(design=name, url=url, src=p.get('src', ''), w=im.width, h=im.height,
                        d=-1, upload=True, note=p.get('note', ''))
        else:
            cands = M.get(a, {}).get('cands') or []
            if not cands:
                raise SystemExit(
                    f'apparel {a}（{name}）に候補がありません。\n'
                    f'  pick.json に調達先を書いてから実行してください。')
            b = cands[0]
            F[a] = dict(design=name, url=b['url'], src=b['src'], w=b['w'], h=b['h'],
                        d=b['d'], upload=False, note='')

    json.dump(F, open(work / 'final.json', 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print(f'{len(F)}種 / 新規アップ {sum(1 for v in F.values() if v["upload"])}種  → {work / "final.json"}')

    # ---- 目視シート（本ごと・左=スニダン正典 / 右=採用画像）
    sheets = work / 'sheets'
    sheets.mkdir(exist_ok=True)
    font = load_font()
    for k in rows:
        tiles, seen = [], set()
        for r in rows[k]:
            a = str(r.get('aid') or '')
            if not a or a in seen:
                continue
            seen.add(a)
            v = F[a]
            try:
                left = imgmatch.trim(imgmatch.fetch(S[a]['img'])) if a in S and S[a].get('img') else None
            except Exception:
                left = None
            try:
                if v['src'] == 'snkrdunk':
                    right = imgmatch.trim(Image.open(cand_dir / f'snkr_{a}.png'))
                else:
                    right = fetch_image(v['url'], referer_for(v['url']))
            except Exception as e:
                right = None
                print(f'  ★画像が出せません {a}: {type(e).__name__}')

            tile = Image.new('RGB', (440, 350), 'white')
            d = ImageDraw.Draw(tile)
            for i, img in enumerate((left, right)):
                if img is None:
                    continue
                t = img.convert('RGB').copy()
                t.thumbnail((200, 290))
                tile.paste(t, (10 + i * 220, 36))
            d.text((8, 2), f'{r.get("rank", "")} {r["name"]}'[:34], fill='black', font=font)
            d.text((8, 18),
                   f'{a} 右={v["src"]} {v["w"]}px {"★新規" if v["upload"] else ""} {v["note"]}'[:44],
                   fill='#c00' if v['upload'] else 'gray', font=font)
            tiles.append(tile)

        if not tiles:
            continue
        cols = 3
        n = (len(tiles) + cols - 1) // cols
        sheet = Image.new('RGB', (cols * 440, n * 350), 'white')
        for i, t in enumerate(tiles):
            sheet.paste(t, ((i % cols) * 440, (i // cols) * 350))
        out = sheets / f'final_{k}.png'
        sheet.save(out)
        print(f'  → {out.name}  {len(tiles)}枚')

    print('\n★目視シートで全対を確認してから次（upload）へ進みます。')


if __name__ == '__main__':
    main()
