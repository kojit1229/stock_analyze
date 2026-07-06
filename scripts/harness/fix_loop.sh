#!/usr/bin/env bash
# 自動修正ループ (ハーネス)
#   ゲート実行 → 失敗ならログを Claude Code に渡して修正 → 再実行 … を繰り返す。
#
#   環境変数:
#     MAX_ITER   最大反復回数 (既定 6)
#     CLAUDE_MODEL  使うモデル (任意。未指定ならアカウント既定)
#   前提: `claude` CLI が PATH にあり、ANTHROPIC_API_KEY が設定済みであること。
set -uo pipefail
cd "$(dirname "$0")/../.." || exit 2

MAX="${MAX_ITER:-6}"
GATE="scripts/harness/gate.sh"
PROMPT_FILE="scripts/harness/fix_prompt.md"
LOG="$(mktemp)"

model_args=()
[ -n "${CLAUDE_MODEL:-}" ] && model_args=(--model "$CLAUDE_MODEL")

for i in $(seq 1 "$MAX"); do
  echo "━━━━━━━━━━ iteration $i / $MAX ━━━━━━━━━━"

  if bash "$GATE" >"$LOG" 2>&1; then
    echo "✅ ゲート通過 (iteration $i)"
    cat "$LOG"
    exit 0
  fi

  echo "❌ ゲート失敗。抜粋:"
  tail -n 30 "$LOG"

  prompt="$(cat "$PROMPT_FILE")

## 今回のゲート出力（この失敗を解消すること）
\`\`\`
$(cat "$LOG")
\`\`\`"

  echo "── Claude Code に修正を依頼中 …"
  if ! claude -p "$prompt" \
        "${model_args[@]}" \
        --allowedTools "Read,Edit,Write,Bash(node *)" \
        --permission-mode acceptEdits \
        --max-turns 30; then
    echo "⚠ claude CLI が異常終了しました。ループを打ち切ります。"
    exit 2
  fi
done

echo "⛔ 最大反復 ($MAX) に到達しても未解決です。最終ゲート結果:"
bash "$GATE" || true
exit 1
