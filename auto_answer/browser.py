"""查找 Chrome 与 Edge 中已经打开的超星「作业作答」窗口。"""

from __future__ import annotations

import ctypes
import re
from ctypes import wintypes
from urllib.parse import parse_qs, urlparse

WINDOW_TITLE = re.compile(r"作业作答")
SKIP_LINES = {"暂时保存", "提交", "作业", "交卷", "筛选", "全部", "已完成", "未完成"}


class _FLASHWINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.UINT),
        ("hwnd", wintypes.HWND),
        ("dwFlags", wintypes.DWORD),
        ("uCount", wintypes.UINT),
        ("dwTimeout", wintypes.DWORD),
    ]


def browser_name(title: str) -> str:
    cleaned = title.replace("\u200b", "")
    if "Google Chrome" in cleaned:
        return "Chrome"
    if "Microsoft Edge" in cleaned or cleaned.endswith(" - Edge"):
        return "Edge"
    return ""


def list_homework_windows() -> list[dict]:
    import uiautomation as auto

    auto.SetGlobalSearchTimeout(1)
    found: list[dict] = []
    for window in auto.GetRootControl().GetChildren():
        if window.ControlTypeName != "WindowControl":
            continue
        title = window.Name or ""
        browser = browser_name(title)
        if not browser or not WINDOW_TITLE.search(title.replace("\u200b", "")):
            continue
        found.append(
            {
                "title": title,
                "browser": browser,
                "handle": int(window.NativeWindowHandle),
            }
        )
    return found


def window_from_handle(handle: int):
    import uiautomation as auto

    auto.SetGlobalSearchTimeout(1)
    for window in auto.GetRootControl().GetChildren():
        if window.ControlTypeName != "WindowControl":
            continue
        if int(window.NativeWindowHandle) == int(handle):
            return window
    return None


def flash_window(handle: int) -> None:
    info = _FLASHWINFO(ctypes.sizeof(_FLASHWINFO), wintypes.HWND(int(handle)), 3, 8, 0)
    ctypes.windll.user32.FlashWindowEx(ctypes.byref(info))


def inspect_homework_window(handle: int, flash: bool = True) -> dict:
    """读取选中窗口的作业名称、题量和地址，并闪烁该窗口。"""
    window = window_from_handle(handle)
    if window is None:
        return {"ok": False, "message": "选中的窗口已经关闭"}
    title = window.Name or ""
    url = _address_url(window)
    name, count = _page_summary(window)
    if flash:
        flash_window(handle)
    minimized = bool(ctypes.windll.user32.IsIconic(wintypes.HWND(int(handle))))
    return {
        "ok": True,
        "browser": browser_name(title),
        "title": title,
        "url": url,
        "work_id": _query_value(url, "workId"),
        "course_id": _query_value(url, "courseId"),
        "name": name,
        "count": count,
        "minimized": minimized,
        "flashed": flash,
    }


def copy_page_text(handle: int) -> str:
    """从指定作业窗口复制当前页题目文本。不会点击提交。完成后回到原来的窗口。"""
    import uiautomation as auto

    previous = _foreground_handle()
    window = window_from_handle(handle)
    if window is None:
        raise SystemExit("选中的窗口已经关闭，请重新刷新")
    try:
        window.SetActive(waitTime=0.3)
        name, _count = _page_summary(window)
        _click_named_text(window, name)
        document = window.DocumentControl(searchDepth=25, Name="作业作答")
        if document.Exists(maxSearchSeconds=1):
            document.SetFocus()
        auto.SendKeys("{Ctrl}a", waitTime=0.25, charMode=False)
        auto.SendKeys("{Ctrl}c", waitTime=0.4, charMode=False)
        text = auto.GetClipboardText() or ""
        _click_named_text(window, name)
        if len(text.strip()) < 20:
            raise SystemExit("没有从当前作业页复制到题目，请确认页面停留在作答界面")
        return text
    finally:
        _restore_foreground(previous)


def _foreground_handle() -> int:
    return int(ctypes.windll.user32.GetForegroundWindow() or 0)


def _restore_foreground(handle: int) -> None:
    if not handle:
        return
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    if _foreground_handle() == int(handle):
        return
    hwnd = wintypes.HWND(int(handle))
    current = kernel32.GetCurrentThreadId()
    foreign = user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), None)
    target = user32.GetWindowThreadProcessId(hwnd, None)
    user32.AttachThreadInput(current, foreign, True)
    user32.AttachThreadInput(current, target, True)
    user32.ShowWindow(hwnd, 9)
    user32.SetForegroundWindow(hwnd)
    user32.AttachThreadInput(current, target, False)
    user32.AttachThreadInput(current, foreign, False)


def _click_named_text(window, name: str) -> None:
    if not name:
        return
    label = window.TextControl(searchDepth=18, Name=name)
    if label.Exists(maxSearchSeconds=0.6):
        label.Click(simulateMove=False, waitTime=0.1)


def describe_inspection(info: dict) -> str:
    if not info.get("ok"):
        return info.get("message") or "没有读到窗口信息"
    parts = [info.get("browser") or "浏览器"]
    if info.get("name"):
        parts.append(info["name"])
    if info.get("count"):
        parts.append(f"题量 {info['count']}")
    if info.get("work_id"):
        parts.append(f"workId {info['work_id']}")
    if info.get("minimized"):
        parts.append("窗口已最小化")
    if info.get("flashed"):
        parts.append("任务栏已闪烁")
    lines = [" · ".join(parts)]
    if info.get("url"):
        lines.append(info["url"])
    return "\n".join(lines)


def _address_url(window) -> str:
    for edit in _descendants(window, "EditControl", depth=8):
        value = _value_of(edit)
        if value and ("chaoxing.com" in value or "workId=" in value or value.startswith("http")):
            if not value.startswith("http"):
                return "https://" + value
            return value
    return ""


def _page_summary(window) -> tuple[str, str]:
    import uiautomation as auto

    document = window.DocumentControl(searchDepth=25, Name="作业作答")
    if not document.Exists(maxSearchSeconds=1):
        return "", ""
    pattern = document.GetPattern(auto.PatternId.TextPattern)
    if not pattern:
        return "", ""
    text = pattern.DocumentRange.GetText(900) or ""
    count = ""
    matched = re.search(r"题量\s*[:：]\s*(\d+)", text)
    if matched:
        count = matched.group(1)
    name = ""
    for line in text.splitlines():
        line = line.replace("\ufffc", "").strip()
        if not line or line in SKIP_LINES or line.startswith("题量"):
            continue
        if not re.search(r"[\u4e00-\u9fffA-Za-z0-9]", line):
            continue
        name = line
        break
    return name, count


def _value_of(control) -> str:
    import uiautomation as auto

    pattern = control.GetPattern(auto.PatternId.ValuePattern)
    if not pattern:
        return ""
    return pattern.Value or ""


def _query_value(url: str, key: str) -> str:
    if not url:
        return ""
    parsed = urlparse(url if "://" in url else "https://" + url)
    values = parse_qs(parsed.query).get(key) or []
    return values[0] if values else ""


def _descendants(control, type_name: str, depth: int):
    found = []

    def walk(node, level: int) -> None:
        if level > depth:
            return
        if level > 0 and node.ControlTypeName == type_name:
            found.append(node)
        for child in node.GetChildren():
            walk(child, level + 1)

    walk(control, 0)
    return found


def connect_window(title: str | None = None, handle: int | None = None):
    import uiautomation as auto

    auto.SetGlobalSearchTimeout(2)
    if handle:
        window = window_from_handle(handle)
        if window is None:
            raise SystemExit("选中的窗口已经关闭，请重新刷新")
        return window
    if title:
        window = auto.WindowControl(searchDepth=1, Name=title)
        if not window.Exists(maxSearchSeconds=2):
            raise SystemExit(f"没有找到窗口：{title}")
        return window
    found = list_homework_windows()
    if len(found) == 1:
        return auto.WindowControl(searchDepth=1, Name=found[0]["title"])
    if not found:
        raise SystemExit("没有找到 Chrome 或 Edge 中标题包含「作业作答」的窗口")
    names = "；".join(item["title"] for item in found)
    raise SystemExit(f"找到多个作业窗口，请在界面中指定一个：{names}")
