"""音声デバイスの列挙と既定デバイス取得 (PyAudioWPatch / WASAPI)。

- マイク入力: 通常の入力デバイス
- 相手音声: WASAPI ループバックデバイス (再生音のキャプチャ)
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def list_input_devices(pa) -> list[dict]:
    """マイクなどの入力デバイス一覧 (ループバックは除く)。"""
    devices = []
    try:
        wasapi_index = pa.get_host_api_info_by_type(_pyaudio().paWASAPI)["index"]
    except OSError:
        wasapi_index = None
    for i in range(pa.get_device_count()):
        info = pa.get_device_info_by_index(i)
        if info.get("maxInputChannels", 0) <= 0:
            continue
        if info.get("isLoopbackDevice", False):
            continue
        # WASAPI ホストの入力のみに絞る(重複列挙を避ける)
        if wasapi_index is not None and info.get("hostApi") != wasapi_index:
            continue
        devices.append(info)
    return devices


def list_loopback_devices(pa) -> list[dict]:
    """WASAPI ループバックデバイス一覧。"""
    try:
        return list(pa.get_loopback_device_info_generator())
    except OSError as e:
        logger.error("ループバックデバイスの列挙に失敗: %s", e)
        return []


def get_default_input(pa) -> dict | None:
    try:
        return pa.get_default_input_device_info()
    except OSError:
        return None


def get_default_loopback(pa) -> dict | None:
    """既定の再生デバイスに対応するループバックデバイス。"""
    try:
        return pa.get_default_wasapi_loopback()
    except OSError as e:
        logger.error("既定ループバックデバイスの取得に失敗: %s", e)
        return None


def find_device_by_name(devices: list[dict], name: str) -> dict | None:
    for info in devices:
        if info.get("name") == name:
            return info
    return None


def _pyaudio():
    import pyaudiowpatch

    return pyaudiowpatch
