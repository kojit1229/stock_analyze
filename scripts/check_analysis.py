#!/usr/bin/env python3
"""AI決算分析 manifest(frontend/data/analysis/seen.json)の整合検証 (roadmap P3-2)。

generate_analysis.py が生成した frontend/data/analysis/ 配下について、以下を検証する:
  (a) 各エントリの必須フィールドが揃っていること
  (b) エントリが指す .md ファイルが実在すること
  (c) disclosure_id が重複していないこと
  (d) .md ファイルの文字数が上限(既定20000字)を超えていないこと
違反があれば一覧を表示して exit 1 (フェイルラウド)。

逆方向として、analysis/*.md にあるが seen.json のどのエントリからも参照されていない
孤児ファイルを警告表示する(exit codeには影響しない。参照漏れの早期発見用)。

analysis/ ディレクトリや seen.json 自体が存在しない場合は「対象なし」として正常終了する
(CI で analysis/ が空/未生成でも赤くしないため)。

依存: Python 標準ライブラリのみ。
"""
import argparse
import json
import os
import sys

REQUIRED_FIELDS = ("disclosure_id", "code", "name", "title", "doc_type", "published_at", "path", "generated_at")
# 空文字を許さない(必須の識別子・ファイル名・時刻)フィールド。
# name/title/doc_type/published_at はキーの存在のみ必須とし、値の空は許容する
# (例: stocks.json に銘柄名が無い場合の name="" など、既存の後方互換のため)。
NON_EMPTY_FIELDS = ("disclosure_id", "code", "path", "generated_at")
DEFAULT_MAX_CHARS = 20000


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def check_manifest(data_dir, max_chars=DEFAULT_MAX_CHARS):
    """検証を実行し (violations, warnings) を返す。どちらも文字列のリスト。"""
    violations = []
    warnings = []
    analysis_dir = os.path.join(data_dir, "analysis")
    manifest_path = os.path.join(analysis_dir, "seen.json")

    if not os.path.exists(manifest_path):
        return violations, warnings  # 未生成は正常(CI等でanalysis/が無くても赤くしない)

    try:
        manifest = load_json(manifest_path)
    except (OSError, ValueError) as e:
        return [f"{manifest_path} の読み込みに失敗しました(壊れている可能性): {e}"], warnings

    items = manifest.get("items") if isinstance(manifest, dict) else None
    if items is None:
        violations.append(f"{manifest_path}: 'items' 配列がありません")
        return violations, warnings

    seen_disclosure_ids = {}
    referenced_paths = set()
    for i, item in enumerate(items):
        label = f"item[{i}]"
        if not isinstance(item, dict):
            violations.append(f"{label}: オブジェクトではありません: {item!r}")
            continue

        # (a) 必須フィールド
        missing = [f for f in REQUIRED_FIELDS if f not in item]
        if missing:
            violations.append(f"{label}: 必須フィールド欠落: {missing}")
        empty = [f for f in NON_EMPTY_FIELDS if f in item and not item.get(f)]
        if empty:
            violations.append(f"{label}: フィールドが空です: {empty}")

        disc_id = item.get("disclosure_id")
        path = item.get("path")
        label = f"item[{i}] (disclosure_id={disc_id!r})"

        # (c) disclosure_id 重複
        if disc_id is not None:
            if disc_id in seen_disclosure_ids:
                violations.append(
                    f"{label}: disclosure_id が item[{seen_disclosure_ids[disc_id]}] と重複しています")
            else:
                seen_disclosure_ids[disc_id] = i

        if not path:
            continue  # (a)で既に欠落/空を報告済み。以降のファイルチェックはスキップ
        referenced_paths.add(path)

        # (b) .md ファイルの実在
        md_path = os.path.join(analysis_dir, path)
        if not os.path.isfile(md_path):
            violations.append(f"{label}: 参照先ファイルが存在しません: {md_path}")
            continue

        # (d) 文字数上限
        try:
            with open(md_path, encoding="utf-8") as f:
                text = f.read()
        except OSError as e:
            violations.append(f"{label}: 参照先ファイルの読み込みに失敗: {md_path} ({e})")
            continue
        if len(text) > max_chars:
            violations.append(
                f"{label}: 文字数上限超過: {md_path} は {len(text)} 字 (上限 {max_chars} 字)")

    # 逆方向: analysis/*.md にあるが seen.json から参照されていない孤児ファイル
    if os.path.isdir(analysis_dir):
        for fname in sorted(os.listdir(analysis_dir)):
            if not fname.endswith(".md"):
                continue
            if fname not in referenced_paths:
                warnings.append(f"孤児ファイル(seen.jsonから未参照): {os.path.join(analysis_dir, fname)}")

    return violations, warnings


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data", default="frontend/data")
    ap.add_argument("--max-chars", type=int, default=DEFAULT_MAX_CHARS,
                     help=f"分析.mdの文字数上限(既定{DEFAULT_MAX_CHARS})")
    args = ap.parse_args()

    violations, warnings = check_manifest(args.data, max_chars=args.max_chars)

    for w in warnings:
        print(f"[check_analysis] WARN: {w}")

    if violations:
        print(f"[check_analysis] {len(violations)}件の整合エラー:")
        for v in violations:
            print(f"  - {v}")
        sys.exit(1)

    print("[check_analysis] OK: manifest整合検証に問題はありません"
          + (f" (警告{len(warnings)}件)" if warnings else ""))
    sys.exit(0)


if __name__ == "__main__":
    main()
