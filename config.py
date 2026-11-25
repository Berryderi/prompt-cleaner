"""Settings from config.json, with defaults. Only loopback model URLs are accepted."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.json"
LOG_DIR = ROOT / "logs"
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})

DEFAULT_TERMINALS = (
    "windowsterminal.exe", "openconsole.exe", "conhost.exe", "cmd.exe", "powershell.exe",
    "pwsh.exe", "wezterm-gui.exe", "alacritty.exe", "mintty.exe",
)


class ConfigError(ValueError):
    pass


# Ollama "cloud" models ("gpt-oss:120b-cloud", "x:cloud") use the same local API but run on ollama.com.
_CLOUD_TAG_RE = re.compile(r"(^|[:\-_/])cloud($|[:\-_/])", re.IGNORECASE)


@dataclass(frozen=True)
class Config:
    hotkey: str = "ctrl+shift+space"
    model: str = "qwen3:8b"
    ollama_url: str = "http://127.0.0.1:11434"
    timeout_s: float = 6.0
    warmup_timeout_s: float = 90.0
    keep_alive: str = "30m"
    ps_timeout_s: float = 1.0          # GET /api/ps before every model call
    evict_other_models: bool = False   # False: never call the model while another holder's model is loaded
    terminal_processes: tuple[str, ...] = field(default=DEFAULT_TERMINALS)

    def __post_init__(self) -> None:
        if _CLOUD_TAG_RE.search(self.model):
            raise ConfigError(f"model {self.model!r} is an Ollama cloud model: it would send your text off this PC")


def load(path: Path = CONFIG_PATH) -> Config:
    data: dict = {}
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
    known = Config.__dataclass_fields__
    unknown = set(data) - set(known)
    if unknown:
        raise ConfigError(f"unknown config keys: {sorted(unknown)}")
    if "terminal_processes" in data:
        data["terminal_processes"] = tuple(p.lower() for p in data["terminal_processes"])
    cfg = Config(**data)
    host = urlparse(cfg.ollama_url).hostname
    if host not in LOOPBACK_HOSTS:
        raise ConfigError(f"ollama_url must be loopback (text never leaves this PC), got host {host!r}")
    if cfg.timeout_s <= 0:
        raise ConfigError("timeout_s must be positive")
    return cfg
