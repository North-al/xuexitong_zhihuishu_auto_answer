"""OpenAI 兼容接口配置。

优先级：调用时传入的值、runtime/openai.json、环境变量、内置默认值。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def app_root() -> Path:
    """开发时用项目目录，打包后的 exe 用 exe 所在目录。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def icon_file() -> Path:
    """窗口图标。打包后从 exe 内的资源读取。"""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", app_root())) / "icon.ico"
    return app_root() / "assets" / "icon.ico"


ROOT = app_root()
CONFIG_PATH = ROOT / "runtime" / "openai.json"
DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_MODEL = "gpt-4o-mini"
PROVIDERS = {
    "OpenAI": {
        "base_url": "https://api.openai.com/v1",
        "models": ("gpt-4o-mini", "gpt-4o", "gpt-4.1", "gpt-4.1-mini", "o4-mini"),
    },
    "DeepSeek": {
        "base_url": "https://api.deepseek.com",
        "models": ("deepseek-chat", "deepseek-reasoner"),
    },
}


def provider_for(base_url: str) -> str:
    host = (base_url or "").lower()
    if "deepseek.com" in host:
        return "DeepSeek"
    if "api.openai.com" in host:
        return "OpenAI"
    return "自定义"


def read_saved_config() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}
    return data


def save_config(api_key: str, base_url: str, model: str) -> None:
    CONFIG_PATH.parent.mkdir(exist_ok=True)
    payload = {
        "api_key": api_key.strip(),
        "base_url": (base_url.strip() or DEFAULT_BASE_URL).rstrip("/"),
        "model": model.strip() or DEFAULT_MODEL,
    }
    CONFIG_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def resolve_config(override: dict | None = None) -> dict:
    saved = read_saved_config()
    override = override or {}

    def pick(field: str, env_name: str, default: str = "") -> str:
        incoming = str(override.get(field) or "").strip()
        if incoming:
            return incoming
        from_env = os.environ.get(env_name, "").strip()
        if from_env:
            return from_env
        from_file = str(saved.get(field) or "").strip()
        if from_file:
            return from_file
        return default

    base_url = pick("base_url", "OPENAI_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
    model = pick("model", "OPENAI_MODEL", DEFAULT_MODEL)
    api_key = pick("api_key", "OPENAI_API_KEY")
    return {"api_key": api_key, "base_url": base_url, "model": model}
