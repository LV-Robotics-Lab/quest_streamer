#!/usr/bin/env python3
"""Patch upstream hand-tracking-streamer AppManager to start the camera plugin."""

from __future__ import annotations

import pathlib
import sys


START_NEEDLE = "        isStreaming = true;\n"
START_BLOCK = """        isStreaming = true;

        bool cameraOk = QuestCameraStreamerBridge.Start(QuestCameraStreamerBridge.DefaultPort);
        SendLog(cameraOk
            ? "Quest camera streamer started on TCP port 9100"
            : "Quest camera streamer did not start. Grant camera permissions, then press Start again.");
"""

HANDLE_DISCONNECT_NEEDLE = """    public void HandleDisconnection(string errorMsg)
    {
        // Prevent spamming if multiple hands fail at once
        if (!isStreaming) return;

        Debug.LogError($"[Network] TCP Disconnect Triggered: {errorMsg}");

        // 1. Reset Logic
        isStreaming = false;
"""
HANDLE_DISCONNECT_BLOCK = """        isStreaming = false;
        QuestCameraStreamerBridge.Stop();
"""

STOP_NEEDLE = """    public void StopStreaming()
    {
        isStreaming = false;
"""
STOP_BLOCK = """    public void StopStreaming()
    {
        isStreaming = false;
        QuestCameraStreamerBridge.Stop();
"""

DESTROY_NEEDLE = """    private void OnDestroy()
    {
        if (protocolDropdown != null)
        {
            protocolDropdown.onValueChanged.RemoveListener(OnProtocolChanged);
        }
    }
"""
DESTROY_BLOCK = """    private void OnDestroy()
    {
        QuestCameraStreamerBridge.Stop();
        if (protocolDropdown != null)
        {
            protocolDropdown.onValueChanged.RemoveListener(OnProtocolChanged);
        }
    }
"""


def replace_once(text: str, needle: str, replacement: str, label: str) -> str:
    count = text.count(needle)
    if count == 0:
        raise RuntimeError(f"could not find patch point: {label}")
    if count > 1:
        raise RuntimeError(f"ambiguous patch point {label}: found {count} copies")
    return text.replace(needle, replacement, 1)


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: patch_app_manager.py /path/to/AppManager.cs", file=sys.stderr)
        return 2

    path = pathlib.Path(sys.argv[1])
    text = path.read_text(encoding="utf-8-sig")

    if "QuestCameraStreamerBridge.Start" in text:
        print(f"{path}: already patched")
        return 0

    text = replace_once(text, START_NEEDLE, START_BLOCK, "start camera after isStreaming=true")
    text = replace_once(
        text,
        HANDLE_DISCONNECT_NEEDLE,
        HANDLE_DISCONNECT_NEEDLE.replace("        isStreaming = false;\n", HANDLE_DISCONNECT_BLOCK),
        "stop camera on disconnection",
    )
    text = replace_once(text, STOP_NEEDLE, STOP_BLOCK, "stop camera in StopStreaming")
    text = replace_once(text, DESTROY_NEEDLE, DESTROY_BLOCK, "stop camera in OnDestroy")

    path.write_text(text, encoding="utf-8")
    print(f"{path}: patched camera streamer lifecycle")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
