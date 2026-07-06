# 決算ナビ — 決算短信自動取得・決算日程管理アプリ

日本株に投資する個人投資家向けの、**決算発表予定の確認**と**決算短信PDFの自動取得・閲覧**を効率化するWebアプリのMVPです。

[要件定義](docs/requirements.md) に基づき、MVPスコープ（決算日程一覧・時価総額での絞り込み・マイ銘柄登録・決算短信の自動取得と閲覧）を実装しています。

## 🌐 GitHub Pages で今すぐ試す

**https://kojit1229.github.io/stock_analyze/**

サーバ不要の**静的デモモード**で全機能（決算予定の絞り込み・マイ銘柄登録・決算短信の取得・PDF閲覧）が動作します。データはブラウザの localStorage に保存され、PDF はブラウザ内で生成されます。`main` へのpushで [GitHub Actions](.github/workflows/pages.yml) が自動デプロイします。

> 初回は Actions の「Deploy to GitHub Pages」ワークフローが一度成功する必要があります（Pages設定は workflow が自動で有効化します）。

## 特徴

- **依存ライブラリゼロ** — Python 標準ライブラリ（`http.server` + `sqlite3`）のみで動作。`pip install` 不要。
- **バニラJS SPA** — ビルド不要のフロントエンド。
- **2つの動作モード** — Pythonサーバモード（SQLite永続化）と静的デモモード（GitHub Pages / localStorage）。`/api/*` が見つからない環境では自動で静的モードにフォールバック。
- 4つの主要画面 + 銘柄詳細 + 決算短信詳細（PDFビューア）。

## 動作環境

- Python 3.8+（3.11 で検証）
- モダンブラウザ

## 起動

```bash
python run.py            # http://127.0.0.1:8000 で起動
python run.py --port 9000
```

初回起動時に SQLite DB を初期化し、代表的な日本株24銘柄のサンプルデータ
（時価総額は要件4.2の全レンジを網羅）と決算予定を投入します。

## 使い方

1. **決算予定** 画面で「今日 / 明日 / 今週 / 来週 / 1ヶ月」や時価総額レンジ、
   銘柄コード・銘柄名・市場・業種で絞り込み。列ヘッダで並び替え。
2. 気になる銘柄を **＋登録** して「マイ銘柄」に追加。
3. ヘッダの **⟳ 取得**（またはマイ銘柄画面の取得ボタン）で、登録銘柄のうち
   決算発表日を迎えたものの**決算短信PDFを自動取得**。
4. **決算短信** 画面でPDFを閲覧。開くと閲覧済みになり、コメントを保存できます。

> 注: MVPでは外部データソース（TDnet等）へは接続せず、`MockDisclosureSource`
> がサンプルPDFを生成します。取得ロジックは `DisclosureSource` インターフェースで
> 差し替え可能な設計のため、将来 TDnet 実装へ置き換えられます。

## アーキテクチャ

```
run.py                  起動エントリポイント
kessan/
  config.py             パス・定数（環境変数で上書き可）
  db.py                 SQLite 接続とスキーマ（要件6のデータ項目に対応）
  market_cap.py         時価総額レンジ定義・判定（要件4.2）
  models.py             データアクセス層（検索・絞り込み・集計）
  seed.py               サンプル銘柄・決算予定
  fetcher.py            決算短信の自動取得（差し替え可能なソース）
  pdfgen.py             最小PDFジェネレータ（サンプルPDF生成）
  api.py                APIハンドラ
  server.py             HTTPサーバ・ルーティング・静的配信
frontend/
  index.html / app.js / styles.css   バニラJS SPA
  local-api.js          静的モード用のブラウザ内API (GitHub Pages 用)
tests/test_api.py       統合テスト（サーバを起動して検証）
scripts/smoke.py        起動スモークテスト
scripts/check_local_api.mjs  静的モードの回帰チェック (Node)
```

### データモデル（要件6）

| テーブル | 対応 | 主な項目 |
|---|---|---|
| `stocks` | 6.1 銘柄データ | コード / 銘柄名 / 市場 / 業種 / 時価総額 / 上場区分 |
| `earnings_schedule` | 6.2 決算予定 | 決算発表予定日 / 決算種別 / 発表予定時刻 / 取得元 |
| `disclosures` | 6.3 決算短信 | タイトル / PDF URL / 保存先 / 資料種別 / 公開・取得日時 / 閲覧フラグ / コメント |
| `my_stocks` | 6.4 登録銘柄 | 保有区分 / 重要度 / メモ / 通知設定 / 登録日時 / 最終確認日時 |

## API

| メソッド | パス | 説明 |
|---|---|---|
| GET | `/api/home` | ホーム画面サマリ |
| GET | `/api/schedule` | 決算予定一覧（`date_range`,`code`,`name`,`sector`,`market`,`cap_range`,`cap_min`,`cap_max`,`sort`,`order`）|
| GET | `/api/stocks/{code}` | 銘柄詳細 |
| GET/POST | `/api/mystocks` | マイ銘柄 一覧 / 登録 |
| PATCH/DELETE | `/api/mystocks/{code}` | マイ銘柄 更新 / 削除 |
| GET | `/api/disclosures` | 決算短信一覧（`unread`,`code`,`doc_type`）|
| GET | `/api/disclosures/{id}` | 決算短信詳細 |
| PATCH | `/api/disclosures/{id}` | 閲覧フラグ / コメント更新 |
| POST | `/api/disclosures/{id}/read` | 閲覧済みにする |
| GET | `/api/disclosures/{id}/pdf` | PDF配信（`?download=1` でダウンロード）|
| POST | `/api/fetch` | 登録銘柄の決算短信を取得 |
| GET | `/api/cap-ranges` `/api/sectors` `/api/markets` `/api/meta` | マスタ・メタ情報 |

## テスト

```bash
python -m unittest discover -s tests -v   # 統合テスト（31ケース）
python scripts/smoke.py                   # 起動スモークテスト
node scripts/check_local_api.mjs          # 静的モードの回帰チェック
```

## MVPで満たす受け入れ条件（要件11）

- [x] 決算予定一覧を確認できる
- [x] 時価総額で銘柄を絞り込める（プリセット6段階＋任意レンジ）
- [x] 銘柄をマイ銘柄に登録できる
- [x] 登録銘柄の決算短信PDFが自動取得される
- [x] 取得済みPDFをアプリ内で開ける
- [x] 取得済み/未取得・閲覧済み/未閲覧が分かる
- [x] 決算日程とPDF取得結果の最終更新日時が分かる

## 今後の拡張（要件8）

`DisclosureSource` を TDnet 実装に差し替え、通知機能・PDF数値抽出・AI要約・
グラフ表示・株価連携などを段階的に追加できる設計です。
