# -*- coding: utf-8 -*-
"""② 保管庫の画像を探して、スニダン正典と dHash ＋ 縦横比で照合する。

入力 : {work}/snkr.json ＋ 台帳（gacha-csv-builder の master_db_*.csv）
出力 : {work}/match.json / {work}/match.log

判定（下の GOOD_* が基準）:
  合格        … 差分 <= 70 かつ 幅 >= 500 かつ 縦横比 0.66〜0.76
  ★なし      … 保管庫に候補が無い
  ★別カード  … 差分が大きい（同じ番号の別の弾を掴んでいる）
  ★比0.XX    … 縦横比が範囲外（切り抜きの失敗を疑う。トレカの正は約0.715）
  ★幅NNN     … 幅が足りない

★不合格でも候補は match.json に残る。final で採用するときに数値を見ること。
"""
import csv
import io
import json
import re
import threading
import warnings
from concurrent.futures import ThreadPoolExecutor

warnings.filterwarnings('ignore')

import paths
from imgmatch import dhash, dist, fetch, trim

GOOD_DIST = 70          # dHash の差分の上限
GOOD_WIDTH = 500        # 幅の下限
GOOD_AR = (0.66, 0.76)  # 縦横比の範囲（トレカ 63mm×88mm = 約0.715）

# 保管庫の名前がこれに当たるものは候補にしない（鑑定ケース・未開封の袋撮りなど）
NG = re.compile(r'〔|『|鑑定済|状態|未開封|開封済|袋')

COLS = ['型番', 'カード名', 'レアリティ', '画像URL']


def load_ledgers():
    db = []
    for path, label in paths.ledger_files():
        for r in csv.DictReader(io.open(path, encoding='utf-8-sig')):
            db.append(dict(
                ban=re.sub(r'\s+', ' ', re.sub(r'[{}\[\]]', ' ', str(r.get(COLS[0], '') or ''))).strip(),
                name=(r.get(COLS[1]) or '').strip(),
                rar=(r.get(COLS[2]) or '').strip(),
                url=(r.get(COLS[3]) or '').strip(),
                src=label))
    return db


def key(design):
    """設計名の末尾 [SV2a 025/073] から (弾コード, 番号) を取り出す。"""
    m = re.search(r'\[([^\]]+)\]\s*$', design)
    if not m:
        return None, None
    p = m.group(1).split()
    return (p[0], ' '.join(p[1:])) if len(p) >= 2 else (None, p[0])


def is_good(c):
    return c and c['d'] <= GOOD_DIST and c['w'] >= GOOD_WIDTH and GOOD_AR[0] <= c['ar'] <= GOOD_AR[1]


def why_ng(c):
    if not c:
        return 'なし'
    if c['d'] > GOOD_DIST:
        return '別カード'
    if not (GOOD_AR[0] <= c['ar'] <= GOOD_AR[1]):
        return '比' + str(c['ar'])
    return f"幅{c['w']}"


def main():
    args = paths.parse_args('保管庫の画像とスニダン正典を照合する')
    S = json.load(open(paths.need(args.work, 'snkr.json'), encoding='utf-8'))
    db = load_ledgers()
    print(f'台帳 {len(db):,}行 を読みました')

    out, lock = {}, threading.Lock()

    def work(a):
        v = S[a]
        ban, num = key(v['design'])
        cand = []
        for d in db:
            b = d['ban']
            if not b or not num or not d['url'].startswith('http') or NG.search(d['name']):
                continue
            if ban and num and (f'{ban} {num}' == b or (num in b and ban.lower() in b.lower())):
                cand.append(d)
            elif num and b == num:
                cand.append(d)

        ref = trim(fetch(v['img'])) if v.get('img') else None
        rh = dhash(ref)
        scored = []
        for c in cand:
            im = trim(fetch(c['url']))
            if im is None:
                continue
            scored.append(dict(name=c['name'], url=c['url'], src=c['src'],
                               d=dist(rh, dhash(im)), w=im.width, h=im.height,
                               ar=round(im.width / im.height, 3)))
        # ★合格圏の候補が複数あるときは一番大きい画像を採る。
        #   差分の小さい順だけで並べると、スニダン由来の430px級が高画質版を押しのける。
        good = [c for c in scored if c['d'] <= GOOD_DIST and GOOD_AR[0] <= c['ar'] <= GOOD_AR[1]]
        good.sort(key=lambda x: -x['w'])
        rest = [c for c in scored if c not in good]
        rest.sort(key=lambda x: (x['d'], -x['w']))

        with lock:
            out[a] = dict(design=v['design'], localized=v['localized'], snkr=v.get('img'),
                          sar=round(ref.width / ref.height, 3) if ref else 0,
                          cands=(good + rest)[:5])

    with ThreadPoolExecutor(max_workers=8) as ex:
        list(ex.map(work, S))

    json.dump(out, open(args.work / 'match.json', 'w', encoding='utf-8'), ensure_ascii=False, indent=1)

    lines, ok, ng = [], 0, 0
    for a, v in sorted(out.items(), key=lambda x: -(x[1]['cands'][0]['d'] if x[1]['cands'] else 999)):
        b = v['cands'][0] if v['cands'] else None
        if is_good(b):
            ok += 1
            continue
        ng += 1
        lines.append(f"  ★{why_ng(b):<9} {v['design'][:40]:<42} {(b['name'][:28] if b else '')}")
    lines.append(f'\n合格 {ok}種 / 要調達・要修正 {ng}種')
    text = '\n'.join(lines)
    open(args.work / 'match.log', 'w', encoding='utf-8').write(text + '\n')
    print(text)
    print(f'\n→ {args.work / "match.json"} / {args.work / "match.log"}')
    print('★次は全種の目視です（機械が合格と言っても飛ばさない）。HANDOFF_景品画像とCSV.md §3')


if __name__ == '__main__':
    main()
