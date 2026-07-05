#!/usr/bin/env python3
"""アプリ起動エントリポイント。

    python run.py [--host HOST] [--port PORT]

DB 初期化・サンプルデータ投入を行い、HTTP サーバを起動する。
"""
import argparse

from kessan import server


def main():
    parser = argparse.ArgumentParser(description="決算短信自動取得・決算日程管理アプリ")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    server.run(args.host, args.port)


if __name__ == "__main__":
    main()
