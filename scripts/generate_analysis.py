#!/usr/bin/env python3
"""AI決算分析の生成・保存 (Phase2 P2-2)。

build_analysis_input.py が組み立てた素材を Claude API (Messages API) へ渡し、
frontend/data/analysis/<code>_<disclosure_id>.md に分析結果を保存する。
frontend/data/analysis/seen.json に処理済み disclosure_id を記録して冪等にする
(このファイルはフロントエンドの一覧表示用 manifest も兼ねる)。

対象: config/pdf_watchlist.json の codes ∪ config/user_data.json のマイ銘柄
      (コスト抑制のため全銘柄には広げない)。
API失敗時は例外を送出してプロセスを exit≠0 で終了する(黙殺しない)。
出力に生成AIの分析結果を含むため、短期売買シグナルではなく事実確認・乖離指摘に
限定するようシステムプロンプトで指示する(FI原則)。

依存: Python 標準ライブラリのみ (urllib.request で Messages API を呼ぶ)。
"""
import argparse
import datetime
import json
import os
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_analysis_input as bai  # noqa: E402
import generate_alerts as ga        # noqa: E402 (parse_user_data を再利用)

JST = datetime.timezone(datetime.timedelta(hours=9))
API_URL = "https://api.anthropic.com/v1/messages"
MODEL = "claude-sonnet-5"
ANALYSIS_DOC_TYPES = ("決算短信", "訂正決算短信")
SYSTEM_PROMPT = (
    "あなたは個人投資家向けの決算分析アシスタントです。与えられたXBRL実績・進捗率・"
    "業績予想修正の有無・市況概況をもとに、事実確認と会社予想・前年同期からの乖離点を"
    "整理してください。短期売買のシグナルや売買推奨は出さず、長期の投資規律の維持に"
    "資する客観的な事実整理に徹してください。断定できない推測は推測と明記してください。"
)


def log(msg):
    print(msg, flush=True)


def load_json(path, fallback=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return fallback


def target_codes(watchlist_path, user_path):
    """コスト抑制のため分析対象を pdf_watchlist ∪ マイ銘柄 に限定する。"""
    codes = set()
    wl = load_json(watchlist_path, {}) or {}
    codes.update(str(c) for c in (wl.get("codes") or []))
    user = load_json(user_path, None)
    if user:
        mystocks, _ = ga.parse_user_data(user)
        codes.update(m["code"] for m in mystocks)
    return codes


def seen_ids_of(data_dir):
    manifest = load_json(os.path.join(data_dir, "analysis", "seen.json"), {"items": []})
    return {str(it.get("disclosure_id")) for it in manifest.get("items") or []}


def pending_disclosures(data_dir, codes, seen_ids):
    discs = load_json(os.path.join(data_dir, "disclosures.json"), []) or []
    out = []
    for d in discs:
        if d.get("doc_type") not in ANALYSIS_DOC_TYPES:
            continue
        code, key = str(d.get("code") or ""), str(d.get("key") or "")
        if code in codes and key and key not in seen_ids:
            out.append(d)
    return out


def call_claude(material, api_key):
    prompt = (
        f"銘柄コード: {material['code']}\n"
        f"開示: {material['disclosure']['title']} ({material['disclosure']['doc_type']}, "
        f"{material['disclosure']['published_at']})\n\n"
        f"年次実績(直近, [期末日,売上高,営業利益,純利益,EPS]): {material['actuals']['annual']}\n"
        f"四半期実績: {material['actuals']['quarterly']}\n"
        f"増収増益シグナル: {material['growth_signal']} / 利益率改善シグナル: {material['margin_signal']}\n"
        f"進捗率(会社予想データが無いため前期比進捗率の前年同時点比較で代替): {material['progress']}\n"
        f"業績予想修正シグナル: {material['revision_signal']} / 関連開示: {material['revision_disclosures']}\n\n"
        f"市況概況({material['market_context_date']}):\n{material['market_context_md']}\n"
    )
    body = json.dumps({
        "model": MODEL, "max_tokens": 1500, "system": SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": prompt}],
    }).encode("utf-8")
    req = urllib.request.Request(
        API_URL, data=body, method="POST",
        headers={"x-api-key": api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        resp = json.loads(r.read().decode("utf-8"))
    text = "\n".join(b.get("text", "") for b in resp.get("content", []) if b.get("type") == "text").strip()
    if not text:
        raise RuntimeError("Claude APIの応答にテキストが含まれていません")
    return text


def save_analysis(data_dir, code, name, material, body_text, now):
    disc_id = material["disclosure"]["id"]
    filename = f"{code}_{disc_id}.md"
    out_dir = os.path.join(data_dir, "analysis")
    os.makedirs(out_dir, exist_ok=True)
    header = (
        f"# {code} {name} 決算分析\n\n"
        f"- 開示: {material['disclosure']['title']} ({material['disclosure']['doc_type']})\n"
        f"- 公表日時: {material['disclosure']['published_at']}\n"
        f"- 生成日時: {now.strftime('%Y-%m-%d %H:%M:%S+09:00')}\n"
        f"- 出典: {', '.join(material['sources'])}\n\n---\n\n"
    )
    with open(os.path.join(out_dir, filename), "w", encoding="utf-8") as f:
        f.write(header + body_text + "\n")
    return filename


def update_manifest(data_dir, item, now):
    """seen.json (処理済みdisclosure_id記録 兼 フロントエンド向けmanifest) を更新する。"""
    path = os.path.join(data_dir, "analysis", "seen.json")
    manifest = load_json(path, {"items": []}) or {"items": []}
    manifest.setdefault("items", [])
    manifest["items"] = [it for it in manifest["items"] if it.get("disclosure_id") != item["disclosure_id"]]
    manifest["items"].append(item)
    manifest["items"].sort(key=lambda it: it.get("published_at") or "", reverse=True)
    manifest["generated_at"] = now.strftime("%Y-%m-%dT%H:%M:%S+09:00")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, separators=(",", ":"))


def run(data_dir, watchlist_path, user_path, api_key, now, call_fn=call_claude):
    codes = target_codes(watchlist_path, user_path)
    if not codes:
        log("分析対象銘柄(pdf_watchlist/マイ銘柄)が無いため終了")
        return []
    pending = pending_disclosures(data_dir, codes, seen_ids_of(data_dir))
    if not pending:
        log("新規の分析対象開示が無いため終了")
        return []
    stocks = load_json(os.path.join(data_dir, "stocks.json"), []) or []
    names = {s.get("code"): s.get("name", "") for s in stocks if isinstance(s, dict)}
    saved = []
    for d in pending:
        code = str(d.get("code"))
        material = bai.build_analysis_input(code, d, data_dir)
        text = call_fn(material, api_key)
        filename = save_analysis(data_dir, code, names.get(code, ""), material, text, now)
        item = {
            "disclosure_id": material["disclosure"]["id"], "code": code, "name": names.get(code, ""),
            "title": material["disclosure"]["title"], "doc_type": material["disclosure"]["doc_type"],
            "published_at": material["disclosure"]["published_at"], "path": filename,
            "generated_at": now.strftime("%Y-%m-%dT%H:%M:%S+09:00"),
        }
        update_manifest(data_dir, item, now)
        saved.append(item)
        log(f"分析生成: {code} {filename}")
    return saved


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="frontend/data")
    ap.add_argument("--watchlist", default="config/pdf_watchlist.json")
    ap.add_argument("--user", default="config/user_data.json")
    args = ap.parse_args()
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        log("ANTHROPIC_API_KEY が無いため終了")
        sys.exit(1)
    now = datetime.datetime.now(JST)
    try:
        run(args.data, args.watchlist, args.user, api_key, now)
    except urllib.error.URLError as e:
        log(f"Claude API呼び出しに失敗したため終了: {e}")
        sys.exit(1)
    except bai.InputAssemblyError as e:
        log(f"分析入力の組み立てに失敗したため終了: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
