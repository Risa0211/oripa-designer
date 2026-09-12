# -*- coding: utf-8 -*-
"""パス・認証・引数の解決。ここ以外に絶対パスを書かない。

- リポジトリの場所はこのファイルからの相対で求める（どのPCでも動く）
- 台帳はリポジトリ内を優先し、無ければ環境変数 MINNATORECA_CSV_DIR を見る
- 作業ディレクトリは --work で受ける（既定はカレント）
"""
import argparse
import io
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]          # oripa-designer
BUILDER = REPO / 'gacha-csv-builder'

# 里沙さんの環境にある外部の台帳ディレクトリ（~/minnatoreca-gacha-csv）。
# 設定されていれば読み書きの対象に加える。無くても動く。
def _find_extra_csv_dir():
    """外部の台帳ディレクトリ。環境変数が優先、無ければ既定の場所を探す。
    ★ここを見落とすと台帳が片方だけ更新され、画像URLが404になる。"""
    env = os.environ.get('MINNATORECA_CSV_DIR')
    if env:
        return Path(env).expanduser()
    default = Path('~/minnatoreca-gacha-csv').expanduser()
    return default if default.is_dir() else None


EXTRA_CSV_DIR = _find_extra_csv_dir()

# (ファイル名, 出典ラベル, 必須か)
LEDGERS = [
    ('master_db_added.csv', 'added', True),
    ('master_db_dopa.csv', 'dopa', True),
    ('master_db_onepiece.csv', 'onepiece', True),
    ('master_db_pokemoncard_owned.csv', 'owned', True),
    ('master_db_admin.csv', 'admin', False),
    ('master_db_pokemoncard.csv', '公式', False),   # 5MB。リポジトリには入れていない
]

# 追記する台帳（upload が書く）。★1つでも漏れると画像URLが404になる。
APPEND_LEDGERS = ('master_db_added.csv', 'thumb_map.csv')


def _sys_path_setup():
    """リポジトリ内のモジュール（wp_client / snkrdunk_client）を import できるようにする。"""
    for p in (str(REPO), str(BUILDER)):
        if p not in sys.path:
            sys.path.insert(0, p)


_sys_path_setup()


def ledger_files():
    """読む台帳の (パス, 出典ラベル) を返す。見つからない必須台帳があれば中断する。"""
    out, missing = [], []
    for name, label, required in LEDGERS:
        for base in (BUILDER, EXTRA_CSV_DIR):
            if base and (base / name).exists():
                out.append(((base / name), label))
                break
        else:
            if required:
                missing.append(name)
    if missing:
        raise SystemExit(
            '台帳が見つかりません: ' + ', '.join(missing) +
            f'\n  探した場所: {BUILDER}' +
            (f' と {EXTRA_CSV_DIR}' if EXTRA_CSV_DIR else '') +
            '\n  リポジトリを最新にするか、環境変数 MINNATORECA_CSV_DIR を設定してください。')
    return out


def append_targets(name):
    """追記先のパス一覧。リポジトリ内は必ず含め、外部ディレクトリはあれば含める。"""
    paths = [BUILDER / name]
    if EXTRA_CSV_DIR and (EXTRA_CSV_DIR / name).exists():
        paths.append(EXTRA_CSV_DIR / name)
    return paths


def wp_credentials():
    """WordPress（画像保管庫）の認証。環境変数を優先し、無ければ ~/.wp_env を読む。"""
    user, pw = os.environ.get('WP_USER'), os.environ.get('WP_APP_PASS')
    if user and pw:
        return user, pw
    env_file = Path('~/.wp_env').expanduser()
    if not env_file.exists():
        raise SystemExit(
            'WordPressの認証が見つかりません。\n'
            '  環境変数 WP_USER / WP_APP_PASS を設定するか、~/.wp_env に次の形で置いてください:\n'
            '    export WP_USER=...\n    export WP_APP_PASS=...')
    env = {}
    for line in io.open(env_file, encoding='utf-8'):
        line = line.strip().replace('export ', '', 1)
        if '=' in line:
            k, v = line.split('=', 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    try:
        return env['WP_USER'], env['WP_APP_PASS']
    except KeyError as e:
        raise SystemExit(f'~/.wp_env に {e} がありません。')


def parse_args(description, extra=None):
    """全スクリプト共通の引数。--work（作業ディレクトリ）と、必要なら追加の引数。"""
    ap = argparse.ArgumentParser(description=description)
    ap.add_argument('--work', default='.', help='案件の作業ディレクトリ（既定: カレント）')
    if extra:
        extra(ap)
    args = ap.parse_args()
    args.work = Path(args.work).expanduser().resolve()
    if not args.work.exists():
        raise SystemExit(f'作業ディレクトリがありません: {args.work}')
    return args


def need(work, name):
    """作業ディレクトリ内の入力ファイル。無ければ、どの段で作られるかを添えて中断する。"""
    p = Path(work) / name
    if not p.exists():
        here = Path(__file__).resolve().parent
        made_by = {
            'rows.json': '設計側の道具に採用カードの一覧を出させて',
            'meta.json': '設計側の道具に本のタイトルを出させて',
            'snkr.json': f'python3 {here}/snkr.py --work {work}',
            'match.json': f'python3 {here}/match.py --work {work}',
            'final.json': f'python3 {here}/final.py --work {work}',
        }.get(name, '前の段を実行して')
        raise SystemExit(f'{p} がありません。\n  先に: {made_by}')
    return p
