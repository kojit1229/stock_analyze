# CLAUDE.md — stock_analyze (決算ナビ)

日本株の決算発表予定の確認と決算短信PDFの取得・閲覧を行うWebアプリ。
フロントは**バニラJS SPA**（`frontend/`、GitHub Pages配信、iOS Safari利用者あり）、
バックエンドは標準ライブラリのみのPython（`kessan/`、ローカル実行用）。

**`kessan/` は開発凍結・ローカルデモ専用**（`MockDisclosureSource` によるサンプル
データのみ）。本番の実データは `.github/workflows/*.yml` 経由の静的パイプライン
（`frontend/data/*.json` を生成）であり、`kessan/` への実データ接続はしない
（二重実装を避けるため。詳細は README.md）。

## よく使うコマンド

```bash
npm install                      # 初回のみ (jsdom)
npm run gate                     # 合格ゲート: 構文+データ層+UIスモーク
npm run fix                      # ゲートが赤のとき、緑になるまで自動修正ループ
python -m unittest discover -s tests   # Pythonバックエンドのテスト
node scripts/check_local_api.mjs       # データ層のみの回帰チェック
```

## 絶対ルール

- **frontend/ を修正するときは、必ずスキル `stock-analyze-fix` に従うこと**
  （詳細: `.claude/skills/stock-analyze-fix/SKILL.md`）。
- 修正の完了条件は `npm run gate` が `GATE: PASS` になること。
- `scripts/`・`tests/`・`.github/` を書き換えてゲートを通すことは禁止。
- iOS Safari 制約: `new Date("文字列")` 禁止 / モジュール変数は `render()` より上で宣言 /
  入力欄を含む再描画は差分DOMパッチ。
- 依存ライブラリは追加しない（開発用 jsdom のみ例外）。

## 構造の要点

- ルーティングは `location.hash`。`frontend/app.js` の `route(name, fn)` に登録。
- `/api/*` が404のとき `frontend/local-api.js` (LocalApi) へ自動フォールバック。
  LocalApi は `frontend/data/*.json` があれば real、なければ sample モード。
- データ更新は GitHub Actions (`update-data.yml`) が `frontend/data/` を定期生成。
