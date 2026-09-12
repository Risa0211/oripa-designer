# gacha-image — 景品画像の道具

ガチャ設計が確定したあと、**景品画像をそろえて保管庫へ上げる**までの道具。
手順の全体と判断基準は `../HANDOFF_景品画像とCSV.md`（Claude Code に読ませる仕様）。

## 使い方

案件ごとに作業ディレクトリを1つ作り、`--work` で渡す。**ファイル名に案件番号は要らない。**

```bash
WORK=~/work/batch12
mkdir -p $WORK
# rows.json と meta.json を設計側の道具から置く

G=~/oripa-designer/gacha-image
python3 $G/snkr.py    --work $WORK     # ① スニダン正典     → snkr.json
python3 $G/match.py   --work $WORK     # ② 保管庫と照合     → match.json / match.log
#    ★ここで match.log を見て、全種を目視する（§3）
#    ★足りない分を調達し、pick.json を書く
python3 $G/final.py   --work $WORK     # ③ 採用確定・目視シート → final.json / sheets/
python3 $G/upload.py  --work $WORK     # ④ 空撃ち（対象と台帳の行き先を表示）
python3 $G/upload.py  --work $WORK --go --label "第12陣"   # ④ 本番
python3 $G/gallery.py --work $WORK     # ⑤ 確認ギャラリー   → gallery/
cd $WORK/gallery && python3 -m http.server 8778
python3 $G/thumbs.py  --work $WORK     # ⑥ サムネ依頼用の画像 → thumbs/
```

## 作業ディレクトリの中身

| ファイル | 誰が作るか |
|---|---|
| `rows.json` | 設計側の道具（採用カードの一覧。各行に `aid` と `name`、あれば `rank`） |
| `meta.json` | 設計側の道具（本ごとの `{"title": "..."}`） |
| `pick.json` | **人**（目視の結果。下を参照。無くても動く） |
| `cand/snkr_{ID}.png` | 人（スニダン原寸を使うときだけ） |
| `snkr.json` / `match.json` / `match.log` / `final.json` / `uploaded.json` | 道具が作る |
| `sheets/` / `gallery/` / `thumbs/` | 道具が作る |

## pick.json（目視の結果を書く）

apparel ID をキーにする。**書かなかったカードは照合の1位が自動で採用される。**

```json
{
  "159664": {"reuse": "https://minnano-toreka.com/wp-content/uploads/2026/09/xxx.webp", "wp_id": 32579},
  "766293": {"url": "https://www.cardrush-op.jp/data/cardrush-op/product/xxx.jpg",
             "src": "cardrush-op", "note": "上位記念品"},
  "128147": {"url": "snkr", "src": "snkrdunk", "note": "スニダン原寸をトリム"}
}
```

- `reuse` … 保管庫に既に正しい版がある（**再アップしない**）
- `url` … 新しく調達した画像（**保管庫へアップする**）
- `url` が `"snkr"` … `cand/snkr_{ID}.png` を読む

調達元のURLの形:

| 取得元 | プレフィックス |
|---|---|
| カードラッシュ op | `https://www.cardrush-op.jp/data/cardrush-op/product/` |
| カードラッシュ pokemon | `https://www.cardrush-pokemon.jp/data/cardrushpokemon/product/`（**ハイフン無し**） |
| ポケカ公式 | `https://www.pokemon-card.com/assets/images/card_images/large/` |

## サムネ依頼用の画像（`thumbs.py`）

デザイナーへ渡す一式を本ごとのフォルダにまとめる。

```
{work}/thumbs/{ガチャ名 訴求}/
    _一覧.txt                        1行目=ガチャ名、以降「等級 カード名 ×枚数」
    01_1等_カード名 [型番].webp
    02_ラストワン賞_…
    03_2等_…
```

- 対象は **apparel ID のある実カードだけ**（PT交換専用・最低保証は入れない）
- 並びは設計の順。同じカードが複数行にあるときは1枚だけ
- フォルダ名はガチャ名から `!!` を外し、`/` を `-` にしたもの
- 出力先を変えるなら `--out ~/Downloads/{案件名}_サムネ用当たりカード`

## 判定の基準（`match.py` の定数）

| 定数 | 値 | 意味 |
|---|---|---|
| `GOOD_DIST` | 70 | dHash の差分の上限。超えると「★別カード」 |
| `GOOD_WIDTH` | 500 | 幅の下限。下回ると「★幅NNN」 |
| `GOOD_AR` | 0.66〜0.76 | 縦横比。外れると「★比0.XX」（トレカの正は約0.715） |

★**不合格でも候補リストには残る。** 他に候補が無ければ `final` がそれを採用してしまうので、
`match.log` に出たものは必ず目視で処理すること。

## 環境

| 要るもの | 既定 |
|---|---|
| 台帳 | `../gacha-csv-builder/master_db_*.csv`（リポジトリ内） |
| 追加の台帳 | `~/minnatoreca-gacha-csv/` があれば自動で使う。変えるなら環境変数 `MINNATORECA_CSV_DIR` |
| 保管庫の認証 | 環境変数 `WP_USER` / `WP_APP_PASS`、無ければ `~/.wp_env` |

★`upload.py` は台帳を**リポジトリ内と外部の両方**に追記する。
**1つでも漏れると画像URLが404になる**ため、空撃ちで行き先を確認してから `--go` を付けること。

## 依存

`requests` / `Pillow`。リポジトリ内の `snkrdunk_client.py` と `gacha-csv-builder/wp_client.py` を使う。
