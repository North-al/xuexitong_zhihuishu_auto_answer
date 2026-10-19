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
DEFAULT_MODEL = ""
PROVIDERS = {
    "OpenAI": {"base_url": "https://api.openai.com/v1"},
    "DeepSeek": {"base_url": "https://api.deepseek.com"},
}
PROVIDER_NAMES = ("OpenAI", "DeepSeek", "自定义")


def provider_for(base_url: str) -> str:
    host = (base_url or "").lower()
    if "deepseek.com" in host:
        return "DeepSeek"
    if "api.openai.com" in host:
        return "OpenAI"
    return "自定义"


def _empty_profiles() -> dict:
    return {
        "OpenAI": {"api_key": "", "base_url": PROVIDERS["OpenAI"]["base_url"], "model": ""},
        "DeepSeek": {"api_key": "", "base_url": PROVIDERS["DeepSeek"]["base_url"], "model": ""},
        "自定义": {"api_key": "", "base_url": "", "model": ""},
    }


def _clean_profile(raw: dict, fallback: dict) -> dict:
    base_url = str(raw.get("base_url") or fallback["base_url"]).strip().rstrip("/")
    return {
        "api_key": str(raw.get("api_key") or "").strip(),
        "base_url": base_url,
        "model": str(raw.get("model") or "").strip(),
    }


def load_store() -> dict:
    """读取各接口各自的地址、密钥和模型。兼容以前只有一份配置的文件。"""
    profiles = _empty_profiles()
    if not CONFIG_PATH.exists():
        return {"provider": "OpenAI", "providers": profiles}
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"provider": "OpenAI", "providers": profiles}
    if not isinstance(data, dict):
        return {"provider": "OpenAI", "providers": profiles}
    saved = data.get("providers")
    if isinstance(saved, dict):
        for name in profiles:
            raw = saved.get(name)
            if isinstance(raw, dict):
                profiles[name] = _clean_profile(raw, profiles[name])
        active = str(data.get("provider") or "")
        if active not in profiles:
            active = "自定义"
        return {"provider": active, "providers": profiles}
    if any(data.get(field) for field in ("api_key", "base_url", "model")):
        name = provider_for(str(data.get("base_url") or ""))
        profiles[name] = _clean_profile(data, profiles[name])
        return {"provider": name, "providers": profiles}
    return {"provider": "OpenAI", "providers": profiles}


def save_store(provider: str, providers: dict) -> None:
    CONFIG_PATH.parent.mkdir(exist_ok=True)
    payload = {"provider": provider, "providers": providers}
    CONFIG_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def read_saved_config() -> dict:
    store = load_store()
    return store["providers"].get(store["provider"], {})


def save_config(api_key: str, base_url: str, model: str) -> None:
    store = load_store()
    name = provider_for(base_url)
    store["providers"][name] = _clean_profile(
        {"api_key": api_key, "base_url": base_url, "model": model},
        store["providers"][name],
    )
    store["provider"] = name
    save_store(store["provider"], store["providers"])


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
