"""Driveフォルダの画像から有料ガチャ情報を取得してリサーチDBに登録

使い方:
  1. Drive API有効化 + フォルダ共有 (oripa-tool-bot@oripa-tool.iam.gserviceaccount.com)
  2. python3 dopa_image_importer.py --download-only でローカルに画像保存
  3. (人 or Claudeが画像を見て情報抽出) → JSONに記録
  4. python3 dopa_image_importer.py --register entries.json でDB登録

画像から自動抽出は本スクリプトの範囲外（OCR API有効化が別途必要）。
"""
from __future__ import annotations
import argparse
import io
import json
from datetime import datetime
from pathlib import Path
from typing import List, Dict

ROOT = Path(__file__).parent
IMAGES_DIR = ROOT / "data" / "dopa_paid_images"
FOLDER_ID = "1SEcVsTyKcG8LEdRvINfTkv27vpIUcW6C"


def download_all_images(folder_id: str = FOLDER_ID) -> List[Dict]:
    """フォルダ内の全画像をローカルにDLしてメタ情報を返す"""
    from google.oauth2.service_account import Credentials
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaIoBaseDownload
    import config

    IMAGES_DIR.mkdir(parents=True, exist_ok=True)

    creds = Credentials.from_service_account_file(config.CREDENTIALS_PATH, scopes=config.SCOPES)
    drive = build("drive", "v3", credentials=creds)

    # ファイル列挙(画像のみ)
    files: List[Dict] = []
    page_token = None
    while True:
        resp = drive.files().list(
            q=f"'{folder_id}' in parents and trashed=false and mimeType contains 'image/'",
            fields="nextPageToken, files(id,name,mimeType,size,createdTime)",
            pageToken=page_token,
            pageSize=200,
        ).execute()
        files.extend(resp.get("files", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    print(f"画像 {len(files)} 件発見")

    downloaded = []
    for i, f in enumerate(files, 1):
        local = IMAGES_DIR / f["name"]
        if local.exists() and local.stat().st_size > 0:
            print(f"  [{i}/{len(files)}] (skip) {f['name']}")
            downloaded.append({"id": f["id"], "name": f["name"], "path": str(local)})
            continue
        req = drive.files().get_media(fileId=f["id"])
        buf = io.BytesIO()
        dl = MediaIoBaseDownload(buf, req)
        done = False
        while not done:
            _, done = dl.next_chunk()
        local.write_bytes(buf.getvalue())
        print(f"  [{i}/{len(files)}] ✅ {f['name']} ({len(buf.getvalue())//1024}KB)")
        downloaded.append({"id": f["id"], "name": f["name"], "path": str(local)})

    # メタJSON出力
    meta_path = IMAGES_DIR / "_index.json"
    meta_path.write_text(json.dumps(downloaded, ensure_ascii=False, indent=2))
    print(f"インデックス: {meta_path}")
    return downloaded


def register_from_json(entries_path: Path):
    """JSON(配列)から有料ガチャ一覧 + 景品明細にupsert

    JSONフォーマット:
    [
      {
        "product_id": "DOPA-???",         (なければ自動生成)
        "title": "○○課金者限定オリパ",
        "url": "https://dopa-game.jp/...",  (画像から取れなければ空)
        "price": 1000,                      (1回pt)
        "total_tickets": 100,
        "charge_amount": 5000,              (引く権利の事前課金額・円)
        "note": "...",
        "prizes": [
          {"rank":"S賞","name":"...","rarity":"...","quantity":1,"point":50000},
          ...
        ]
      }, ...
    ]
    """
    import sys; sys.path.insert(0, str(ROOT))
    from research import (
        PremiumGacha, PrizeCard,
        bulk_upsert_premium_gachas, save_premium_gacha_prizes,
    )

    entries = json.loads(Path(entries_path).read_text(encoding="utf-8"))
    today = datetime.now().strftime("%Y-%m-%d")

    pgachas: List[PremiumGacha] = []
    for e in entries:
        pid = e.get("product_id") or f"DOPA-PAID-{e['title'][:20]}"
        pgachas.append(PremiumGacha(
            product_id=pid, site="DOPA",
            title=e["title"], url=e.get("url", ""),
            price=int(e.get("price") or 0),
            total_tickets=int(e.get("total_tickets") or 0),
            card_types=len(e.get("prizes", [])),
            charge_amount=int(e.get("charge_amount") or 0),
            note=e.get("note") or f"画像取込({today})",
            updated_at="",
        ))
    bulk_upsert_premium_gachas(pgachas)
    print(f"✅ 有料ガチャ一覧 {len(pgachas)}件 upsert")

    # 各商品の景品明細
    for e in entries:
        pid = e.get("product_id") or f"DOPA-PAID-{e['title'][:20]}"
        prizes = e.get("prizes", [])
        if not prizes:
            continue
        cards = [
            PrizeCard(seq=i + 1, tier=p.get("rank", ""), card_name=p.get("name", ""),
                      rarity=p.get("rarity", ""), qty=int(p.get("quantity", 0)))
            for i, p in enumerate(prizes)
        ]
        save_premium_gacha_prizes(pid, cards)
        print(f"  ✅ {pid}: 景品明細 {len(cards)}件")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--download-only", action="store_true",
                        help="Driveから画像ダウンロードのみ")
    parser.add_argument("--register", type=str,
                        help="JSONファイルから有料ガチャをDB登録")
    args = parser.parse_args()

    if args.download_only:
        download_all_images()
    elif args.register:
        register_from_json(Path(args.register))
    else:
        # デフォルト: DL→（人が画像を見てJSON書く）→registerの流れを促す
        print(__doc__)
