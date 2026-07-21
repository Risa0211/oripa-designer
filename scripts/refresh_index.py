"""スニダン価格インデックスを専用スプレッドシートへ書き込む(相場=PSA10直近/BOX下限)。

タブ: 使い方 / ポケカ / ワンピ
相場(souba): シングル=PSA10直近買取価格 / BOX・パック等=スニダン表記の下限額(minPrice)
空欄(履歴なし)は備考に「取引履歴なし（希少）」。全カードを残す。
毎日更新: 相場ありのカードを毎朝再取得(--reprice)。

実行: python3 scripts/refresh_index.py            # ロードのみ
      python3 scripts/refresh_index.py --reprice  # 日次(相場再取得)
"""
from __future__ import annotations
import argparse, csv, os, sys, time
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config
from sheets_client import get_client

JST = timezone(timedelta(hours=9))
DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
CSV_BY_GAME = {"pokemon": "index_pokemon.csv", "onepiece": "index_onepiece.csv"}
CHUNK = 8000
HEADER_ROW = 5
DATA_START = 6
ITEM_JA = {"single": "シングル", "box": "BOX", "pack": "パック", "deck": "デッキ", "other": "その他", "sealed": "未開封"}

# 表示列: (見出し, 元キー, 幅px, 種別 text/price/url/flag/item/tech)
DISPLAY = [
    ("カード名", "name", 250, "text"),
    ("レア", "rarity", 70, "text"),
    ("型番", "card_number", 110, "text"),
    ("種別", "item_type", 80, "item"),
    ("相場", "souba", 120, "price"),
    ("毎日更新", "daily_check", 80, "flag"),
    ("備考", "note", 240, "text"),
    ("スニダンURL", "url", 90, "url"),
    ("apparel_id", "apparel_id", 90, "tech"),
    ("set_code", "set_code", 70, "tech"),
    ("product_number", "product_number", 120, "tech"),
    ("psa10_price", "psa10_price", 90, "tech"),
    ("min_price", "min_price", 90, "tech"),
]
HEADERS = [d[0] for d in DISPLAY]

USAGE = [
    ["🎴 スニダン価格インデックス ｜ 使い方"],
    [""],
    ["スニダンにある全ポケカ・ワンピのカード相場を毎朝自動で集めた一覧です。"],
    [""],
    ["■ どのタブを見る？"],
    ["   ・「ポケカ」…ポケモンカード / 「ワンピ」…ワンピースカード"],
    [""],
    ["■ 各タブの見方"],
    ["   ・上の色ボックス … 最終更新日時と状態(🟢正常/🔴失敗)。毎朝6:20自動更新。"],
    ["   ・「相場」列 … これがそのカードの採用相場(倍率をかける元)。"],
    ["        シングル = スニダンPSA10の直近買取価格"],
    ["        BOX・パック = スニダン表記の下限額（「¥1,000〜」なら¥1,000）"],
    ["   ・「毎日更新」○ … 相場ありで毎朝再取得する対象。"],
    ["   ・相場が空欄 … スニダンに取引履歴が無いカード(かなり希少)。備考に明記。"],
    ["        価値が無いのではなく流通が少なく履歴が無いだけ。履歴が出れば自動で入ります。"],
    [""],
    ["■ やること"],
    ["   1) 基本は何もしなくてOK。毎朝スニダン相場が自動更新。"],
    ["   2) ガチャの目玉カードだけ、買取チェッカーで買取額を確認し、相場より高ければそちらを採用(手元判断)。"],
]


def now_jst():
    return datetime.now(JST).strftime("%Y-%m-%d %H:%M")


def load_csv(game):
    with open(os.path.join(DATA, CSV_BY_GAME[game]), encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _int(v):
    try:
        return int(v or 0)
    except Exception:
        return 0


def fetch_min_price(url):
    """/v1/apparels/{id} の minPrice(=表記下限) を取得。BOX/パック用。"""
    import re, json, urllib.request
    m = re.search(r"/apparels/(\d+)", url)
    if not m:
        return 0
    try:
        req = urllib.request.Request(f"https://snkrdunk.com/v1/apparels/{m.group(1)}",
                                     headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
        d = json.load(urllib.request.urlopen(req, timeout=15))
        return _int(d.get("minPrice")) or _int(d.get("usedMinPrice"))
    except Exception:
        return 0


def reprice(rows):
    """相場ありカードを再取得。single=PSA10直近 / それ以外=表記下限(minPrice)。"""
    from snkrdunk_client import fetch_recent_price
    from concurrent.futures import ThreadPoolExecutor, as_completed
    targets = [r for r in rows if r.get("daily_check") == "TRUE"]

    def one(r):
        try:
            if r["item_type"] == "single":
                price, _ = fetch_recent_price(r["url"], grade="PSA10", is_pack=False, item_name=r["name"])
                return r, "psa10_price", price
            else:
                return r, "min_price", fetch_min_price(r["url"])
        except Exception:
            return r, None, None
    ok = 0
    with ThreadPoolExecutor(max_workers=4) as ex:
        for fut in as_completed([ex.submit(one, r) for r in targets]):
            r, key, price = fut.result()
            if key and price:
                r[key] = str(price); ok += 1
            time.sleep(0.15)
    return len(targets), ok


def recompute(rows):
    for r in rows:
        souba = _int(r.get("psa10_price")) if r["item_type"] == "single" else _int(r.get("min_price"))
        r["souba"] = str(souba) if souba else ""
        r["daily_check"] = "TRUE" if souba else "FALSE"
        if not souba:
            r["note"] = "取引履歴なし（希少）"


def cell(r, key, kind):
    v = r.get(key, "")
    if kind == "item":
        return ITEM_JA.get(v, v)
    if kind == "flag":
        return "○" if v == "TRUE" else ""
    if kind == "price":
        return _int(v) if v else ""
    return v


def build_matrix(rows):
    return [HEADERS] + [[cell(r, k, kind) for (_, k, _, kind) in DISPLAY] for r in rows]


def C(h):
    return {"red": int(h[0:2], 16) / 255, "green": int(h[2:4], 16) / 255, "blue": int(h[4:6], 16) / 255}


def fmt_requests(sid, ncols, nrows, ok):
    status_bg = C("1E8E3E") if ok else C("D93025")
    reqs = [
        {"updateSheetProperties": {"properties": {"sheetId": sid, "gridProperties": {"frozenRowCount": HEADER_ROW, "frozenColumnCount": 1}}, "fields": "gridProperties.frozenRowCount,gridProperties.frozenColumnCount"}},
        {"repeatCell": {"range": {"sheetId": sid, "startRowIndex": 0, "endRowIndex": 3, "startColumnIndex": 0, "endColumnIndex": ncols},
                        "cell": {"userEnteredFormat": {"backgroundColor": status_bg, "textFormat": {"bold": True, "fontSize": 12, "foregroundColor": C("FFFFFF")}, "verticalAlignment": "MIDDLE"}},
                        "fields": "userEnteredFormat(backgroundColor,textFormat,verticalAlignment)"}},
        {"updateDimensionProperties": {"range": {"sheetId": sid, "dimension": "ROWS", "startIndex": 0, "endIndex": 3}, "properties": {"pixelSize": 30}, "fields": "pixelSize"}},
        {"repeatCell": {"range": {"sheetId": sid, "startRowIndex": HEADER_ROW - 1, "endRowIndex": HEADER_ROW, "startColumnIndex": 0, "endColumnIndex": ncols},
                        "cell": {"userEnteredFormat": {"backgroundColor": C("263238"), "textFormat": {"bold": True, "foregroundColor": C("FFFFFF")}, "horizontalAlignment": "CENTER"}},
                        "fields": "userEnteredFormat(backgroundColor,textFormat,horizontalAlignment)"}},
    ]
    for i, d in enumerate(DISPLAY):
        reqs.append({"updateDimensionProperties": {"range": {"sheetId": sid, "dimension": "COLUMNS", "startIndex": i, "endIndex": i + 1}, "properties": {"pixelSize": d[2]}, "fields": "pixelSize"}})
        if d[3] == "price":
            reqs.append({"repeatCell": {"range": {"sheetId": sid, "startRowIndex": DATA_START - 1, "endRowIndex": nrows + DATA_START, "startColumnIndex": i, "endColumnIndex": i + 1},
                                        "cell": {"userEnteredFormat": {"numberFormat": {"type": "NUMBER", "pattern": "¥#,##0"}, "textFormat": {"bold": True}, "backgroundColor": C("FFF3CD")}}, "fields": "userEnteredFormat(numberFormat,textFormat,backgroundColor)"}})
        if d[3] == "tech":
            reqs.append({"updateDimensionProperties": {"range": {"sheetId": sid, "dimension": "COLUMNS", "startIndex": i, "endIndex": i + 1}, "properties": {"hiddenByUser": True}, "fields": "hiddenByUser"}})
    return reqs


def write_usage(ss):
    try:
        ws = ss.worksheet("使い方")
    except Exception:
        ws = ss.add_worksheet(title="使い方", rows=40, cols=4)
    ws.clear()
    ws.update("A1", USAGE, value_input_option="RAW")
    ss.batch_update({"requests": [
        {"updateDimensionProperties": {"range": {"sheetId": ws.id, "dimension": "COLUMNS", "startIndex": 0, "endIndex": 1}, "properties": {"pixelSize": 820}, "fields": "pixelSize"}},
        {"repeatCell": {"range": {"sheetId": ws.id, "startRowIndex": 0, "endRowIndex": 1}, "cell": {"userEnteredFormat": {"textFormat": {"bold": True, "fontSize": 14}}}, "fields": "userEnteredFormat.textFormat"}},
    ]})


def write_tab(ws, rows, status):
    ss = ws.spreadsheet
    ws.clear()
    daily = sum(1 for r in rows if r["daily_check"] == "TRUE")
    icon = "🟢 正常" if status["ok"] else "🔴 失敗"
    top = [
        [f"🎴 {status['label']} ｜ スニダン価格インデックス", "", "", "", f"状態: {icon}"],
        [f"最終更新: {status['ts']}（毎朝 6:20 自動更新）", "", "", "", status.get("detail", "")],
        [f"総 {len(rows):,}件 ／ 相場あり(毎日更新) {daily:,}件 ／ 空欄=取引履歴なし(希少)", "", "", "", ""],
    ]
    ws.update("A1", top, value_input_option="RAW")
    matrix = build_matrix(rows)
    for start in range(0, len(matrix), CHUNK):
        ws.update(f"A{HEADER_ROW + start}", matrix[start:start + CHUNK], value_input_option="RAW")
        time.sleep(0.8)
    ss.batch_update({"requests": fmt_requests(ws.id, len(HEADERS), len(rows), status["ok"])})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reprice", action="store_true")
    args = ap.parse_args()
    ss = get_client().open_by_key(config.INDEX_SHEET_ID)
    write_usage(ss)
    overall_ok = True
    for game, tab in config.INDEX_TABS.items():
        t0 = time.time()
        rows = load_csv(game)
        try:
            ws = ss.worksheet(tab)
        except Exception:
            ws = ss.add_worksheet(title=tab, rows=len(rows) + 10, cols=len(HEADERS) + 2)
        detail = "ロードのみ"; ok_flag = True
        try:
            if args.reprice:
                tg, ok = reprice(rows); detail = f"相場再取得 {ok}/{tg}"
            recompute(rows)
        except Exception as e:
            ok_flag = False; overall_ok = False; detail = str(e)[:80]
            recompute(rows)
        write_tab(ws, rows, {"label": tab, "ts": now_jst(), "ok": ok_flag, "detail": detail})
        print(f"[{tab}] {'OK' if ok_flag else 'NG'} {detail} 書込{len(rows)}件 {time.time()-t0:.0f}s", flush=True)
    keep = {"使い方"} | set(config.INDEX_TABS.values())
    for ws in ss.worksheets():
        if ws.title not in keep:
            try:
                ss.del_worksheet(ws)
            except Exception:
                pass
    try:
        ss.reorder_worksheets([ss.worksheet("使い方")] + [ss.worksheet(t) for t in config.INDEX_TABS.values()])
    except Exception:
        pass
    if not overall_ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
