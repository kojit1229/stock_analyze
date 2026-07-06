@echo off
rem ============================================================
rem  会議文字起こしツール セットアップ (初回のみ実行)
rem  - Python 仮想環境 (.venv) を作成
rem  - 依存ライブラリを pip install
rem  - silero-vad モデル (約2MB) をダウンロード
rem ============================================================
chcp 65001 >nul
cd /d "%~dp0"

echo [1/4] Python の確認...
where py >nul 2>nul
if %errorlevel%==0 (
    set "PYTHON=py -3"
) else (
    where python >nul 2>nul
    if %errorlevel% neq 0 (
        echo エラー: Python が見つかりません。
        echo https://www.python.org/downloads/ から Python 3.11 以降を
        echo インストールしてください (Add python.exe to PATH にチェック)。
        pause
        exit /b 1
    )
    set "PYTHON=python"
)
%PYTHON% --version

echo [2/4] 仮想環境を作成...
if not exist .venv (
    %PYTHON% -m venv .venv
    if %errorlevel% neq 0 (
        echo エラー: 仮想環境の作成に失敗しました。
        pause
        exit /b 1
    )
)

echo [3/4] 依存ライブラリをインストール...
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if %errorlevel% neq 0 (
    echo エラー: ライブラリのインストールに失敗しました。
    echo ネットワーク接続を確認して再実行してください。
    pause
    exit /b 1
)

echo [4/4] silero-vad モデルをダウンロード...
if not exist models mkdir models
if not exist models\silero_vad.onnx (
    powershell -NoProfile -Command ^
      "Invoke-WebRequest -Uri 'https://github.com/snakers4/silero-vad/raw/master/src/silero_vad/data/silero_vad.onnx' -OutFile 'models\silero_vad.onnx'"
    if %errorlevel% neq 0 (
        echo 注意: VAD モデルのダウンロードに失敗しました。
        echo アプリ初回起動時に自動ダウンロードを再試行します。
    )
)

echo.
echo セットアップ完了。run.bat でアプリを起動してください。
echo ※ Whisper モデル本体は初回の録音開始時に自動ダウンロードされます。
pause
