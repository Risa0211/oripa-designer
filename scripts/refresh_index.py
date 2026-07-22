"""スニダン価格インデックスを専用スプレッドシートへ書き込む。

タブ: 使い方 / ポケカ / ワンピ
2つの価格列(スニダンのカードページと同じ2指標):
  直近取引価格(souba)  = 直近に「売れた」価格(成約)。シングル=PSA10直近成約のみ(無ければ空欄・他グレードにフォールバックしない)。
  相場(souba_ask)      = 今「出ている」出品の最安値(現在値)。
                         シングル=PSA10グレードの出品最安 / BOX・パック=表記の下限額(minPrice)。
両方空欄 → 備考「取引履歴なし（希少）」。全カードを残す。
更新頻度/並び順は souba_sort(=両者の高い方)で判定。
毎日更新: souba_sort>¥3,000 + 残りは1/7ずつ巡回(--reprice)。

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
HEADER_ROW = 5
DATA_START = 6
ITEM_JA = {"single": "シングル", "box": "BOX", "pack": "パック", "deck": "デッキ", "other": "その他", "sealed": "未開封"}

# 表示列: (見出し, 元キー, 幅px, 種別 text/price/url/flag/item/tech)
DISPLAY = [
    ("カード名", "name", 250, "text"),
    ("レア", "rarity", 70, "text"),
    ("型番", "card_number", 110, "text"),
    ("種別", "item_type", 80, "item"),
    ("直近取引価格", "souba", 120, "price"),
    ("相場", "souba_ask", 120, "price"),
    ("更新頻度", "freq", 90, "text"),
    ("備考", "note", 240, "text"),
    ("スニダンURL", "url", 90, "url"),
    ("apparel_id", "apparel_id", 90, "tech"),
    ("set_code", "set_code", 70, "tech"),
    ("product_number", "product_number", 120, "tech"),
    ("psa10_price", "psa10_price", 90, "tech"),
    ("min_price", "min_price", 90, "tech"),
    ("ask_price", "ask_price", 90, "tech"),
    ("souba_sort", "souba_sort", 90, "tech"),
]
HEADERS = [d[0] for d in DISPLAY]

USAGE = [
    ["🎴 スニダン価格インデックス ｜ 使い方"],
    [""],
    ["スニダンにある全ポケカ・ワンピのカード相場を自動で集めた一覧です。"],
    [""],
    ["■ どのタブを見る？"],
    ["   ・「ポケカ」…ポケモンカード / 「ワンピ」…ワンピースカード"],
    [""],
    ["■ 価格は2列あります（スニダンのカードページと同じ2つの数字）"],
    ["   ●「直近取引価格」… 直近に “売れた” 価格（成約）"],
    ["        ・シングル … スニダンPSA10の直近成約価格（PSA10の成約が無ければ空欄）"],
    ["        ・BOX/パック … この列は空欄（成約は追っていません）"],
    ["   ●「相場」… 今 “出ている” 出品の最安値（リアルタイムの売り希望）"],
    ["        ・シングル … PSA10グレードの現在の出品最安（例「¥14,980〜」の14,980）"],
    ["        ・BOX/パック … スニダン表記の下限額（「¥1,000〜」なら¥1,000）"],
    ["   ※相場＝“売り希望の下限”なので、成約（直近取引価格）より高めに出ることがあります。"],
    ["     両方を見比べて判断してください。相場が空欄＝そのカードは今PSA10の出品ゼロ。"],
    ["   ※両方とも空欄 … スニダンに取引も出品も無いカード（かなり希少）。備考に明記。"],
    ["        価値が無いのではなく流通が少ないだけ。動きが出れば自動で入ります。"],
    [""],
    ["■ 更新頻度列（どのくらいの頻度で価格を取り直すか）"],
    ["   ・「毎日」… 価格（直近取引か相場の高い方）が¥3,000超の高額カード。毎朝6:20に最新化。"],
    ["   ・「週1」… それ以外（安いカード＋希少カード）。7日で必ず一巡して取り直し。"],
    ["        →希少カードも週1で新しい出品/履歴が出ていないかチェックしています（放置しません）。"],
    ["   ・上の色ボックス … 最終更新日時と状態(🟢正常/🔴失敗)。失敗時はChatwork通知。"],
    [""],
    ["■ 検索・絞り込み（閲覧のままでOK・データは変わりません）"],
    ["   ・検索 … Ctrl+F（Macは⌘+F）でカード名・型番を検索。"],
    ["   ・プリセット … 各タブ「データ → フィルタ表示」から選ぶだけ:"],
    ["        「価格が高い順」「毎日更新(高額)だけ」「価格あり(空欄を除く)」"],
    ["   ・自由に絞る … 見出しの▼ボタン、または「データ → フィルタ表示 → 新規作成」。"],
    ["        ※フィルタ表示は自分の画面だけ。他の人の見え方は変わりません。"],
    [""],
    ["■ やること"],
    ["   1) 基本は何もしなくてOK。価格は自動で更新されます。"],
    ["   2) ガチャの目玉カードだけ、買取チェッカーで買取額を確認し、相場より高ければそちらを採用（手元判断）。"],
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


DAILY_THRESHOLD = 3000  # 相場>¥3,000=毎日 / それ以外=7日巡回


def reprice(rows):
    """毎日: 高額(souba_sort>¥3,000) + その他全カードを1/7ずつ巡回(希少含む・7日で一巡)。
    single → 直近取引価格(PSA10直近成約)と相場(PSA10出品最安)の両方を再取得。
    それ以外 → 相場=表記下限(minPrice)。"""
    from snkrdunk_client import fetch_psa10_sale, fetch_psa10_ask
    from concurrent.futures import ThreadPoolExecutor, as_completed
    bucket = datetime.now(JST).timetuple().tm_yday % 7

    def is_target(r):
        if _int(r.get("souba_sort")) > DAILY_THRESHOLD:
            return True
        try:
            return int(r["apparel_id"]) % 7 == bucket
        except Exception:
            return False
    targets = [r for r in rows if is_target(r)]

    def one(r):
        upd = {}
        try:
            if r["item_type"] == "single":
                price, snote = fetch_psa10_sale(r["url"])    # 直近取引価格=PSA10成約のみ(無ければNone=空欄)
                if snote:                                    # 成功時のみ更新(通信失敗はstale保持)
                    upd["psa10_price"] = str(price) if price else ""
                    upd["note"] = snote
                ask = fetch_psa10_ask(r["url"])              # 相場(PSA10出品最安)
                if ask is not None:                          # None=取得失敗はstale保持
                    upd["ask_price"] = str(ask) if ask else ""  # 0=出品ゼロは空欄化
            else:
                mp = fetch_min_price(r["url"])               # 相場(表記下限)
                if mp:
                    upd["min_price"] = str(mp)
        except Exception:
            pass
        return r, upd
    ok = 0
    with ThreadPoolExecutor(max_workers=5) as ex:
        for fut in as_completed([ex.submit(one, r) for r in targets]):
            r, upd = fut.result()
            if upd:
                r.update(upd); ok += 1
            time.sleep(0.12)
    return len(targets), ok


def recompute(rows):
    for r in rows:
        single = r["item_type"] == "single"
        souba = _int(r.get("psa10_price")) if single else 0        # 直近取引価格(成約)
        ask = _int(r.get("ask_price")) if single else _int(r.get("min_price"))  # 相場(出品)
        hi = max(souba, ask)
        r["souba"] = str(souba) if souba else ""
        r["souba_ask"] = str(ask) if ask else ""
        r["souba_sort"] = str(hi) if hi else ""
        r["freq"] = "毎日" if hi > DAILY_THRESHOLD else "週1"
        if not hi:
            r["note"] = "取引履歴なし（希少）"           # 直近取引も相場も無い=希少
        elif single and not souba and ask:
            r["note"] = "PSA10成約なし（相場は出品あり）"  # 直近成約は無いが出品はある


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


VISIBLE_COLS = sum(1 for d in DISPLAY if d[3] != "tech")  # 表示列数
SOUBA_COL = next(i for i, d in enumerate(DISPLAY) if d[1] == "souba")
FREQ_COL = next(i for i, d in enumerate(DISPLAY) if d[1] == "freq")
SORT_COL = next(i for i, d in enumerate(DISPLAY) if d[1] == "souba_sort")  # 並び/絞込キー(直近取引と相場の高い方・非表示列)


def setup_filters(ss, ws, nrows):
    """閲覧者向けプリセット: 見出しにフィルタ▼ + フィルタ表示(価格順/毎日のみ/空欄除く)。
    並び・絞込は souba_sort(直近取引と相場の高い方・非表示列)を基準にする。"""
    sid = ws.id
    # フィルタ範囲は非表示のsouba_sort列まで含める(sort/criteriaがその列を参照するため)
    rng = {"sheetId": sid, "startRowIndex": HEADER_ROW - 1, "endRowIndex": HEADER_ROW + nrows,
           "startColumnIndex": 0, "endColumnIndex": SORT_COL + 1}
    sort_souba = [{"dimensionIndex": SORT_COL, "sortOrder": "DESCENDING"}]
    # 既存フィルタ表示/基本フィルタを削除(重複防止)
    dels = []
    try:
        meta = ss.fetch_sheet_metadata()
        for sh in meta.get("sheets", []):
            if sh["properties"]["sheetId"] == sid:
                for fv in sh.get("filterViews", []):
                    dels.append({"deleteFilterView": {"filterId": fv["filterViewId"]}})
    except Exception:
        pass
    dels.append({"clearBasicFilter": {"sheetId": sid}})
    try:
        ss.batch_update({"requests": dels})
    except Exception:
        pass
    adds = [
        {"setBasicFilter": {"filter": {"range": dict(rng)}}},
        {"addFilterView": {"filter": {"title": "価格が高い順", "range": dict(rng), "sortSpecs": sort_souba}}},
        {"addFilterView": {"filter": {"title": "毎日更新(高額)だけ", "range": dict(rng),
                                      "criteria": {str(FREQ_COL): {"hiddenValues": ["週1"]}}, "sortSpecs": sort_souba}}},
        {"addFilterView": {"filter": {"title": "価格あり(空欄を除く)", "range": dict(rng),
                                      "criteria": {str(SORT_COL): {"condition": {"type": "NOT_BLANK"}}}, "sortSpecs": sort_souba}}},
    ]
    try:
        ss.batch_update({"requests": adds})
    except Exception:
        pass


def write_tab(ws, rows, status):
    ss = ws.spreadsheet
    ws.clear()
    mainichi = sum(1 for r in rows if r.get("freq") == "毎日")
    icon = "🟢 正常" if status["ok"] else "🔴 失敗"
    top = [
        [f"🎴 {status['label']} ｜ スニダン価格インデックス", "", "", "", f"状態: {icon}"],
        [f"最終更新: {status['ts']}（毎朝 6:20 自動更新）", "", "", "", status.get("detail", "")],
        [f"総 {len(rows):,}件 ／ 毎日更新 {mainichi:,}件（価格>¥3,000）／ 週1巡回 {len(rows)-mainichi:,}件", "", "", "", ""],
    ]
    ws.update("A1", top, value_input_option="RAW")
    matrix = build_matrix(rows)
    for start in range(0, len(matrix), CHUNK):
        ws.update(f"A{HEADER_ROW + start}", matrix[start:start + CHUNK], value_input_option="RAW")
        time.sleep(0.8)
    ss.batch_update({"requests": fmt_requests(ws.id, len(HEADERS), len(rows), status["ok"])})
    setup_filters(ss, ws, len(rows))


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
            recompute(rows)  # CSV baselineから souba_sort等を先に確定(repriceの高額判定に必要)
            if args.reprice:
                tg, ok = reprice(rows); detail = f"価格再取得 {ok}/{tg}"
            recompute(rows)  # 再取得分を反映
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
