---
name: stock-analyze-fix
description: 決算ナビ (stock_analyze) のフロントエンド (frontend/app.js, local-api.js, index.html, styles.css) を修正・変更・デバッグするときは必ずこのスキルに従うこと。バグ修正、機能追加、リファクタ、「画面が壊れた」「ボタンが効かない」「表示がおかしい」「ゲートが落ちる」等の依頼、および scripts/harness/fix_loop.sh 経由の自動修正時に適用する。合格ゲートの実行方法と、iOS Safari 由来の必守制約を定義する。
---

# stock-analyze-fix — フロントエンド修正の作法

このプロジェクトのフロントエンドは**バニラ JS の SPA**（依存ライブラリなし、GitHub Pages 配信、iOS Safari 利用者あり）。
修正の合否は人の感覚ではなく**合格ゲート**で判定する。

## 合格ゲート（作業の開始時と終了時に必ず実行）

```bash
bash scripts/harness/gate.sh
```

内容: ①構文チェック (`node --check`) ②データ層回帰 (`scripts/check_local_api.mjs`)
③UIスモーク (`scripts/smoke_ui.mjs` — 全ルート描画＋代表ボタン操作を jsdom で実測)。
`GATE: PASS` が出るまで作業は完了ではない。初回は `npm install`（jsdom）が必要。

## 変更してよいファイル / 禁止ファイル

- 修正対象: `frontend/app.js`, `frontend/local-api.js`, `frontend/index.html`, `frontend/styles.css`
- **変更禁止**: `scripts/`（ゲート・スモークテスト本体）, `tests/`, `.github/`, `kessan/`（依頼が明示的にバックエンドの場合を除く）
- **ゲートやテストを書き換えて PASS させることは絶対にしない。** 落ちるならアプリ本体を直す。

## 必守制約（iOS Safari 安全策 — 違反は実機で壊れる）

1. **`new Date("文字列")` 禁止。** 日付パースは成分分解で行う:
   `const [y,m,d] = s.split("-").map(Number); new Date(y, m-1, d);`
2. **モジュールレベル変数は `render()` より上で宣言**（巻き上げ事故防止）。
3. **検索/入力欄を含む再描画は差分 DOM パッチ**で行い、`innerHTML` 全置換で入力中のフォーカスを奪わない。
4. 依存ライブラリを追加しない（バニラ JS を維持）。npm 依存は開発用 (jsdom) のみ。

## アプリ構造の要点

- ルーター: `location.hash` ベース。ルートは `home / schedule / mystocks / disclosures / stock/:code / analysis / compare`。各ルートは `route(name, fn)` で登録され、`render()` が dispatch する。
- API 層: `api.req()` は `/api/*` へ fetch し、静的ホスティングで 404 なら **LocalApi へ自動フォールバック**。GitHub Pages / `file:` では最初からローカルモード。
- LocalApi: 初回 `handle()` で `frontend/data/*.json` を読み、あれば real モード、なければ sample モード。この二重経路を壊さないこと。
- モーダルは `modal(html)` が `.modal-backdrop` を body 直下に作る。閉じ処理を必ず残す。

## 進め方

1. `bash scripts/harness/gate.sh` を実行し、失敗内容を再現・確認する。
2. エラーメッセージから原因箇所を特定し、**最小限の差分**で修正する。無関係な整形・リファクタはしない。
3. 再度ゲートを実行。`GATE: PASS` になるまで 1〜2 を繰り返す。
4. 何をなぜ直したか（原因→対処）を 1〜3 行で要約して報告する。
