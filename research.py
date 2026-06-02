"""競合リサーチシート読込"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import List, Optional

import config
from sheets_client import open_research, parse_int, get_or_create_tab


TIER_COLS = ["1等", "2等", "3等", "4等", "5等", "6等", "7等", "キリ番", "ラストワン"]


@dataclass
class Reference:
    no: str
    title: str
    url: str
    price_per_coin: int
    total_tickets: int
    sold_date: str
    tags: str
    tiers: dict  # {"1等": "カード名/...", ...} 空のものは除外


@lru_cache(maxsize=1)
def load_all_references() -> List[Reference]:
    res = _retry(open_research)
    ws = _retry(lambda: res.worksheet(config.TAB_RESEARCH))
    values = _retry(lambda: ws.get_all_values())
    if not values:
        return []
    headers = values[0]

    def col(name):
        return headers.index(name) if name in headers else -1

    c_no = col("No.")
    c_title = col("タイトル")
    c_url = col("商品URL")
    c_price = col("価格(coin)/1回")
    c_total = col("総口数")
    c_sold = col("完売日時")
    c_tags = col("タグ")
    tier_idx = {t: col(t) for t in TIER_COLS}

    refs: List[Reference] = []
    for row in values[1:]:
        def g(i):
            return row[i] if 0 <= i < len(row) else ""
        no = g(c_no).strip()
        title = g(c_title).strip()
        if not no or not title:
            continue
        tiers = {}
        for t, ci in tier_idx.items():
            if ci < 0:
                continue
            v = g(ci).strip()
            if v:
                tiers[t] = v
        refs.append(Reference(
            no=no,
            title=title,
            url=g(c_url),
            price_per_coin=parse_int(g(c_price)) or 0,
            total_tickets=parse_int(g(c_total)) or 0,
            sold_date=g(c_sold),
            tags=g(c_tags),
            tiers=tiers,
        ))
    return refs


def find_reference(no: str) -> Optional[Reference]:
    no = str(no).strip()
    for r in load_all_references():
        if r.no == no:
            return r
    return None


def count_cards_in_tier(tier_text: str) -> int:
    """等級テキストから推定カード枚数（'/' 区切り、'xN' 表記も考慮）"""
    if not tier_text:
        return 0
    parts = [p.strip() for p in tier_text.split("/") if p.strip()]
    total = 0
    import re
    for p in parts:
        m = re.search(r"x\s*(\d+)", p)
        total += int(m.group(1)) if m else 1
    return total


# ============================================================
# 景品明細 (ローカルParquet・読み取り専用)
# ============================================================

@dataclass
class PrizeCard:
    """1枚分の景品明細"""
    seq: int
    tier: str          # "1等" "ラストワン" など
    card_name: str
    rarity: str
    qty: int           # 本数


@dataclass
class DesignTemplate:
    """商品Noに紐づくテンプレ（一覧情報 + カード明細）"""
    no: int
    title: str
    price: int            # 単価(円)
    total_tickets: int    # 総口数
    card_types: int       # カード種類数
    url: str              # 商品ページURL（リサーチDBから）
    cards: List[PrizeCard] = field(default_factory=list)


@lru_cache(maxsize=1)
def _load_prize_details_df():
    """景品明細Parquetを読み込み（プロセス内キャッシュ）"""
    import pandas as pd
    p = Path(config.PRIZE_DETAILS_PARQUET)
    if not p.exists():
        return pd.DataFrame(columns=["no", "seq", "tier", "card_name", "rarity", "qty"])
    return pd.read_parquet(p)


@lru_cache(maxsize=1)
def _load_reference_index_by_no():
    """No → Reference の辞書"""
    return {r.no: r for r in load_all_references()}


def _parse_tier_text_to_cards(tier: str, text: str, start_seq: int) -> List[PrizeCard]:
    """リサーチDBの等列テキスト 'カード名 [レア] xN / カード名 [レア] / ...' をパース"""
    import re
    out = []
    if not text:
        return out
    seq = start_seq
    for part in text.split("/"):
        p = part.strip()
        if not p:
            continue
        # xN を抽出
        m_qty = re.search(r"x\s*(\d+)\s*$", p)
        qty = int(m_qty.group(1)) if m_qty else 1
        if m_qty:
            p = p[:m_qty.start()].strip()
        # [レア] を抽出
        m_rar = re.search(r"\[([^\]]+)\]\s*$", p)
        rarity = m_rar.group(1).strip() if m_rar else ""
        if m_rar:
            p = p[:m_rar.start()].strip()
        out.append(PrizeCard(seq=seq, tier=tier, card_name=p, rarity=rarity, qty=qty))
        seq += 1
    return out


def load_design_template(no) -> Optional[DesignTemplate]:
    """商品No.からテンプレ(一覧情報 + カード明細)を取得

    景品明細(Parquet) を優先。なければリサーチDBの各等列をフォールバック。
    """
    try:
        no_int = int(str(no).strip())
    except (ValueError, TypeError):
        return None

    df = _load_prize_details_df()
    rows = df[df["no"] == no_int].sort_values("seq")

    refs = _load_reference_index_by_no()
    ref = refs.get(str(no_int))
    title = ref.title if ref else ""
    url = ref.url if ref else ""
    price = ref.price_per_coin if ref else 0
    total_tickets = ref.total_tickets if ref else 0

    cards = [
        PrizeCard(
            seq=int(r["seq"]) if r["seq"] is not None else 0,
            tier=str(r["tier"] or ""),
            card_name=str(r["card_name"] or ""),
            rarity=str(r["rarity"] or ""),
            qty=int(r["qty"] or 0),
        )
        for _, r in rows.iterrows()
    ]

    # フォールバック1: 景品明細がなくリサーチDBにtiers情報があればパース
    if not cards and ref and ref.tiers:
        seq = 1
        for t in TIER_COLS:
            text = ref.tiers.get(t, "")
            parsed = _parse_tier_text_to_cards(t, text, seq)
            cards.extend(parsed)
            seq += len(parsed)

    # フォールバック2: ParquetもリサーチDBもtiers無い場合、japan-toreca.com APIから直接取得
    if not cards and ref and ref.url:
        try:
            import re as _re
            m = _re.search(r"japan-toreca\.com/oripa/[\w-]+/(\d+)", ref.url)
            if m:
                from torecacenter_scraper import fetch_detail, extract_cards_from_detail
                detail = fetch_detail(m.group(1))
                if detail:
                    api_cards = extract_cards_from_detail(detail)
                    # API応答にカード明細あれば変換
                    rank_to_label = {
                        "1等": "1等", "2等": "2等", "3等": "3等", "4等": "4等",
                        "5等": "5等", "6等": "6等", "7等": "7等",
                        "キリ番": "キリ番", "ラストワン": "ラストワン",
                    }
                    for c in api_cards:
                        cards.append(PrizeCard(
                            seq=c["seq"], tier=rank_to_label.get(c["rank"], c["rank"]),
                            card_name=c["name"], rarity=c["rarity"],
                            qty=int(c["quantity"] or 0),
                        ))
                    # APIから単価/総口数も埋め直す(リサーチDB値より新しい可能性)
                    if not price and detail.get("price"):
                        price = int(detail["price"])
                    if not total_tickets and detail.get("total_cards"):
                        total_tickets = int(detail["total_cards"])
        except Exception:
            pass

    if not cards and not ref:
        return None  # 完全未登録

    return DesignTemplate(
        no=no_int,
        title=title,
        price=price,
        total_tickets=total_tickets,
        card_types=len(set((c.card_name, c.rarity) for c in cards)),
        url=url,
        cards=cards,
    )


# ============================================================
# 有料ガチャ一覧 / 新規ガチャ一覧 (Google Sheets)
# ============================================================

@dataclass
class PremiumGacha:
    product_id: str
    site: str
    title: str
    url: str
    price: int
    total_tickets: int
    card_types: int
    charge_amount: int    # 引く権利の事前課金額(円)
    note: str
    updated_at: str


@dataclass
class NewGacha:
    no: str
    site: str
    title: str
    url: str
    price: int
    total_tickets: int
    new_period: str       # "登録後7日" など
    registered_at: str
    note: str
    updated_at: str


def _open_premium_tab():
    return get_or_create_tab(open_research(), config.TAB_PREMIUM_GACHA, config.PREMIUM_GACHA_HEADERS)


def _open_new_gacha_tab():
    return get_or_create_tab(open_research(), config.TAB_NEW_GACHA, config.NEW_GACHA_HEADERS)


def load_premium_gachas() -> List[PremiumGacha]:
    ws = _retry(_open_premium_tab)
    rows = _retry(lambda: ws.get_all_records())
    out = []
    for r in rows:
        if not r.get("商品ID") and not r.get("タイトル"):
            continue
        out.append(PremiumGacha(
            product_id=str(r.get("商品ID", "")),
            site=str(r.get("サイト", "")),
            title=str(r.get("タイトル", "")),
            url=str(r.get("商品URL", "")),
            price=parse_int(r.get("単価(円)")) or 0,
            total_tickets=parse_int(r.get("総口数")) or 0,
            card_types=parse_int(r.get("カード種数")) or 0,
            charge_amount=(parse_int(r.get("引く権利の事前課金額(円)"))
                           or parse_int(r.get("課金額(pt買い増し相当)"))  # 旧名称フォールバック
                           or 0),
            note=str(r.get("備考", "")),
            updated_at=str(r.get("更新日時", "")),
        ))
    return out


def load_new_gachas() -> List[NewGacha]:
    ws = _retry(_open_new_gacha_tab)
    rows = _retry(lambda: ws.get_all_records())
    out = []
    for r in rows:
        if not r.get("No") and not r.get("タイトル"):
            continue
        out.append(NewGacha(
            no=str(r.get("No", "")),
            site=str(r.get("サイト", "")),
            title=str(r.get("タイトル", "")),
            url=str(r.get("商品URL", "")),
            price=parse_int(r.get("単価(円)")) or 0,
            total_tickets=parse_int(r.get("総口数")) or 0,
            new_period=str(r.get("新規限定期間", "")),
            registered_at=str(r.get("登録日", "")),
            note=str(r.get("備考", "")),
            updated_at=str(r.get("更新日時", "")),
        ))
    return out


def upsert_premium_gacha(g: PremiumGacha):
    """商品IDで既存検索→更新 or 新規追加"""
    ws = _open_premium_tab()
    all_vals = ws.get_all_values()
    headers = all_vals[0] if all_vals else config.PREMIUM_GACHA_HEADERS
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    row = [
        g.product_id, g.site, g.title, g.url,
        g.price, g.total_tickets, g.card_types,
        g.charge_amount, g.note, now,
    ]
    target_row = None
    for i, vals in enumerate(all_vals[1:], start=2):
        if vals and vals[0] == g.product_id:
            target_row = i
            break
    if target_row:
        ws.update([row], f"A{target_row}:J{target_row}", value_input_option="USER_ENTERED")
    else:
        ws.append_row(row, value_input_option="USER_ENTERED")


def _retry(fn, max_tries: int = 4, base_sleep: float = 3.0):
    """指数バックオフ付きリトライ。クォータ系エラーのみリトライ、それ以外は即throw"""
    import time as _t
    last = None
    for t in range(max_tries):
        try:
            return fn()
        except Exception as e:
            msg = str(e).lower()
            is_quota = "429" in str(e) or "quota" in msg or "rate" in msg
            last = e
            if not is_quota and t == 0:
                # クォータ以外は即上げる(認証エラー等で延々待たない)
                raise
            _t.sleep(base_sleep * (2 ** t))
    raise last


def bulk_upsert_premium_gachas(gachas: List[PremiumGacha]):
    """大量データ用: シートを1回読み込み→メモリ上で更新→1回書き込み"""
    ws = _retry(_open_premium_tab)
    all_vals = _retry(lambda: ws.get_all_values())
    headers = all_vals[0] if all_vals else config.PREMIUM_GACHA_HEADERS
    body = all_vals[1:] if len(all_vals) > 1 else []
    # product_id → row idx
    existing_idx = {v[0]: i for i, v in enumerate(body) if v and len(v) > 0}
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for g in gachas:
        row = [
            g.product_id, g.site, g.title, g.url,
            g.price, g.total_tickets, g.card_types,
            g.charge_amount, g.note, now,
        ]
        # pad to 10列
        row = [str(x) if x is not None else "" for x in row]
        if g.product_id in existing_idx:
            body[existing_idx[g.product_id]] = row
        else:
            existing_idx[g.product_id] = len(body)
            body.append(row)
    # 1回で書き込み
    _retry(lambda: ws.clear())
    _retry(lambda: ws.update([headers] + body, "A1", value_input_option="USER_ENTERED"))


def bulk_upsert_new_gachas(gachas: List[NewGacha]):
    """大量データ用: 新規ガチャ一覧の一括upsert"""
    ws = _retry(_open_new_gacha_tab)
    all_vals = _retry(lambda: ws.get_all_values())
    headers = all_vals[0] if all_vals else config.NEW_GACHA_HEADERS
    body = all_vals[1:] if len(all_vals) > 1 else []
    existing_idx = {f"{v[0]}|{v[1]}" if len(v) >= 2 else v[0] if v else "": i
                    for i, v in enumerate(body) if v}
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for g in gachas:
        row = [
            g.no, g.site, g.title, g.url,
            g.price, g.total_tickets, g.new_period,
            g.registered_at, g.note, now,
        ]
        row = [str(x) if x is not None else "" for x in row]
        key = f"{g.no}|{g.site}"
        if key in existing_idx:
            body[existing_idx[key]] = row
        else:
            existing_idx[key] = len(body)
            body.append(row)
    _retry(lambda: ws.clear())
    _retry(lambda: ws.update([headers] + body, "A1", value_input_option="USER_ENTERED"))


def upsert_new_gacha(g: NewGacha):
    """NoとサイトのペアでUpsert"""
    ws = _open_new_gacha_tab()
    all_vals = ws.get_all_values()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    row = [
        g.no, g.site, g.title, g.url,
        g.price, g.total_tickets, g.new_period,
        g.registered_at, g.note, now,
    ]
    target_row = None
    for i, vals in enumerate(all_vals[1:], start=2):
        if len(vals) >= 2 and vals[0] == g.no and vals[1] == g.site:
            target_row = i
            break
    if target_row:
        ws.update([row], f"A{target_row}:J{target_row}", value_input_option="USER_ENTERED")
    else:
        ws.append_row(row, value_input_option="USER_ENTERED")


def delete_premium_gacha(product_id: str):
    ws = _open_premium_tab()
    all_vals = ws.get_all_values()
    for i, vals in enumerate(all_vals[1:], start=2):
        if vals and vals[0] == product_id:
            ws.delete_rows(i)
            return


def delete_new_gacha(no: str, site: str):
    ws = _open_new_gacha_tab()
    all_vals = ws.get_all_values()
    for i, vals in enumerate(all_vals[1:], start=2):
        if len(vals) >= 2 and vals[0] == no and vals[1] == site:
            ws.delete_rows(i)
            return


# ============================================================
# カードマスタ (カード名+レア → snkrdunk URL + 買取価格キャッシュ)
# ============================================================

@dataclass
class CardMaster:
    name: str
    rarity: str
    snkrdunk_url: str
    buy_price: int
    source: str
    updated_at: str

    @property
    def key(self) -> str:
        return f"{self.name}|{self.rarity}".lower()


def _open_card_master_tab():
    return get_or_create_tab(open_research(), config.TAB_CARD_MASTER, config.CARD_MASTER_HEADERS)


@lru_cache(maxsize=1)
def load_card_master_index() -> dict:
    """カードマスタを辞書化（キー = カード名|レア の小文字）"""
    try:
        ws = _open_card_master_tab()
        rows = ws.get_all_records()
    except Exception:
        return {}
    out = {}
    for r in rows:
        name = str(r.get("カード名", "")).strip()
        if not name:
            continue
        rarity = str(r.get("レアリティ", "")).strip()
        cm = CardMaster(
            name=name, rarity=rarity,
            snkrdunk_url=str(r.get("snkrdunk URL", "")).strip(),
            buy_price=parse_int(r.get("買取価格(円)")) or 0,
            source=str(r.get("価格取得元", "")),
            updated_at=str(r.get("価格更新日時", "")),
        )
        out[cm.key] = cm
    return out


def clear_card_master_cache():
    """マスタ書込み後にキャッシュをクリア"""
    load_card_master_index.cache_clear()


def upsert_card_master(cm: CardMaster):
    """カード名+レアでupsert"""
    ws = _open_card_master_tab()
    all_vals = ws.get_all_values()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    row = [cm.name, cm.rarity, cm.snkrdunk_url, cm.buy_price, cm.source, now]
    target = None
    for i, v in enumerate(all_vals[1:], start=2):
        if (len(v) >= 2 and v[0].strip().lower() == cm.name.lower()
                and v[1].strip().lower() == cm.rarity.lower()):
            target = i
            break
    if target:
        ws.update([row], f"A{target}:F{target}", value_input_option="USER_ENTERED")
    else:
        ws.append_row(row, value_input_option="USER_ENTERED")
    clear_card_master_cache()


def find_card_in_master(name: str, rarity: str) -> Optional[CardMaster]:
    key = f"{name}|{rarity}".lower()
    return load_card_master_index().get(key)


def snkrdunk_search_url(name: str, rarity: str = "") -> str:
    """スニダン検索ページURLを生成"""
    from urllib.parse import quote
    q = (name + (" " + rarity if rarity else "")).strip()
    return f"https://snkrdunk.com/apparels?keyword={quote(q)}"


# ============================================================
# 有料ガチャ景品明細 (DOPA等)
# ============================================================

def _open_premium_prizes_tab():
    return get_or_create_tab(open_research(), config.TAB_PREMIUM_PRIZES, config.PREMIUM_PRIZES_HEADERS)


def load_premium_gacha_prizes(product_id: str) -> List[PrizeCard]:
    """有料ガチャ商品IDに紐づく景品明細を取得"""
    ws = _open_premium_prizes_tab()
    rows = ws.get_all_records()
    out = []
    for r in rows:
        if str(r.get("商品ID", "")).strip() != product_id:
            continue
        out.append(PrizeCard(
            seq=parse_int(r.get("seq")) or 0,
            tier=str(r.get("賞", "")),
            card_name=str(r.get("カード名", "")),
            rarity=str(r.get("レアリティ", "")),
            qty=parse_int(r.get("本数")) or 0,
        ))
    return out


# ============================================================
# DOPA商品一覧 (過去現在の全商品の参考DB)
# ============================================================

@dataclass
class DopaProduct:
    product_id: str       # "DOPA-285731"
    category: str         # "pokemon"
    title: str
    url: str
    price: int            # 単価(pt)
    total_tickets: int    # 総口数
    remaining: int        # 残口数
    has_last_one: bool
    min_point: int        # 最低保証pt
    limit_day: int        # 期限(日)
    limit_quantity: int   # 制限数量
    pull_restriction: bool
    rank_restriction: bool
    user_group_restriction: bool
    is_new_gacha: bool
    is_paid_gacha: bool
    status: str
    note: str
    updated_at: str


def _open_dopa_tab():
    return get_or_create_tab(open_research(), config.TAB_DOPA_PRODUCTS, config.DOPA_PRODUCTS_HEADERS)


def load_dopa_products() -> List[DopaProduct]:
    try:
        ws = _retry(_open_dopa_tab)
        rows = _retry(lambda: ws.get_all_records())
    except Exception:
        return []
    out = []
    for r in rows:
        if not r.get("商品ID"):
            continue
        out.append(DopaProduct(
            product_id=str(r.get("商品ID", "")),
            category=str(r.get("カテゴリ", "")),
            title=str(r.get("タイトル", "")),
            url=str(r.get("商品URL", "")),
            price=parse_int(r.get("単価(pt)")) or 0,
            total_tickets=parse_int(r.get("総口数")) or 0,
            remaining=parse_int(r.get("残口数")) or 0,
            has_last_one=str(r.get("ラストワン", "")).strip().lower() in ("true", "yes", "○", "あり", "1"),
            min_point=parse_int(r.get("最低保証pt")) or 0,
            limit_day=parse_int(r.get("期限(日)")) or 0,
            limit_quantity=parse_int(r.get("制限数量")) or 0,
            pull_restriction=str(r.get("プル制限", "")).strip().lower() in ("true", "yes", "○", "あり", "1"),
            rank_restriction=str(r.get("ランク制限", "")).strip().lower() in ("true", "yes", "○", "あり", "1"),
            user_group_restriction=str(r.get("ユーザーグループ制限", "")).strip().lower() in ("true", "yes", "○", "あり", "1"),
            is_new_gacha=str(r.get("新規ガチャ判定", "")).strip().lower() in ("true", "yes", "○", "あり", "1"),
            is_paid_gacha=str(r.get("有料ガチャ判定", "")).strip().lower() in ("true", "yes", "○", "あり", "1"),
            status=str(r.get("ステータス", "")),
            note=str(r.get("備考", "")),
            updated_at=str(r.get("更新日時", "")),
        ))
    return out


def bulk_upsert_dopa_products(products: List[DopaProduct]):
    """大量データ用: DOPA商品一覧をまとめてupsert"""
    ws = _retry(_open_dopa_tab)
    all_vals = _retry(lambda: ws.get_all_values())
    headers = all_vals[0] if all_vals else config.DOPA_PRODUCTS_HEADERS
    body = all_vals[1:] if len(all_vals) > 1 else []
    existing_idx = {v[0]: i for i, v in enumerate(body) if v and len(v) > 0}
    for p in products:
        row = [
            p.product_id, p.category, p.title, p.url,
            p.price, p.total_tickets, p.remaining,
            "○" if p.has_last_one else "",
            p.min_point, p.limit_day, p.limit_quantity,
            "○" if p.pull_restriction else "",
            "○" if p.rank_restriction else "",
            "○" if p.user_group_restriction else "",
            "○" if p.is_new_gacha else "",
            "○" if p.is_paid_gacha else "",
            p.status, p.note, p.updated_at,
        ]
        row = [str(x) if x is not None else "" for x in row]
        if p.product_id in existing_idx:
            body[existing_idx[p.product_id]] = row
        else:
            existing_idx[p.product_id] = len(body)
            body.append(row)
    _retry(lambda: ws.clear())
    _retry(lambda: ws.update([headers] + body, "A1", value_input_option="USER_ENTERED"))


def save_premium_gacha_prizes(product_id: str, cards: List[PrizeCard], snkrdunk_urls: List[str] = None):
    """有料ガチャの景品明細を一括登録(既存はクリアして全置換)"""
    ws = _open_premium_prizes_tab()
    all_vals = ws.get_all_values()
    # 既存の同商品IDを行ごと削除
    to_delete = []
    for i, v in enumerate(all_vals[1:], start=2):
        if v and str(v[0]).strip() == product_id:
            to_delete.append(i)
    for i in reversed(to_delete):
        ws.delete_rows(i)
    # 新規追加
    if not snkrdunk_urls:
        snkrdunk_urls = [""] * len(cards)
    new_rows = []
    for i, c in enumerate(cards):
        url = snkrdunk_urls[i] if i < len(snkrdunk_urls) else ""
        new_rows.append([product_id, i + 1, c.tier, c.card_name, c.rarity, c.qty, url, ""])
    if new_rows:
        ws.append_rows(new_rows, value_input_option="USER_ENTERED")
