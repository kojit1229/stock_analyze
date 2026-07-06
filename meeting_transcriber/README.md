# オンライン会議リアルタイム文字起こしツール

Zoom / Teams / Google Meet 等のオンライン会議の音声を、Windows デスクトップ上で
**リアルタイムに文字起こし**し、会議終了後に txt ファイルとして保存するツールです。

- 会議アプリ非依存(音声レイヤーで完結)
- **完全ローカル処理** — 音声・テキストを外部サービスへ送信しません
  (初回のみ Whisper / VAD モデルのダウンロードで通信が発生します)
- 「自分」(マイク) と「相手」(PC 再生音の WASAPI ループバック) を
  タイムスタンプ・話者ラベル付きで記録

要件定義・設計は [docs/design.md](docs/design.md) を参照。

## 動作環境

- Windows 10 / 11
- Python 3.11 以上
- Bluetooth イヤホンマイク対応(A2DP→HFP 切替による音質低下は Bluetooth の仕様で、
  文字起こし品質への実害はありません)

## セットアップ(初回のみ)

1. [Python 3.11+](https://www.python.org/downloads/) をインストール
   (インストーラで **Add python.exe to PATH** にチェック)
2. `setup.bat` をダブルクリック
   - 仮想環境の作成、依存ライブラリのインストール、VAD モデルの取得を行います

## 使い方

1. `run.bat` をダブルクリックして起動
2. マイクと相手音声(ループバック)のデバイスを選択
   - 通常は「既定のマイク」「既定の再生デバイスに追従」のままで OK
3. **● 録音開始** をクリック
   - 初回は Whisper モデル(数百 MB)の自動ダウンロードがあるため、
     認識開始まで時間がかかります
4. 会議終了後 **■ 録音停止** をクリック
   - `{保存先}/transcript_YYYYMMDD_HHMM.txt` に保存され、パスがステータスに表示されます

発話確定ごとにファイルへ逐次追記しているため、アプリが異常終了しても
それまでの文字起こしは失われません。

## 設定 (config.json)

初回起動時に自動生成されます。主な項目:

| キー | 説明 |
|---|---|
| `model` | Whisper モデル。`small` / `medium` / `kotoba-tech/kotoba-whisper-v2.0-faster` など |
| `compute_type` | `int8`(CPU 推奨)。GPU なら `float16` |
| `device` | `cpu` / `cuda` |
| `initial_prompt` | 業務用語のヒント。製品名・略語を書くと固有名詞の認識精度が上がる |
| `output_dir` | 保存先フォルダ(UI の「保存先...」からも変更可) |
| `markdown_output` | `true` で txt と同時に Markdown も出力 |
| `vad.min_silence_ms` | この長さの無音で発話を区切る(既定 600ms) |

### モデル選定の目安

| 環境 | 推奨モデル |
|---|---|
| CPU のみ(まず試す) | `small` |
| CPU のみ(本命候補) | `kotoba-tech/kotoba-whisper-v2.0-faster`(日本語特化) |
| NVIDIA GPU あり | `large-v3` + `device: cuda` |

## 検証ツール

```bat
.venv\Scripts\activate

rem M1: デバイス一覧と2系統同時録音の確認
python tools\record_poc.py --list
python tools\record_poc.py --seconds 15

rem M2: モデル比較 (実時間比 0.5 未満ならリアルタイム処理に余裕あり)
python tools\compare_models.py poc_out\loopback.wav
```

## トラブルシューティング

| 症状 | 対処 |
|---|---|
| 相手の声が文字起こしされない | 会議アプリの出力先と「相手音声」デバイスの一致を確認。「既定の再生デバイスに追従」を推奨。30秒無音が続くとステータスに警告が出ます |
| Bluetooth が切断された | 自動で再接続を最大5回試みます(指数バックオフ)。復帰しない場合は録音を停止→デバイス再検出→再開 |
| 認識が遅い(表示まで5秒超) | `model` を `small` に。`kotoba-tech/kotoba-whisper-v2.0-faster` も CPU で高速です |
| エラーで落ちる | `logs\app.log` を確認 |

## アーキテクチャ

```
[マイク入力]────┐
                ├→ CaptureThread×2 → silero-vad で発話セグメント化
[ループバック]──┘         │
                          ▼
                 セグメントキュー (queue.Queue)
                          │
                          ▼
                 ASRWorker×1 (faster-whisper, int8/CPU)
                          │
                          ├→ UI へ Qt Signal (逐次表示, start_ts でソート)
                          └→ TranscriptWriter (txt へ逐次追記)
```
