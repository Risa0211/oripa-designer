"""スニダン価格インデックスを専用スプレッドシートへ「見やすいUI付き」で書き込む。

タブ構成: 使い方 / ポケカ / ワンピ （それ以外の余分タブは削除）
各データタブ:
  行1-3 = ステータスボックス(色付き・最終更新/状態/件数/操作案内)
  行5   = 日本語の列見出し(固定) ／ 行6〜 = データ
  重要列を左(カード名/型番/レア/★最終相場/買取(手入力))、技術列は非表示
  価格は ¥ 表記、手入力列は色分け、1列目+見出し行を固定
既存の「買取(手入力)」はapparel_idで照合して保全。

実行: python3 scripts/refresh_index.py            # ロードのみ
      python3 scripts/refresh_index.py --reprice  # 日次(価格再取得)
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
STATUS_ROWS = 4          # 1-3=ステータス, 4=空
HEADER_ROW = 5           # 見出し行(1-based)
DATA_START = 6

ITEM_JA = {"single": "シングル", "box": "BOX", "pack": "パック", "deck": "デッキ", "other": "その他", "sealed": "未開封"}

# 表示列: (見出し, 元キー, 幅px, 種別)  種別: text/price/url/flag/item/tech(非表示)
DISPLAY = [
    ("カード名", "name", 240, "text"),
    ("レア", "rarity", 70, "text"),
    ("型番", "card_number", 110, "text"),
    ("種別", "item_type", 80, "item"),
    ("★最終相場", "final_value", 110, "price"),
    ("買取(手入力)", "buyout_manual", 110, "price"),
    ("スニダン相場", "snkrdunk_value", 110, "price"),
    ("PSA10価格", "psa10_price", 100, "price"),
    ("生価格", "raw_price", 90, "price"),
    ("採用", "price_source", 60, "text"),
    ("毎日更新", "daily_check", 70, "flag"),
    ("スニダンURL", "url", 90, "url"),
    ("備考", "price_note", 220, "text"),
    ("更新日", "priced_at", 110, "text"),
    ("apparel_id", "apparel_id", 90, "tech"),
    ("set_code", "set_code", 70, "tech"),
    ("product_number", "product_number", 120, "tech"),
]
HEADERS = [d[0] for d in DISPLAY]
IDX_BUYOUT = HEADERS.index("買取(手入力)")
IDX_APPID = HEADERS.index("apparel_id")

USAGE = [
    ["🎴 スニダン価格インデックス ｜ 使い方"],
    [""],
    ["このシートは、スニダンにある全ポケカ・ワンピのカード相場を毎朝自動で集めた一覧です。"],
    [""],
    ["■ どのタブを見る？"],
    ["   ・「ポケカ」タブ … ポケモンカードの相場一覧"],
    ["   ・「ワンピ」タブ … ワンピースカードの相場一覧"],
    [""],
    ["■ 各タブの見方"],
    ["   ・上の色付きボックス … 最終更新日時と状態(🟢正常/🔴失敗)。毎朝6:20に自動更新されます。"],
    ["   ・「★最終相場」列 … これがそのカードの採用相場です(倍率をかける元)。"],
    ["   ・「買取(手入力)」列 … 買取チェッカーで調べた買取額をここに手入力すると、"],
    ["                        最終相場が max(スニダン, 買取) に自動で切り替わります(自動更新でも消えません)。"],
    ["   ・「毎日更新」列が ○ のカード … 価格ありで毎朝再取得する対象(約17,495件)。"],
    ["                        空欄のカード … 現在スニダンに相場データが無い(=ほぼ無価値)。一覧には残しています。"],
    [""],
    ["■ やること"],
    ["   1) 基本は何もしなくてOK。毎朝スニダン価格が自動更新されます。"],
    ["   2) ガチャの目玉カードだけ、買取チェッカーで買取額を見て「買取(手入力)」に入れてください(任意)。"],
    [""],
    ["■ 困ったら"],
    ["   ・上部が🔴失敗の時はChatworkにも通知が飛びます。"],
]


def now_jst():
    return datetime.now(JST).strftime("%Y-%m-%d %H:%M")


def load_csv(game):
    with open(os.path.join(DATA, CSV_BY_GAME[game]), encoding="utf-8") as f:
        return list(csv.DictReader(f))


def read_existing_buyout(ws):
    """既存タブから apparel_id -> 買取(手入力) を保全。"""
    out = {}
    try:
        vals = ws.get_all_values()
    except Exception:
        return out
    if len(vals) < HEADER_ROW:
        return out
    hdr = vals[HEADER_ROW - 1]
    if "apparel_id" not in hdr or "買取(手入力)" not in hdr:
        return out
    ii, ib = hdr.index("apparel_id"), hdr.index("買取(手入力)")
    for row in vals[DATA_START - 1:]:
        if len(row) > max(ii, ib) and row[ii] and row[ib].strip():
            out[row[ii]] = row[ib].strip()
    return out


def reprice(rows):
    from snkrdunk_client import fetch_recent_price
    from concurrent.futures import ThreadPoolExecutor, as_completed
    targets = [r for r in rows if r.get("daily_check") == "TRUE"]

    def one(r):
        is_pack = r["item_type"] in ("box", "pack")
        try:
            price, _ = fetch_recent_price(r["url"], grade="PSA10", is_pack=is_pack, item_name=r["name"])
        except Exception:
            price = None
        return r, price
    ok = 0
    with ThreadPoolExecutor(max_workers=4) as ex:
        for fut in as_completed([ex.submit(one, r) for r in targets]):
            r, price = fut.result()
            if price:
                r["psa10_price"] = str(price); ok += 1
            time.sleep(0.15)
    return len(targets), ok


def recompute(rows, buyout_keep):
    for r in rows:
        def n(k):
            try:
                return int(r.get(k) or 0)
            except Exception:
                return 0
        bo = buyout_keep.get(r["apparel_id"], r.get("buyout_manual", "")) or ""
        r["buyout_manual"] = bo
        try:
            bo_n = int(bo)
        except Exception:
            bo_n = 0
        sv = max(n("psa10_price"), n("raw_price"))
        r["snkrdunk_value"] = str(sv) if sv else ""
        r["price_source"] = "" if sv == 0 else ("PSA10" if n("psa10_price") >= n("raw_price") and n("psa10_price") > 0 else "生")
        fv = max(sv, bo_n)
        r["final_value"] = str(fv) if fv else ""
        r["daily_check"] = "TRUE" if fv > 0 else "FALSE"


def cell(r, key, kind):
    v = r.get(key, "")
    if kind == "item":
        return ITEM_JA.get(v, v)
    if kind == "flag":
        return "○" if v == "TRUE" else ""
    if kind == "price":
        try:
            return int(v) if v else ""
        except Exception:
            return v
    return v


def build_matrix(rows):
    out = [HEADERS]
    for r in rows:
        out.append([cell(r, k, kind) for (_, k, _, kind) in DISPLAY])
    return out


def fmt_requests(sid, ncols, nrows, ok):
    C = lambda hexv: {"red": int(hexv[0:2], 16) / 255, "green": int(hexv[2:4], 16) / 255, "blue": int(hexv[4:6], 16) / 255}
    status_bg = C("1E8E3E") if ok else C("D93025")
    reqs = [
        # 固定(見出し行まで + 1列目)
        {"updateSheetProperties": {"properties": {"sheetId": sid, "gridProperties": {"frozenRowCount": HEADER_ROW, "frozenColumnCount": 1}}, "fields": "gridProperties.frozenRowCount,gridProperties.frozenColumnCount"}},
        # ステータスボックス(行1-3)
        {"repeatCell": {"range": {"sheetId": sid, "startRowIndex": 0, "endRowIndex": 3, "startColumnIndex": 0, "endColumnIndex": ncols},
                        "cell": {"userEnteredFormat": {"backgroundColor": status_bg, "textFormat": {"bold": True, "fontSize": 12, "foregroundColor": C("FFFFFF")}, "verticalAlignment": "MIDDLE"}},
                        "fields": "userEnteredFormat(backgroundColor,textFormat,verticalAlignment)"}},
        {"updateDimensionProperties": {"range": {"sheetId": sid, "dimension": "ROWS", "startIndex": 0, "endIndex": 3}, "properties": {"pixelSize": 30}, "fields": "pixelSize"}},
        # 見出し行(HEADER_ROW)
        {"repeatCell": {"range": {"sheetId": sid, "startRowIndex": HEADER_ROW - 1, "endRowIndex": HEADER_ROW, "startColumnIndex": 0, "endColumnIndex": ncols},
                        "cell": {"userEnteredFormat": {"backgroundColor": C("263238"), "textFormat": {"bold": True, "foregroundColor": C("FFFFFF")}, "horizontalAlignment": "CENTER"}},
                        "fields": "userEnteredFormat(backgroundColor,textFormat,horizontalAlignment)"}},
    ]
    # 列幅
    for i, d in enumerate(DISPLAY):
        reqs.append({"updateDimensionProperties": {"range": {"sheetId": sid, "dimension": "COLUMNS", "startIndex": i, "endIndex": i + 1}, "properties": {"pixelSize": d[2]}, "fields": "pixelSize"}})
    # 価格列 ¥書式
    for i, d in enumerate(DISPLAY):
        if d[3] == "price":
            reqs.append({"repeatCell": {"range": {"sheetId": sid, "startRowIndex": DATA_START - 1, "endRowIndex": nrows + DATA_START, "startColumnIndex": i, "endColumnIndex": i + 1},
                                        "cell": {"userEnteredFormat": {"numberFormat": {"type": "NUMBER", "pattern": "¥#,##0"}}}, "fields": "userEnteredFormat.numberFormat"}})
    # ★最終相場 列を強調
    fi = HEADERS.index("★最終相場")
    reqs.append({"repeatCell": {"range": {"sheetId": sid, "startRowIndex": DATA_START - 1, "endRowIndex": nrows + DATA_START, "startColumnIndex": fi, "endColumnIndex": fi + 1},
                                "cell": {"userEnteredFormat": {"backgroundColor": C("FFF3CD"), "textFormat": {"bold": True}}}, "fields": "userEnteredFormat(backgroundColor,textFormat)"}})
    # 買取(手入力) 列を色分け(入力欄)
    bi = IDX_BUYOUT
    reqs.append({"repeatCell": {"range": {"sheetId": sid, "startRowIndex": DATA_START - 1, "endRowIndex": nrows + DATA_START, "startColumnIndex": bi, "endColumnIndex": bi + 1},
                                "cell": {"userEnteredFormat": {"backgroundColor": C("E8F0FE")}}, "fields": "userEnteredFormat.backgroundColor"}})
    # 技術列を非表示
    for i, d in enumerate(DISPLAY):
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
    sid = ws.id
    C = lambda h: {"red": int(h[0:2], 16) / 255, "green": int(h[2:4], 16) / 255, "blue": int(h[4:6], 16) / 255}
    ss.batch_update({"requests": [
        {"updateDimensionProperties": {"range": {"sheetId": sid, "dimension": "COLUMNS", "startIndex": 0, "endIndex": 1}, "properties": {"pixelSize": 900}, "fields": "pixelSize"}},
        {"repeatCell": {"range": {"sheetId": sid, "startRowIndex": 0, "endRowIndex": 1}, "cell": {"userEnteredFormat": {"textFormat": {"bold": True, "fontSize": 14}}}, "fields": "userEnteredFormat.textFormat"}},
    ]})
    return ws


def write_tab(ws, rows, status):
    ss = ws.spreadsheet
    ws.clear()
    daily = sum(1 for r in rows if r["daily_check"] == "TRUE")
    icon = "🟢 正常" if status["ok"] else "🔴 失敗"
    top = [
        [f"🎴 {status['label']} ｜ スニダン価格インデックス", "", "", "", f"状態: {icon}"],
        [f"最終更新: {status['ts']}（毎朝 6:20 自動更新）", "", "", "", status.get("detail", "")],
        [f"総 {len(rows):,}件 ／ 毎日更新 {daily:,}件 ／ ★最終相場=採用値・買取(手入力)は水色列に入力", "", "", "", ""],
    ]
    ws.update("A1", top, value_input_option="RAW")
    matrix = build_matrix(rows)
    for start in range(0, len(matrix), CHUNK):
        chunk = matrix[start:start + CHUNK]
        ws.update(f"A{HEADER_ROW + start}", chunk, value_input_option="RAW")
        time.sleep(0.8)
    ss.batch_update({"requests": fmt_requests(ws.id, len(HEADERS), len(rows), status["ok"])})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reprice", action="store_true")
    args = ap.parse_args()
    cli = get_client()
    ss = cli.open_by_key(config.INDEX_SHEET_ID)

    write_usage(ss)
    overall_ok = True
    for game, tab in config.INDEX_TABS.items():
        t0 = time.time()
        rows = load_csv(game)
        try:
            ws = ss.worksheet(tab)
        except Exception:
            ws = ss.add_worksheet(title=tab, rows=len(rows) + 10, cols=len(HEADERS) + 2)
        keep = read_existing_buyout(ws)
        ok_flag = True; detail = "ロードのみ"
        try:
            if args.reprice:
                repriced, ok = reprice(rows); detail = f"再取得 {ok}/{repriced}"
            recompute(rows, keep)
        except Exception as e:
            ok_flag = False; overall_ok = False; detail = str(e)[:80]
            recompute(rows, keep)
        write_tab(ws, rows, {"label": tab, "ts": now_jst(), "ok": ok_flag, "detail": detail})
        print(f"[{tab}] {'OK' if ok_flag else 'NG'} {detail} 書込{len(rows)}件 {time.time()-t0:.0f}s", flush=True)

    # 余分タブ削除(使い方/ポケカ/ワンピ 以外)
    keep_titles = {"使い方"} | set(config.INDEX_TABS.values())
    for ws in ss.worksheets():
        if ws.title not in keep_titles:
            try:
                ss.del_worksheet(ws); print(f"余分タブ削除: {ws.title}", flush=True)
            except Exception:
                pass
    # 使い方を先頭に
    try:
        ss.reorder_worksheets([ss.worksheet("使い方")] + [ss.worksheet(t) for t in config.INDEX_TABS.values()])
    except Exception:
        pass
    if not overall_ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
