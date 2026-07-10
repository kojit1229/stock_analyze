#!/usr/bin/env python3
"""AI決算分析の生成・保存 (Phase2 P2-2)。

build_analysis_input.py が組み立てた素材を Claude へ渡し、
frontend/data/analysis/<code>_<disclosure_id>.md に分析結果を保存する。
frontend/data/analysis/seen.json に処理済み disclosure_id を記録して冪等にする
(このファイルはフロントエンドの一覧表示用 manifest も兼ねる)。

バックエンドは2種類 (--backend で選択、既定は api で後方互換):
- api        : Claude API (Messages API) を直接呼ぶ (要 ANTHROPIC_API_KEY)。GitHub Actions用。
- claude-cli : ローカルの claude CLI (`claude -p`) をサブスクリプション認証で呼ぶ。
               ANTHROPIC_API_KEY 不要。Windowsタスクスケジューラ経由のローカルバッチ用
               (2026-07-10 承認。ワークスペースの loop/collect-invest.sh と同じ
               `claude -p --output-format json` 呼び出しパターンを踏襲)。

対象: config/pdf_watchlist.json の codes ∪ config/user_data.json のマイ銘柄
      (コスト抑制のため全銘柄には広げない)。
API失敗時は例外を送出してプロセスを exit≠0 で終了する(黙殺しない)。
出力に生成AIの分析結果を含むため、短期売買シグナルではなく事実確認・乖離指摘に
限定するようシステムプロンプトで指示する(FI原則)。

依存: Python 標準ライブラリのみ (urllib.request で Messages API、subprocess で claude CLI を呼ぶ)。
"""
import argparse
import datetime
import json
import os
import subprocess
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
    "資する客観的な事実整理に徹してください。断定できない推測は推測と明記してください。\n\n"
    "出力は必ず見出し `## 3行要約` から始め、直後に箇条書き(`- `)で3行、この開示の要点を"
    "簡潔に書いてください。3行要約の後に空行を1つ入れ、通常の見出し構成で詳細な事実整理を"
    "続けてください(3行要約はホーム画面・一覧画面の軽量表示に機械的に抽出して使うため、"
    "この形式を厳守してください)。"
)


def log(msg):
    print(msg, flush=True)


def load_json(path, fallback=None):
    """ファイル未存在は fallback を返すが、存在するファイルの読込・パース失敗は
    黙殺せず例外を送出する(冪等性台帳の破損を空扱いにして再処理・再通知・
    余計なAIコストを発生させないため。FI原則)。"""
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return fallback
    except (OSError, ValueError) as e:
        raise RuntimeError(f"{path} の読み込みに失敗しました(壊れている可能性): {e}") from e


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


def build_prompt(material):
    return (
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


def call_claude(material, api_key):
    prompt = build_prompt(material)
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


# claude-cliバックエンドの累積コスト(USD)。main()実行ごとに reset_cli_cost_total() でリセットする。
# call_claude_cli の戻り値は run() の call_fn 契約(テキストのみ返す)を変えないための側路。
_CLI_COST_TOTAL = 0.0


def reset_cli_cost_total():
    """claude-cliバックエンドの累積コストを0に戻す。main()の各実行の先頭、およびテストで使う。"""
    global _CLI_COST_TOTAL
    _CLI_COST_TOTAL = 0.0


def get_cli_cost_total():
    """claude-cliバックエンドの累積コスト(USD)を返す。"""
    return _CLI_COST_TOTAL


def call_claude_cli(material, api_key=None):
    """ローカルの claude CLI (`claude -p`) をヘッドレス実行して分析テキストを得る。
    ANTHROPIC_API_KEY は不要 (CLIのサブスクリプション認証を使う)。api_key引数は
    call_claude とシグネチャを揃えるためだけに存在し、未使用。
    loop/collect-invest.sh の claude 呼び出しパターン(--output-format json,
    stdinでプロンプト投入)を踏襲する。
    応答の total_cost_usd を _CLI_COST_TOTAL へ累積する(NEVER 8 予算ガードの精度確保のため。
    黙殺しない: 数値でないレスポンスはwarnログを出したうえで0として扱う)。"""
    global _CLI_COST_TOTAL
    prompt = build_prompt(material)
    claude_bin = os.environ.get("CLAUDE_BIN", "claude")
    cmd = [claude_bin, "-p", "--model", MODEL, "--output-format", "json",
           "--system-prompt", SYSTEM_PROMPT, "--allowedTools", ""]
    try:
        proc = subprocess.run(
            cmd, input=prompt, capture_output=True, text=True, encoding="utf-8",
            timeout=180, check=False)
    except (OSError, subprocess.SubprocessError) as e:
        raise RuntimeError(f"claude CLIの起動に失敗しました: {e}") from e
    if proc.returncode != 0:
        raise RuntimeError(
            f"claude CLIが失敗しました (exit={proc.returncode}): {(proc.stderr or '').strip()[:500]}")
    try:
        resp = json.loads(proc.stdout)
    except ValueError as e:
        raise RuntimeError(f"claude CLIの出力をJSONとして解析できません: {e}") from e
    # is_error(エラー応答)でもCLI呼び出し自体のコストは発生しているため、
    # エラー判定より先に加算する(黙殺しない。NEVER 8 予算ガードの精度確保)。
    cost = resp.get("total_cost_usd")
    if isinstance(cost, (int, float)) and not isinstance(cost, bool):
        _CLI_COST_TOTAL += cost
    else:
        log(f"claude CLI応答にtotal_cost_usdが無い/数値でないため0として扱います: {cost!r}")
    if resp.get("is_error"):
        raise RuntimeError(f"claude CLIがエラーを返しました: {resp.get('result')}")
    text = (resp.get("result") or "").strip()
    if not text:
        raise RuntimeError("claude CLIの応答にテキストが含まれていません")
    return text


def extract_summary(body_text):
    """分析本文冒頭の `## 3行要約` セクションから箇条書き行を抽出して返す(P3-3)。
    見出しが無い/直後に箇条書きが1行も続かない場合は空リストを返す(呼び出し側でwarnログを出す。
    抽出失敗は生成自体の失敗にはしない)。"""
    lines = body_text.splitlines()
    start = None
    for i, line in enumerate(lines):
        if line.strip() == "## 3行要約":
            start = i + 1
            break
    if start is None:
        return []
    bullets = []
    for line in lines[start:]:
        s = line.strip()
        if not s:
            if bullets:
                break
            continue  # 見出し直後の空行はスキップ
        if s.startswith("- ") or s.startswith("* "):
            bullets.append(s[2:].strip())
        else:
            break  # 箇条書きでない行(別の見出し等)に達したら終了
    return bullets


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
    summary = extract_summary(body_text)
    if not summary:
        log(f"警告: {filename} から3行要約を抽出できませんでした"
            f"(本文冒頭に'## 3行要約'の箇条書きが無い可能性)。summaryは空配列で記録します")
    return filename, summary


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


def create_issue(item, now):
    """分析MD生成時にGitHub Issueを起票し@メンションで通知する
    (generate_alerts.create_issue と同じ経路・同じユーザー名)。"""
    token = os.environ.get("GITHUB_TOKEN")
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not token or not repo:
        log("GITHUB_TOKEN/GITHUB_REPOSITORY が無いため Issue 通知はスキップ")
        return False
    owner = repo.split("/")[0]
    link = f"https://github.com/{repo}/blob/main/frontend/data/analysis/{item['path']}"
    body = {
        "title": f"🧠 AI決算分析 {item['code']} {item['name']} ({(item['published_at'] or '')[:10]})",
        "body": (f"@{owner} {item['code']} {item['name']} の決算分析を生成しました。\n\n"
                 f"- 開示: {item['title']} ({item['doc_type']})\n"
                 f"- 分析: {link}\n\n"
                 "> このIssueは決算ナビのAI分析機能が自動作成しました。確認後はクローズしてください。"),
        "labels": ["ai-analysis"],
    }
    req = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/issues",
        data=json.dumps(body).encode(), method="POST",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                 "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            log(f"AI分析Issue通知を作成: HTTP {r.status}")
            return True
    except Exception as e:  # noqa: BLE001
        log(f"AI分析Issue作成に失敗: {type(e).__name__}: {e}")
        return False


def run(data_dir, watchlist_path, user_path, api_key, now, call_fn=call_claude, notify_fn=create_issue,
        max_items=3, max_cost_usd=1.0, get_cost_fn=get_cli_cost_total):
    """AI決算分析を生成する。1回の実行で処理する開示数は max_items で上限を
    設ける(watchlist/マイ銘柄に未処理短信が多い場合のコスト暴発を防ぐ)。
    また各アイテムの処理前に get_cost_fn() の累積コストを確認し、
    max_cost_usd に達していれば「上限到達」をログしてそこで打ち切る
    (exit≠0にはせず正常終了。api backendはコストを計測しないため常に0扱いで
    このゲートは事実上claude-cli backend専用)。"""
    codes = target_codes(watchlist_path, user_path)
    if not codes:
        log("分析対象銘柄(pdf_watchlist/マイ銘柄)が無いため終了")
        return []
    pending = pending_disclosures(data_dir, codes, seen_ids_of(data_dir))
    if not pending:
        log("新規の分析対象開示が無いため終了")
        return []
    if len(pending) > max_items:
        log(f"保留{len(pending)}件のうち上限{max_items}件のみ処理します(--max-items)")
    targets = pending[:max_items]
    stocks = load_json(os.path.join(data_dir, "stocks.json"), []) or []
    names = {s.get("code"): s.get("name", "") for s in stocks if isinstance(s, dict)}
    saved = []
    for d in targets:
        cost_so_far = get_cost_fn()
        if cost_so_far >= max_cost_usd:
            log(f"累積コスト上限(${max_cost_usd:.2f})に到達したため以降の生成を中止します"
                f"(処理済み{len(saved)}/{len(targets)}件、現在のコスト ${cost_so_far:.4f})")
            break
        code = str(d.get("code"))
        material = bai.build_analysis_input(code, d, data_dir)
        text = call_fn(material, api_key)
        filename, summary = save_analysis(data_dir, code, names.get(code, ""), material, text, now)
        item = {
            "disclosure_id": material["disclosure"]["id"], "code": code, "name": names.get(code, ""),
            "title": material["disclosure"]["title"], "doc_type": material["disclosure"]["doc_type"],
            "published_at": material["disclosure"]["published_at"], "path": filename,
            "generated_at": now.strftime("%Y-%m-%dT%H:%M:%S+09:00"), "summary": summary,
        }
        update_manifest(data_dir, item, now)
        notify_fn(item, now)
        saved.append(item)
        log(f"分析生成: {code} {filename}")
    return saved


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="frontend/data")
    ap.add_argument("--watchlist", default="config/pdf_watchlist.json")
    ap.add_argument("--user", default="config/user_data.json")
    ap.add_argument("--backend", choices=("api", "claude-cli"), default="api",
                     help="api=Claude API (既定・要ANTHROPIC_API_KEY) / "
                          "claude-cli=ローカルclaude CLI (サブスクリプション認証、ローカルバッチ用)")
    ap.add_argument("--max-items", type=int, default=3,
                     help="1回の実行で処理する開示数の上限(既定3。コスト暴発防止)")
    ap.add_argument("--max-cost-usd", type=float, default=1.0,
                     help="累積コスト(USD)がこれを超えたら以降の生成を中止する(既定1.0)")
    args = ap.parse_args()
    if args.backend == "claude-cli":
        reset_cli_cost_total()
        call_fn, api_key = call_claude_cli, None
    else:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            log("ANTHROPIC_API_KEY が無いため終了")
            sys.exit(1)
        call_fn = call_claude
    now = datetime.datetime.now(JST)
    try:
        run(args.data, args.watchlist, args.user, api_key, now, call_fn=call_fn,
            max_items=args.max_items, max_cost_usd=args.max_cost_usd)
    except urllib.error.URLError as e:
        log(f"Claude API呼び出しに失敗したため終了: {e}")
        sys.exit(1)
    except bai.InputAssemblyError as e:
        log(f"分析入力の組み立てに失敗したため終了: {e}")
        sys.exit(1)
    except RuntimeError as e:
        log(f"分析生成に失敗したため終了: {e}")
        sys.exit(1)
    finally:
        # NEVER 8 予算ガードの精度確保のため成功/失敗によらずコストを機械可読な1行で出力する
        # (analyze-kessan.sh がこの行をパースして log-cost.sh 経由で台帳に記録する)。
        # apiバックエンドはコストをこのプロセスで把握しないため0扱い(同一形式で出力)。
        cost = get_cli_cost_total() if args.backend == "claude-cli" else 0.0
        print(f"TOTAL_COST_USD={cost:.4f}", flush=True)


if __name__ == "__main__":
    main()
