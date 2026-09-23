"""手で書かれた商品名（管理画面のカード名・画像台帳のカード名・設計シートの賞品名）が
スニダンのどの商品かを当て、みんトレ表記に寄せる。

★機械で決めつけない: 候補が1つに絞れないもの・名前が食い違うものは「要確認」にして候補を並べる。
   人が選んだ結果は data/mintore_overrides.csv（元の表記, apparel_id）に書けば、次回からそれを使う。

使い方（モジュール）:
    R = Resolver.load()           # data/index_*.csv と data/official_names.csv を読む
    R.resolve("カスミのラプラス【AR】{072/063} [SV9a]")
      → {"status": "確定"|"要確認"|"スニダン外"|"不明", "apparel_id", "mintore_name", "cands": [...], "why"}
"""
from __future__ import annotations
import csv, os, re, sys, unicodedata

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from mintore_name import mintore_name

DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
GAMES = ("pokemon", "onepiece")
SEALED_GAMES = ("pokemon", "onepiece", "yugioh", "dragonball")   # BOX・パックの名前照合は全ゲームから
OVERRIDES = os.path.join(DATA, "mintore_overrides.csv")

# スニダンに無い商品（ポイント・演出・家電）。これらはスニダン一覧の外で表記を決める
NON_SNKR = re.compile(r"pt交換|ポイント交換|^\d[\d,]*\s*pt|なにかの|iphone|ipad|airpods|macbook|apple ?watch|switch|playstation|"
                      r"ps5|ギフト|amazon|チケット|福袋|ハズレ|はずれ|最低保証|演出", re.I)
RARS = ["SAR", "SIR", "CSR", "CHR", "MUR", "BWR", "SSR", "RRR", "ACE", "SEC", "AR", "SR", "HR", "UR", "RR", "MA",
        "UC", "TR", "PR", "SP", "L", "R", "C", "U", "K", "A", "S", "P", "プロモ", "PROMO"]
VARIANT_WORDS = ["マスターボールミラー", "モンスターボールミラー", "ボールミラー", "ミラー", "エラー", "箔押し", "中国語", "韓国語",
                 "パラレル", "コミパラ", "1ed", "初版", "再販", "未開封"]
EDITION_WORDS = {"1ed", "初版", "再販"}   # 版の印。手書きにあれば加点、無くても減点しない（その版しか無いカードが多い）
OP_VAR = re.compile(r"\b(SEC|SR|R|UC|C|L|P|SP|TR)(-(?:P|SP|SPC|PR|MR))?\b")


def nfkc(s):
    return unicodedata.normalize("NFKC", str(s or ""))


def nk(s):
    """名前比較用: NFKC・アクセント除去（Pokémon→pokemon）・空白/中黒/記号除去・小文字"""
    s = "".join(ch for ch in unicodedata.normalize("NFKD", nfkc(s)) if not unicodedata.combining(ch))
    s = nfkc(s).lower()
    s = re.sub(r"[\s・･\-‐_:：.,、。'\"!！?？☆★◇◆]", "", s)
    return s


def set_of(r):
    s = (r.get("set_code") or "").strip()
    if s:
        return s.upper()
    m = re.match(r"pkmn-tcg-(?:en-)?([A-Za-z0-9+]+(?:-P)?)-", r.get("product_number") or "")
    return m.group(1).upper() if m else ""


_OFF_SN = re.compile(r"\[\s*([A-Za-z0-9+\-]+)\s+(\d{1,3})(?:\s*/\s*([0-9A-Za-z\-]+))?\s*\]")


def num_key_of(r, game):
    """★スニダン正式名の [SET NUM] があればそれを正とする（index の set_code が欠けているプロモ等を救う）"""
    off = r.get("official_name") or r.get("_official") or ""
    if game == "pokemon" and off:
        m = _OFF_SN.search(nfkc(off))
        if m:
            st, no, den = m.group(1).upper(), int(m.group(2)), (m.group(3) or "").upper()
            if den:
                return f"{no:03d}/{den}"
            if st.endswith("-P"):
                return f"{no:03d}/{st}"
    n = nfkc(r.get("card_number")).strip().upper().replace(" ", "")
    if game == "onepiece":
        m = re.search(r"([A-Z]{2,4}\d{0,2})-(\d{3})", n)
        return f"{m.group(1)}-{m.group(2)}" if m else ""
    if re.fullmatch(r"\d{1,3}/[0-9A-Z-]+", n):
        a, b = n.split("/")
        return f"{int(a):03d}/{b}"
    if re.fullmatch(r"\d{1,3}", n) and set_of(r).endswith("-P"):
        return f"{int(n):03d}/{set_of(r)}"
    return ""


def parse_title(t):
    """手書きの商品名から 型番・セット・レア・名前の頭 を拾う"""
    s = nfkc(t).replace("｛", "{").replace("｝", "}")
    s = re.sub(r"〔[^〕]*〕", " ", s).strip()   # 〔※状態難/PSA10鑑定済〕 のような頭書き
    up = s.upper()
    num = ""; game = ""
    m = re.search(r"\b((?:OP|ST|EB|PRB|P)\d{0,2})\s*-\s*(\d{3})\b", up)
    if m:
        num, game = f"{m.group(1)}-{m.group(2)}", "onepiece"
    if not num:
        m = re.search(r"(\d{1,3})\s*/\s*(\d{2,3})(?!\d)", up)
        if m:
            num, game = f"{int(m.group(1)):03d}/{m.group(2).zfill(3)}", "pokemon"
    if not num:
        m = (re.search(r"(?<![A-Z0-9])(\d{1,3})\s*/\s*([A-Z][A-Z0-9]{0,4}-P)\b", up)
             or re.search(r"(?<![A-Z0-9])([A-Z][A-Z0-9]{0,4}-P)\s*/?\s*(\d{1,3})(?![0-9/])", up)
             or re.search(r"(?<![A-Z0-9/])(\d{3})\s+([A-Z][A-Z0-9]{0,4}-P)\b", up))
        if m:
            a, b = m.groups()
            if not a.isdigit():
                a, b = b, a
            num, game = f"{int(a):03d}/{b}", "pokemon"
    # セット記号（sv9 / SV9a / M1S / SM8b / s8b / S12a）: 括弧・角括弧の中か型番の直前
    sets = set()
    for m in re.finditer(r"(?<![A-Z0-9])((?:SV|SM|S|M|XY|BW|CP|SC|DP|PT|L|ADV|PCG)\d{0,2}[A-Z+]?)(?=[\s\]\)}]|\d{3}/)", up):
        v = m.group(1)
        if re.search(r"\d", v) and v not in ("S1", ):
            sets.add(v)
    rar = ""
    m = re.search(r"【([^】]+)】", s)
    if m and m.group(1).strip().upper() in RARS:
        rar = m.group(1).strip().upper()
    if not rar:
        m = re.search(r"(?:^|[\s(])(" + "|".join(RARS) + r")(?:[\s)\]]|$)", up)
        if m:
            rar = m.group(1)
    opvar = ""
    m = re.search(r"\b((?:SEC|SR|R|UC|C|L|P)-(?:P|SP|SPC))\b", up)
    if m:
        opvar = m.group(1)
    # 名前の頭: 最初の括弧・型番・レアの前まで
    head = re.split(r"[\[【{(（]|\s(?:" + "|".join(RARS) + r")(?:\s|$|-)|\s\d{3}|\d{3}/|\s[A-Z]{1,4}\d", s)[0].strip()
    head = re.sub(r"\s+[A-Za-z0-9]{1,5}-P\b.*$", "", head).strip()          # 「名探偵ピカチュウ SV-P 098」の SV-P
    head = re.sub(r"\s*(" + "|".join(RARS) + r")$", "", head).strip()
    quoted = re.findall(r"[『「]([^』」]+)[』」]", s)
    return {"raw": t, "num": num, "game": game, "sets": sets, "rar": rar, "opvar": opvar, "head": head, "quoted": quoted,
            "en": bool(re.search(r"英語|中国語|韓国語|タイ語|\bEN\b|english|アジア", s, re.I)), "psa": bool(re.search(r"PSA|鑑定", s, re.I))}


class Resolver:
    def __init__(self, rows, official, overrides):
        self.rows = rows
        self.by_id = {r["apparel_id"]: r for r in rows}
        self.official = official
        self.overrides = overrides
        self.by_num = {}
        for r in rows:
            k = r["_num"]
            if k:
                self.by_num.setdefault((r["_game"], k), []).append(r)
        self.sealed = [r for r in rows if r["item_type"] != "single"]
        self.by_setno = {}
        for r in rows:
            m = _OFF_SN.search(nfkc(r["_official"])) if r["_game"] == "pokemon" and r["item_type"] == "single" else None
            if m:
                self.by_setno.setdefault((m.group(1).upper(), int(m.group(2))), []).append(r)

    @classmethod
    def load(cls, games=SEALED_GAMES):
        official = {}
        p = os.path.join(DATA, "official_names.csv")
        if os.path.exists(p):
            for r in csv.DictReader(open(p, encoding="utf-8")):
                official[r["apparel_id"]] = r["localized_name"]
        rows = []
        for g in games:
            path = os.path.join(DATA, f"index_{g}.csv")
            if not os.path.exists(path):
                continue
            for r in csv.DictReader(open(path, encoding="utf-8")):
                if g not in GAMES and r["item_type"] == "single":
                    continue   # 遊戯王・DBのシングルは使わない（BOX・パックだけ）
                r["_game"] = g
                off = r.get("official_name") or official.get(r["apparel_id"], "")
                r["_official"] = off
                r["_num"] = num_key_of(r, g) if r["item_type"] == "single" else ""
                r["_set"] = set_of(r)
                if g == "pokemon" and off:
                    m = _OFF_SN.search(nfkc(off))
                    if m and not r["_set"]:
                        r["_set"] = m.group(1).upper()
                # BOX・パックは index の name が正式名そのまま（parse_name が削らない）。正式名が未取得でも使える
                r["_mintore"] = r.get("mintore_name") or (mintore_name(off) if off else
                                                          (mintore_name(r["name"]) if r["item_type"] != "single" else ""))
                r["_en"] = bool(re.search(r"英語版|中国語版|韓国語版|タイ語版|インドネシア語版|簡体字|繁体字|【EN】|for Asia|アジア版", off)) or "-en-" in (r.get("product_number") or "").lower()
                r["_unopened"] = "未開封パック" in off
                r["_nk"] = nk(r["name"])
                rows.append(r)
        ov = {}
        if os.path.exists(OVERRIDES):
            for r in csv.DictReader(open(OVERRIDES, encoding="utf-8")):
                ov[nk(r["元の表記"])] = (r["apparel_id"].strip(), (r.get("メモ") or "").strip())
        return cls(rows, official, ov)

    def _out(self, status, r=None, cands=(), why=""):
        return {"status": status, "apparel_id": r["apparel_id"] if r else "", "mintore_name": r["_mintore"] if r else "",
                "cands": [(c["apparel_id"], c["_mintore"] or c["name"], sc) for sc, c in cands][:12], "why": why}

    def _score_single(self, p, c):
        sc = 0; why = []
        h, cn = nk(p["head"]), c["_nk"]
        base = nk(re.sub(r"\s+(?:" + "|".join(RARS) + r")(?:-\w+)?(?:\s.*)?$", "", c["name"]))
        if h and (h == base or h == cn):
            sc += 4
        elif h and h in cn:
            sc += 2
        elif h:
            sc -= 4; why.append("名前違い")
        if p["sets"]:
            if c["_set"] and c["_set"] in p["sets"]:
                sc += 2
            elif c["_set"]:
                sc -= 3; why.append("セット違い")
        cr = (c.get("rarity") or "").upper()
        if p["rar"] and cr:
            sc += 1 if p["rar"] == cr or (p["rar"] in ("P", "PROMO") and cr == "プロモ") else -1
        cv = OP_VAR.search(nfkc(c["name"]).upper())
        cv = cv.group(0) if cv and cv.group(2) else ""
        if c["_game"] == "onepiece":
            if p["opvar"]:
                sc += 3 if p["opvar"] == cv else -3
            elif cv:
                sc -= 2   # 手書きに -P/-SP が無いのに候補はパラレル版
        if c["_en"] != p["en"]:
            sc -= 3
        if c["_unopened"]:
            sc -= 3
        # 別版の印（ミラー・エラー等）は手書きに書いてあるものだけ採る
        tn = nk(p["raw"])
        for w in VARIANT_WORDS:
            inc, int_ = w in nk(c["_official"] or c["name"]), w in tn
            if inc and not int_ and w not in EDITION_WORDS:
                sc -= 3
            elif inc and int_:
                sc += 2
        for q in p["quoted"]:
            if nk(q) and nk(q) in nk(c["_official"]):
                sc += 2
        return sc

    def resolve(self, title):
        t = str(title or "").strip()
        if not t:
            return self._out("不明", why="空")
        ov = self.overrides.get(nk(t))
        if ov:
            aid, memo = ov
            if not aid:   # 人が「決めない」とした表記（メモに理由）
                return self._out("要確認", why=memo or "人が保留にした（overrides）")
            r = self.by_id.get(aid)
            return self._out("確定", r, why="人が選んだ（overrides）") if r else self._out("不明", why=f"overridesのID {aid} が一覧に無い")
        if NON_SNKR.search(nfkc(t)):
            return self._out("スニダン外", why="ポイント・演出・家電など")
        p = parse_title(t)
        if p["num"]:
            games = [p["game"]] if p["game"] else list(GAMES)
            cands = []
            for g in games:
                cands += self.by_num.get((g, p["num"]), [])
            if not cands and re.fullmatch(r"\d{3}/[A-Z0-9]+-P", p["num"]):   # s8a-P 022 → [S8a-P 022/025]
                no, st = p["num"].split("/")
                cands = self.by_setno.get((st, int(no)), [])
            if not cands:
                return self._out("要確認", why=f"型番 {p['num']} がスニダン一覧に無い")
            scored = sorted(((self._score_single(p, c), c) for c in cands), key=lambda x: -x[0])
            top = scored[0][0]
            best = [c for sc, c in scored if sc == top]
            names = {nk(c["_mintore"]) or c["apparel_id"] for c in best}
            if top < 2:
                return self._out("要確認", None, scored, "名前が合う候補が無い")
            if len(names) > 1 and all(c["_game"] == "onepiece" for c in best):
                # ★ワンピの再録（THE BEST・スタートデッキ・記念品・セット）は手書きに収録の指定が無ければ初出の弾を採る
                orig = [c for c in best if re.search(r"ブースターパック|エクストラブースター", c["_official"])
                        and not re.search(r"プレミアムブースター|スタートデッキ|記念|アニバーサリー|セット|for Asia", c["_official"])]
                on = {nk(c["_mintore"]) for c in orig}
                if len(on) == 1:
                    return self._out("確定", orig[0], scored, "再録と同点→初出の弾（手書きに収録の指定なし）")
            if len(names) > 1:
                return self._out("要確認", None, scored, f"候補{len(names)}つが同点（版・収録違い）")
            return self._out("確定", best[0], scored, "")
        # 型番なし: BOX・パックはBOX/パックの中から、カードは名前で
        return self._resolve_by_name(t, p)

    def _resolve_by_name(self, t, p):
        s = nfkc(t)
        s = re.sub(r"\s*[(（]?\s*[×xX]?\s*\d+\s*パック\s*[)）]?$", " パック", s)   # 「アビスアイ 5パック」「(3パック)」→ パック（数は後で付ける）
        core = p["quoted"][0] if p["quoted"] else re.sub(r"\(\s*1?\s*box\s*\)|1box|ボックス|\bbox\b|【[^】]*】|\{[^}]*\}|\[[^\]]*\]|\([A-Za-z0-9-]{1,6}\)|パック$", " ", s, flags=re.I)
        is_box = bool(re.search(r"box|ボックス|パック|pack|デッキ|セット", s, re.I)) or bool(p["quoted"])
        kc = nk(core)
        if not kc:
            return self._out("不明", why="名前が取れない")
        pool = self.sealed if is_box else [r for r in self.rows if r["item_type"] == "single"]
        # ★「拡張パック」「ハイクラスパック」は商品の種類名。単位としての「パック」「BOX」だけを見る
        unit_s = re.sub(r"強化拡張パック|拡張パック|ハイクラスパック|ブースターパック|プロモカードパック|スペシャルパック|コンセプトパック", "", s)
        want_box = bool(re.search(r"box|ボックス", unit_s, re.I))
        want_pack = bool(re.search(r"パック|pack", unit_s, re.I)) and not want_box
        unit_known = want_box or want_pack
        scored = []
        parts = [x for x in re.split(r"デラックス", kc) if x] if "デラックス" in kc else [kc]
        for c in pool:
            cn = nk(c["name"])
            hit = kc in cn or ("デラックス" in kc and "デラックス" in cn and all(x in cn for x in parts))
            if not hit and not (is_box and cn in kc and len(cn) > 3):
                continue
            sc = 3 if kc == cn else 1
            if is_box:
                ctype = c["item_type"]
                if unit_known:
                    sc += 2 if (want_pack and ctype == "pack") or (want_box and ctype == "box") else -1
            else:
                base = nk(re.sub(r"\s+(?:" + "|".join(RARS) + r")(?:\s.*)?$", "", c["name"]))
                sc += 2 if base == nk(p["head"]) else 0
                if p["rar"] and (c.get("rarity") or "").upper() == p["rar"]:
                    sc += 2
            if c["_en"] != p["en"]:
                sc -= 3
            if c["_unopened"]:
                sc -= 3
            if is_box:
                cc = c["name"] + c["_official"]
                for w in ("シュリンクなし", "カートン", "セット", "エラー", "中国", "韓国", "デッキビルド", "デラックス", "英語", "スペシャル"):
                    if w in cc and w not in s:
                        sc -= 2   # 同じ名前の別形態より通常のBOXを選ぶ（手書きにその語があれば減点しない）
                    elif w in cc and w in s:
                        sc += 2
            scored.append((sc, c))
        if not scored:
            return self._out("要確認", why="名前で当たる商品が無い")
        scored.sort(key=lambda x: -x[0])
        top = scored[0][0]
        best = [c for sc, c in scored if sc == top]
        names = {nk(c["_mintore"]) or c["apparel_id"] for c in best}
        if len(scored) == 1 and scored[0][0] >= 1:
            c0 = best[0]
            base0 = nk(re.sub(r"\s+(?:" + "|".join(RARS) + r")(?:\s.*)?$|\s*:.*$", "", c0["name"]))
            # ★シングルは名前が候補の名前と一致するときだけ（「フクオカ」→「フクオカのピカチュウ」のような部分一致で決めない）
            if is_box or nk(p["head"]) in (base0, nk(c0["name"])) or kc in (base0, nk(c0["name"])):
                return self._out("確定", c0, scored, "型番なし・当たる商品が1つだけ")
            return self._out("要確認", None, scored, "型番なし・名前の一部しか一致しない")
        if is_box and len(names) == 1 and top >= 3:
            return self._out("確定", best[0], scored, "型番なし・名前で一致")
        # ★シングルを名前だけで確定するのは「同じ名前のカードがスニダンに1種類しか無い」ときだけ
        #   （レッドのピカチュウ・ひかるレックウザ等は別セットに同名があり得る。型番が無い手書きは人が選ぶ）
        if not is_box:
            same = {nk(c["_mintore"]) or c["apparel_id"] for sc, c in scored if sc >= 3 and not c["_en"] and not c["_unopened"]}
            if len(same) == 1 and top >= 4:
                return self._out("確定", best[0], scored, "型番なし・同名カードが1種類だけ")
        return self._out("要確認", None, scored, "型番なし・候補が複数" if len(names) > 1 else "型番なし・一致が弱い")
