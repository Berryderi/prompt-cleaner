"""prompt-cleaner: Ctrl+Shift+Space cleans the selected text (or, in a terminal, a dictation box).

Run through start.cmd (pythonw). Logs: logs/cleaner.jsonl (counts and timings only, never text).
"""
from __future__ import annotations

import argparse
import dataclasses
import logging
import sys
import threading

import config
import jsonlog
import winapi
from cleaner import Cleaner
from model import OllamaClient


def handle_hotkey(cfg: config.Config, cleaner: Cleaner) -> None:
    winapi.mask_menu_activation()  # first: Alt is still held while this runs
    hwnd, proc = winapi.foreground()
    terminal = winapi.is_terminal(proc, cfg.terminal_processes)
    try:
        if terminal:
            import dictation_box  # tkinter is only needed here

            jsonlog.event("hotkey", mode="terminal-box", process=proc or "unknown")
            text = dictation_box.ask()
            if text is None:
                jsonlog.event("cancelled", mode="terminal-box")
                return
            if hwnd and not winapi.focus_window(hwnd):
                jsonlog.event("focus_not_restored", level=logging.WARNING)
                return
        else:
            jsonlog.event("hotkey", mode="selection", process=proc)
            text = winapi.grab_selection()
            if text is None or not text.strip():
                jsonlog.event("no_selection", process=proc)
                return
        result = cleaner.clean(text)
        winapi.paste_text(result.text)
        jsonlog.event("pasted", mode="terminal-box" if terminal else "selection", path=result.path)
    except OSError as exc:
        jsonlog.event("os_error", level=logging.ERROR, error_type=type(exc).__name__, detail=str(exc)[:120])


def main() -> int:
    ap = argparse.ArgumentParser(description="prompt-cleaner")
    ap.add_argument("--hotkey", help="override config.json hotkey, e.g. ctrl+alt+j")
    ap.add_argument("--no-warmup", action="store_true", help="do not touch the model at startup (smoke tests)")
    ap.add_argument("--no-model", action="store_true", help="rules-only, never call the model (smoke tests)")
    args = ap.parse_args()
    jsonlog.setup(config.LOG_DIR)
    sys.excepthook = lambda *a: jsonlog.event("crash", level=logging.CRITICAL, exc_info=a)
    try:
        cfg = config.load()
        if args.hotkey:
            cfg = dataclasses.replace(cfg, hotkey=args.hotkey)
        winapi.parse_hotkey(cfg.hotkey)
    except (config.ConfigError, ValueError, OSError) as exc:
        jsonlog.event("bad_config", level=logging.CRITICAL, detail=str(exc)[:200])
        return 2

    mutex = winapi.acquire_single_instance()
    if mutex is None:
        jsonlog.event("already_running")
        return 0

    client = OllamaClient(cfg)
    if not (args.no_warmup or args.no_model):
        threading.Thread(target=client.warm_up, name="warmup", daemon=True).start()
    cleaner = Cleaner(None if args.no_model else client)
    jsonlog.event("started", hotkey=cfg.hotkey, model=cfg.model)
    try:
        winapi.run_hotkey_loop(cfg.hotkey, lambda: handle_hotkey(cfg, cleaner))
    except OSError as exc:
        jsonlog.event("hotkey_failed", level=logging.CRITICAL, detail=str(exc)[:200])
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
