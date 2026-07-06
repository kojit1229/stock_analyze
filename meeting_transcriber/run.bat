@echo off
rem 会議文字起こしツール 起動
chcp 65001 >nul
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo エラー: 仮想環境がありません。先に setup.bat を実行してください。
    pause
    exit /b 1
)

".venv\Scripts\python.exe" main.py
if %errorlevel% neq 0 (
    echo.
    echo アプリがエラー終了しました。logs\app.log を確認してください。
    pause
)
