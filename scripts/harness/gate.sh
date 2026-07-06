#!/usr/bin/env bash
# 合格ゲート — フロントエンドが「壊れていない」ことの客観判定。
#   1. 構文エラーがないこと          (node --check)
#   2. データ層が正しく動くこと        (check_local_api.mjs)
#   3. 全ルートが描画され、操作で例外が出ないこと (smoke_ui.mjs)
# すべて通れば exit 0、1つでも失敗すれば exit 1。
set -uo pipefail
cd "$(dirname "$0")/../.." || exit 2

fail=0

echo "▶ [1/3] 構文チェック (node --check)"
for f in frontend/app.js frontend/local-api.js; do
  if node --check "$f"; then
    echo "    ok: $f"
  else
    echo "    NG: $f"; fail=1
  fi
done

echo "▶ [2/3] ローカルAPI回帰 (scripts/check_local_api.mjs)"
node scripts/check_local_api.mjs || fail=1

echo "▶ [3/3] UIスモーク (scripts/smoke_ui.mjs)"
node scripts/smoke_ui.mjs || fail=1

if [ "$fail" -eq 0 ]; then
  echo "GATE: PASS"
else
  echo "GATE: FAIL"
fi
exit "$fail"
