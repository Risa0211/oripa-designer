# -*- coding: utf-8 -*-
"""⑤ 確認ギャラリーを作る（人に見せて承認をもらうための画面）。

入力 : {work}/rows.json / {work}/meta.json / {work}/match.json / {work}/final.json（あれば）
出力 : {work}/gallery/index.html

左=スニダン正典 / 右=採用画像。橙枠=新規にアップするもの、赤枠=まだ採用が決まっていないもの。

    cd {work}/gallery && python3 -m http.server 8778
    → Chrome で http://localhost:8778 を開く

★Chromeで開いた状態にしてから報告する。★承認が出るまでCSVは作らない。
"""
import html
import json

import paths
from match import GOOD_AR, GOOD_DIST, GOOD_WIDTH

CSS = """body{font-family:sans-serif;background:#f6f6f6;margin:16px}
h2{margin:24px 0 8px}
.g{display:flex;flex-wrap:wrap;gap:10px}
.c{background:#fff;border:3px solid #ddd;border-radius:6px;padding:6px;width:440px}
.p{display:flex;gap:8px}
.p div{width:210px;text-align:center}
.c.warn{border-color:#f90}
.c.bad{border-color:#e33}
.c img{height:270px;width:200px;object-fit:contain;background:#eee}
.n{font-size:12px;height:3em;overflow:hidden}
.m{font-size:11px;color:#555}
.none{height:270px;width:200px;background:#fdd;display:inline-block;line-height:270px}"""


def main():
    args = paths.parse_args('確認ギャラリーを作る')
    work = args.work
    rows = json.load(open(paths.need(work, 'rows.json'), encoding='utf-8'))
    M = json.load(open(paths.need(work, 'match.json'), encoding='utf-8'))
    meta_path = work / 'meta.json'
    meta = json.load(open(meta_path, encoding='utf-8')) if meta_path.exists() else {}
    final_path = work / 'final.json'
    F = json.load(open(final_path, encoding='utf-8')) if final_path.exists() else {}

    title = f'景品画像の確認（{work.name}）— 左=スニダン正典 / 右=採用画像'
    out = [f'<meta charset="utf-8"><title>{html.escape(title)}</title><style>{CSS}</style>',
           f'<h1 style="font-size:18px">{html.escape(title)}</h1>']

    for k in rows:
        label = (meta.get(k) or {}).get('title') or k
        out.append(f'<h2>{html.escape(str(label))}</h2><div class="g">')
        seen = set()
        for r in rows[k]:
            a = str(r.get('aid') or '')
            if not a or a in seen:
                continue
            seen.add(a)
            v = M.get(a, {})
            f = F.get(a, {})
            best = (v.get('cands') or [None])[0]
            pick = f.get('url') or (best['url'] if best else '')
            width = f.get('w', best['w'] if best else 0)
            src = f.get('src') or (best['src'] if best else '')
            note = f.get('note') or ''

            if not pick:
                cls = 'bad'
            elif f.get('upload'):
                cls = 'warn'
            elif best and not (best['d'] <= GOOD_DIST and best['w'] >= GOOD_WIDTH
                               and GOOD_AR[0] <= best['ar'] <= GOOD_AR[1]):
                cls = 'warn'
            else:
                cls = ''

            right = (f'<img src="{html.escape(pick)}" referrerpolicy="no-referrer">'
                     if pick else '<div class="none">要調達</div>')
            out.append(
                f'<div class="c {cls}"><div class="n">{html.escape(str(r.get("rank", "")))} '
                f'{html.escape(r["name"])}</div>'
                f'<div class="p"><div>左=スニダン<br>'
                f'<img src="{html.escape(v.get("snkr") or "")}" referrerpolicy="no-referrer"></div>'
                f'<div>右=採用画像<br>{right}</div></div>'
                f'<div class="m">右={html.escape(str(src))} 幅{width}px {html.escape(note)}</div></div>')
        out.append('</div>')

    dest_dir = work / 'gallery'
    dest_dir.mkdir(exist_ok=True)
    dest = dest_dir / 'index.html'
    open(dest, 'w', encoding='utf-8').write('\n'.join(out))
    print(f'→ {dest}')
    print(f'\n  cd {dest_dir} && python3 -m http.server 8778')
    print('  → Chrome で http://localhost:8778 を開いてから報告する')


if __name__ == '__main__':
    main()
