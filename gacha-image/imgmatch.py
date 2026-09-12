# -*- coding: utf-8 -*-
"""画像の取得・トリム・dHash。

   スニダンの画像を正典にして、保管庫の候補を dHash で突き合わせる。
   ★型番＋名前の一致だけで採用しない（同番の別カード／未開封の袋撮りを掴む）"""
import io, re, threading, warnings
warnings.filterwarnings('ignore')
import requests
from PIL import Image
from concurrent.futures import ThreadPoolExecutor

UA = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)'}
_cache, _lock = {}, threading.Lock()


def fetch(url):
    with _lock:
        if url in _cache:
            return _cache[url]
    try:
        r = requests.get(url, headers=UA, timeout=25)
        im = Image.open(io.BytesIO(r.content))
        im.load()
    except Exception:
        im = None
    with _lock:
        _cache[url] = im
    return im


def trim(im):
    """スニダンは透過キャンバス。しかも『カード＋右に拡大アップ』が1枚に入っていることがある。
       アルファの列プロファイルで塊に分け、一番広い塊＝カード本体だけを切り出す。"""
    if im is None:
        return None
    if im.mode in ('RGBA', 'LA'):
        a = im.getchannel('A')
        bb = a.getbbox()
        if not bb:
            return im.convert('RGB')
        a = a.crop(bb)
        w, h = a.size
        px = a.load()
        # 列ごとに不透明画素があるか（縦を8px間隔で走査）
        cols = [any(px[x, y] > 16 for y in range(0, h, 8)) for x in range(w)]
        runs, st = [], None
        for x, on in enumerate(cols + [False]):
            if on and st is None:
                st = x
            elif not on and st is not None:
                runs.append((st, x)); st = None
        if len(runs) > 1:
            s0, e0 = max(runs, key=lambda r: r[1] - r[0])
            im = im.crop((bb[0] + s0, bb[1], bb[0] + e0, bb[3]))
        else:
            im = im.crop(bb)
    return im.convert('RGB')


def dhash(im, s=16):
    if im is None:
        return None
    g = im.convert('L').resize((s + 1, s), Image.LANCZOS)
    px = list(g.getdata())
    bits = []
    for y in range(s):
        for x in range(s):
            bits.append(px[y * (s + 1) + x] < px[y * (s + 1) + x + 1])
    return bits


def dist(a, b):
    if a is None or b is None:
        return 999
    return sum(1 for x, y in zip(a, b) if x != y)


def size_of(im):
    return (im.size if im is not None else (0, 0))


NG = re.compile(r'〔|『|鑑定済|状態|未開封|開封済|袋')
