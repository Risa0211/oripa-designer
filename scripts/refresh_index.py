"""スニダン価格インデックスを専用スプレッドシートの2タブ(ポケカ/ワンピ)へ書き込む。

- data/index_pokemon.csv / data/index_onepiece.csv (リポジトリ同梱の確定リスト) を読む
- 既存シートの buyout_manual 列(買取チェッカー手動入力)は apparel_id で照合して保全
- 各タブ最上部にステータスヘッダー(最終更新日時/ステータス/件数)を書く
- 初回=ロードのみ / 日次=--reprice で daily_check の PSA10/生を再取得してから書く

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
# シート列(CSVと同じ並び)。status行を上に2行置き、3行目をヘッダ、4行目からデータ。
DATA_COLS = ["brand", "name", "rarity", "item_type", "card_number", "set_code",
             "psa10_price", "raw_price", "snkrdunk_value", "price_source",
             "buyout_manual", "final_value", "daily_check", "url", "apparel_id",
             "product_number", "price_note", "priced_at"]
HEADER_ROW = 3          # 列見出し行
DATA_START = 4          # データ開始行
CHUNK = 5000


def now_jst() -> str:
    return datetime.now(JST).strftime("%Y-%m-%d %H:%M")


def load_csv(game: str) -> list[dict]:
    with open(os.path.join(DATA, CSV_BY_GAME[game]), encoding="utf-8") as f:
        return list(csv.DictReader(f))


def read_existing_buyout(ws) -> dict:
    """既存タブから apparel_id -> buyout_manual を取得(手動入力の保全)。"""
    out = {}
    try:
        vals = ws.get_all_values()
    except Exception:
        return out
    if len(vals) < HEADER_ROW:
        return out
    hdr = vals[HEADER_ROW - 1]
    try:
        i_id = hdr.index("apparel_id"); i_bo = hdr.index("buyout_manual")
    except ValueError:
        return out
    for row in vals[DATA_START - 1:]:
        if len(row) > max(i_id, i_bo) and row[i_id]:
            bo = row[i_bo].strip()
            if bo:
                out[row[i_id]] = bo
    return out


def reprice(rows: list[dict]) -> tuple[int, int]:
    """daily_check=TRUE の行を PSA10/生 で再取得。rows を in-place 更新。"""
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


def recompute(rows: list[dict], buyout_keep: dict):
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


def write_tab(ws, rows: list[dict], status: dict):
    ws.clear()
    label = status["label"]
    daily = sum(1 for r in rows if r["daily_check"] == "TRUE")
    header_block = [
        ["最終更新日時", status["ts"], "", "ステータス", status["state"], status.get("detail", "")],
        [f"{label} 総{len(rows):,}件 / 日次{daily:,} / 非日次{len(rows)-daily:,}",
         "", "", "毎朝 JST 06:00 自動更新（失敗時のみChatwork通知）", "", ""],
    ]
    matrix = [DATA_COLS] + [[r.get(c, "") for c in DATA_COLS] for r in rows]
    # ステータス2行 + 見出し + データ
    ws.update("A1", header_block, value_input_option="RAW")
    # 見出し(3行目)〜データをチャンク書き込み
    for start in range(0, len(matrix), CHUNK):
        chunk = matrix[start:start + CHUNK]
        row0 = HEADER_ROW + start
        ws.update(f"A{row0}", chunk, value_input_option="RAW")
        time.sleep(1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reprice", action="store_true", help="日次: daily_checkを再取得してから書く")
    args = ap.parse_args()

    cli = get_client()
    ss = cli.open_by_key(config.INDEX_SHEET_ID)
    overall_ok = True
    for game, tab in config.INDEX_TABS.items():
        t0 = time.time()
        rows = load_csv(game)
        try:
            ws = ss.worksheet(tab)
        except Exception:
            ws = ss.add_worksheet(title=tab, rows=len(rows) + 10, cols=len(DATA_COLS) + 2)
        buyout_keep = read_existing_buyout(ws)
        repriced = ok = 0
        try:
            if args.reprice:
                repriced, ok = reprice(rows)
            recompute(rows, buyout_keep)
            state, detail = "✅成功", (f"再取得{ok}/{repriced}" if args.reprice else "ロードのみ")
        except Exception as e:
            state, detail, overall_ok = "❌失敗", str(e)[:80], False
            recompute(rows, buyout_keep)
        write_tab(ws, rows, {"label": tab, "ts": now_jst(), "state": state, "detail": detail})
        print(f"[{tab}] {state} {detail} 書込{len(rows)}件 {time.time()-t0:.0f}s", flush=True)
    if not overall_ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
