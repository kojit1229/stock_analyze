# 依頼プロンプト(別チャットへの貼り付け用) — 決算ナビ AI要約機能

新着決算短信をAIが3行要約し、フロントで「あれば表示」する機能を追加してください。

**目的(1文)**: 新着決算短信をAIが3行要約し、フロントで「あれば表示」する機能を追加する。

**必読ファイル(絶対パス)**:
- `C:\Users\kojit\Documents\ClaudeCode\repos\stock_analyze\CLAUDE.md`(このリポジトリの規約。着手前に必ず読むこと)
- `C:\Users\kojit\Documents\ClaudeCode\repos\stock_analyze\.claude\skills\stock-analyze-fix\SKILL.md`(frontend/を修正するときは必ずこのSkillに従うこと)
- ai-linked-app-dev Skill(型: バッチ→`data/summaries/*.json`→real/sampleフォールバックと同型の「あれば表示」設計。taskchute-ipadのv57 `hydrateStaticMarkdown`パターンと同じ思想)
- `C:\Users\kojit\Documents\ClaudeCode\repos\stock_analyze\frontend\local-api.js`(既存のreal/sampleフォールバック構造。`frontend/data/*.json`があればreal、なければsampleという既存の型に要約データも合わせること)
- `C:\Users\kojit\Documents\ClaudeCode\repos\stock_analyze\.github\workflows\update-data.yml`(既存の定期データ生成パイプライン。要約バッチをどこに差し込むか判断する材料)

**実装方針の骨子**:
- `update-data.yml`の既存ステップ(`fetch_real_data.py`等)の後段に新ステップを追加するか、ローカルバッチとして独立させるかはPlanフェーズで判断する
- 新着の決算短信XBRL/PDFテキストを取得 → `claude -p`で3行程度に要約 → `frontend/data/summaries/<code>-<date>.json`として生成・コミット
- フロント側は既存のreal/sampleフォールバックと同じ流儀で、要約ファイルが「あれば」短信一覧に表示し、「なければ」従来通りの表示を維持する(要約の有無でアプリの他機能が壊れないこと)
- 要約データを検証するスクリプト(スキーマ・文字数上限のチェック)を必須で用意し、不正な出力はコミットさせない(フェイルラウド)
- 生成する要約文には「投資助言ではない」旨の免責を含める

**制約**:
- `npm run gate`が`GATE: PASS`になることを完了条件に含める
- `scripts/`・`tests/`・`.github/`を書き換えてゲートを回避することは禁止(CLAUDE.md準拠)
- 依存ライブラリは追加しない(開発用jsdomのみ例外、CLAUDE.md準拠)
- iOS Safari制約(`new Date("文字列")`禁止、モジュール変数は`render()`より上で宣言、入力欄を含む再描画は差分DOMパッチ)を厳守する
- コストは1日の新着分のみを対象とし、予算上限を設ける(無制限にAPIを呼ばない)

**完了条件(検証可能)**:
1. `npm run gate`が`GATE: PASS`になること
2. 要約JSONのスキーマ・文字数上限を検証するスクリプトが存在し、不正な出力を検知してexit 1で止められること
3. `frontend/data/summaries/`が存在しない・空の場合でも既存の短信一覧表示が壊れないこと(real/sampleフォールバックと同様の「なければ非表示」動作を実機/ローカルで確認)
4. 要約が存在する銘柄では短信一覧に3行要約が表示され、免責文言が含まれること
5. バッチの1回あたりの実行対象が「当日の新着分のみ」に絞られており、予算上限(件数またはコスト)の仕組みがコード上に存在すること

**成果物の置き場所**: `C:\Users\kojit\Documents\ClaudeCode\repos\stock_analyze\` 配下(このリポジトリ内)

**進め方の推奨**: まずPlanで実装方針(GitHub Actions後段 vs ローカルバッチ)を固めてから着手し、`stock-analyze-fix` Skillの必守制約に沿ってfrontend側を修正すること。投資助言ではない旨、実装物自体も念頭に置くこと。
