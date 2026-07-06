"""config.json の読み書き。

存在しなければ既定値で新規作成する。既存ファイルに無いキーは
既定値で補完するため、バージョンアップでキーが増えても壊れない。
"""

from __future__ import annotations

import copy
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.json"

DEFAULTS: dict = {
    # faster-whisper のモデル名。"small" / "medium" /
    # "kotoba-tech/kotoba-whisper-v2.0-faster" などをそのまま指定できる
    "model": "small",
    "compute_type": "int8",
    "device": "cpu",
    "language": "ja",
    "initial_prompt": "以下はオンライン会議の文字起こしです。",
    "beam_size": 2,
    "vad": {
        "min_silence_ms": 600,   # この長さ無音が続いたらセグメント確定
        "min_speech_ms": 250,    # これ未満の短い音は捨てる
        "max_segment_s": 30,     # 連続発話の強制分割長
        "speech_threshold": 0.5, # silero-vad の発話判定しきい値
        "pre_roll_ms": 200,      # 発話開始前に遡って含める長さ
    },
    # 既定はユーザーの Documents/transcripts
    "output_dir": str(Path.home() / "Documents" / "transcripts"),
    "markdown_output": False,
    "follow_default_output": True,
    "mic_device_name": None,
    "loopback_device_name": None,
    "always_on_top": False,
}


def _merge(defaults: dict, loaded: dict) -> dict:
    merged = copy.deepcopy(defaults)
    for key, value in loaded.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_config(path: Path = CONFIG_PATH) -> dict:
    if path.exists():
        try:
            with open(path, encoding="utf-8") as f:
                loaded = json.load(f)
            return _merge(DEFAULTS, loaded)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("config.json の読み込みに失敗したため既定値を使用: %s", e)
    config = copy.deepcopy(DEFAULTS)
    save_config(config, path)
    return config


def save_config(config: dict, path: Path = CONFIG_PATH) -> None:
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
            f.write("\n")
    except OSError as e:
        logger.error("config.json の保存に失敗: %s", e)
