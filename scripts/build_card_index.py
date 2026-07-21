"""Phase1: スニダン全カード列挙ツール（URL・型番・レア・種別付き一覧を生成）

対象: ポケカ(brandIds=pokemon) / ワンピ(brandIds=onepiece)
出力: シングル / BOX / パック / デッキ を全部含む
方式: /search?brandIds=..&page=N の productTile を全ページ巡回して抽出

- 礼儀正しいスロットル + 空応答リトライ（スロットリングを本当の終端と誤認しない）
- 既存CSVへ追記・seen ID管理でresume可能
- Google認証不要（まずローカルCSVを生成 → 中身OKなら専用スプシへ）

実行:
  python3 scripts/build_card_index.py                # 全ブランド全ページ
  python3 scripts/build_card_index.py --brand pokemon --max-pages 5   # 検証用
  python3 scripts/build_card_index.py --resume       # 既存CSVの続きから
"""
from __future__ import annotations
import argparse, csv, html, os, re, sys, time
from datetime import datetime, timezone, timedelta

try:
    import requests
except ImportError:
    sys.exit("requests が必要です: pip install requests")

JST = timezone(timedelta(hours=9))
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17 Safari/605.1.15"
HEADERS = {"User-Agent": UA, "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
           "Accept-Language": "ja,en;q=0.5"}

BRANDS = {
    "pokemon":  {"yuyu": "poc", "label": "ポケモン"},
    "onepiece": {"yuyu": "opc", "label": "ワンピース"},
}

OUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
OUT_CSV = os.path.join(OUT_DIR, "snkrdunk_card_index.csv")
FIELDS = ["brand", "apparel_id", "url", "item_type", "name", "rarity",
          "set_code", "card_number", "list_price", "raw_alt", "page", "fetched_at"]

# productTile アンカー: href の apparel ID と aria-label(名前+レア+型番+価格) を対で取る
TILE_RE = re.compile(
    r'href="https://snkrdunk\.com/apparels/(\d+)"\s+class="[^"]*productTile[^"]*"\s+aria-label="([^"]+)"',
    re.S)
# 単カード: "{name} {RARITY} [{set} {num}](...)"  例: メガリザードンXex MA [M2a 223/193](...)
# レアと"["が密着するケース(SR[S8b...])もあるので \s* を許容
SINGLE_RE = re.compile(r'^(?P<name>.+?)\s+(?P<rarity>[A-Z]{1,4})\s*\[(?P<setnum>[^\]]+)\]')
# 型番だけ(レア無し) "{name} [{set} {num}]"
NUMONLY_RE = re.compile(r'^(?P<name>.+?)\s*\[(?P<setnum>[^\]]+)\]')
# [M2a 223/193] / [001/SV-P] / [M-P 020] 等から set_code と number を分離
SETNUM_RE = re.compile(r'^(?:(?P<set>[A-Za-z0-9-]+)\s+)?(?P<num>[0-9]+/[0-9A-Za-z-]+|[0-9]+)$')
PRICE_RE = re.compile(r'[-‐−]\s*¥\s*([\d,]+)\s*$')


def now_jst() -> str:
    return datetime.now(JST).strftime("%Y-%m-%d %H:%M")


def classify(alt: str) -> str:
    a = alt
    if re.search(r'ボックス|BOX', a, re.I):
        return "box"
    if re.search(r'スタートデッキ|デッキ', a):
        return "deck"
    # [型番] があれば単カード（"プロモカードパック" 等でも型番付きは単カード扱い）
    if re.search(r'\[[^\]]*\d', a):
        return "single"
    if re.search(r'パック|PACK', a, re.I):
        return "pack"
    return "other"


def parse_alt(alt: str) -> dict:
    """aria-label から name/rarity/set/number/list_price を抽出"""
    alt = html.unescape(alt).strip()
    list_price = ""
    m_price = PRICE_RE.search(alt)
    core = alt
    if m_price:
        list_price = m_price.group(1).replace(",", "")
        core = alt[:m_price.start()].strip()
    item_type = classify(core)
    name = core
    rarity = set_code = card_number = ""
    if item_type == "single":
        m = SINGLE_RE.match(core) or NUMONLY_RE.match(core)
        if m:
            name = m.group("name").strip().rstrip(":：").strip()
            rarity = m.groupdict().get("rarity") or ""
            sn = SETNUM_RE.match(m.group("setnum").strip())
            if sn:
                set_code = (sn.group("set") or "").strip()
                card_number = sn.group("num").strip()
            else:
                card_number = m.group("setnum").strip()
        # 日本語レア「プロモ」を補完（レア未取得時）
        if not rarity and re.search(r'プロモ', core):
            rarity = "プロモ"
        # 名前末尾に残る ": プロモ" 等を除去
        name = re.sub(r'[:：]?\s*プロモ\s*$', '', name).strip().rstrip(":：").strip()
    return {"item_type": item_type, "name": name, "rarity": rarity,
            "set_code": set_code, "card_number": card_number, "list_price": list_price,
            "raw_alt": core}


def fetch_page(brand: str, page: int, retries: int = 3) -> list[tuple[str, str]]:
    """1ページの productTile を (apparel_id, aria_label) で返す。空はリトライ。"""
    url = f"https://snkrdunk.com/search?brandIds={brand}&page={page}"
    last_n = 0
    for attempt in range(retries):
        try:
            r = requests.get(url, headers=HEADERS, timeout=20)
            if r.status_code == 200:
                tiles = TILE_RE.findall(r.text)
                # dedupe within page, preserve order
                seen, out = set(), []
                for pid, alt in tiles:
                    if pid not in seen:
                        seen.add(pid); out.append((pid, alt))
                if out:
                    return out
                last_n = 0
            else:
                last_n = -1
        except requests.RequestException:
            last_n = -1
        time.sleep(2.0 + attempt * 1.5)  # backoff
    return []  # 本当に空（終端 or 恒久失敗）


def load_seen(path: str) -> set[str]:
    seen = set()
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                seen.add((row.get("brand", ""), row.get("apparel_id", "")))
    return seen


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--brand", choices=list(BRANDS), help="対象ブランド（未指定=全部）")
    ap.add_argument("--max-pages", type=int, default=0, help="上限ページ（0=終端まで）")
    ap.add_argument("--empty-stop", type=int, default=3, help="連続空Nページで終了")
    ap.add_argument("--delay", type=float, default=1.3, help="ページ間スリープ秒")
    ap.add_argument("--resume", action="store_true", help="既存CSVの続きから（重複スキップ）")
    ap.add_argument("--out", default=OUT_CSV)
    args = ap.parse_args()

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    brands = [args.brand] if args.brand else list(BRANDS)
    seen = load_seen(args.out) if args.resume else set()
    new_file = not os.path.exists(args.out) or (not args.resume)
    mode = "a" if (args.resume and os.path.exists(args.out)) else "w"

    with open(args.out, mode, encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        if mode == "w":
            w.writeheader()
        for brand in brands:
            print(f"\n=== {brand} ({BRANDS[brand]['label']}) 列挙開始 ===", flush=True)
            page, empty_streak, added = 1, 0, 0
            while True:
                if args.max_pages and page > args.max_pages:
                    break
                tiles = fetch_page(brand, page)
                if not tiles:
                    empty_streak += 1
                    print(f"  p{page}: 空 ({empty_streak}/{args.empty_stop})", flush=True)
                    if empty_streak >= args.empty_stop:
                        break
                    page += 1
                    time.sleep(args.delay)
                    continue
                empty_streak = 0
                page_new = 0
                for pid, alt in tiles:
                    key = (brand, pid)
                    if key in seen:
                        continue
                    seen.add(key)
                    d = parse_alt(alt)
                    w.writerow({"brand": brand, "apparel_id": pid,
                                "url": f"https://snkrdunk.com/apparels/{pid}",
                                "page": page, "fetched_at": now_jst(), **d})
                    page_new += 1
                added += page_new
                f.flush()
                print(f"  p{page}: {len(tiles)}件(新規{page_new}) 累計{added}", flush=True)
                page += 1
                time.sleep(args.delay)
            print(f"=== {brand} 完了: 新規{added}件 ===", flush=True)

    print(f"\n出力: {args.out}  総行数: {len(seen)}", flush=True)


if __name__ == "__main__":
    main()
