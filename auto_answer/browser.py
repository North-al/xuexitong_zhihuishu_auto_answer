"""查找 Chrome 与 Edge 中已经打开的超星 / 智慧树作答窗口。"""

from __future__ import annotations

import ctypes
import re
from ctypes import wintypes
from urllib.parse import parse_qs, urlparse

CHAOXING_TITLE = re.compile(r"作业作答")
ZHIHUISHU_TITLE = re.compile(r"智慧树|作业考试")
SKIP_LINES = {"暂时保存", "提交", "作业", "交卷", "筛选", "全部", "已完成", "未完成", "提交作业", "暂存作业"}

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


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


def detect_platform(title: str, url: str = "") -> str:
    cleaned = (title or "").replace("\u200b", "")
    host = (url or "").lower()
    if "zhihuishu.com" in host:
        return "zhihuishu"
    if "chaoxing.com" in host:
        return "chaoxing"
    if "作业考试" in cleaned or "智慧树" in cleaned or ZHIHUISHU_TITLE.search(cleaned):
        return "zhihuishu"
    if CHAOXING_TITLE.search(cleaned):
        return "chaoxing"
    return ""


def list_homework_windows() -> list[dict]:
    """兼容旧名：列出超星与智慧树作答窗口。"""
    return list_answer_windows()


def list_answer_windows() -> list[dict]:
    """用 Win32 枚举顶层窗口，避免 UIA 扫整棵控件树导致卡死。"""
    found: list[dict] = []

    def _callback(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        title = buffer.value or ""
        browser = browser_name(title)
        if not browser:
            return True
        platform = detect_platform(title)
        if not platform:
            return True
        found.append(
            {
                "title": title,
                "browser": browser,
                "handle": int(hwnd),
                "platform": platform,
            }
        )
        return True

    user32.EnumWindows(WNDENUMPROC(_callback), 0)
    return found


def window_from_handle(handle: int):
    """按句柄取窗口，避免再枚举全部顶层窗口。"""
    import uiautomation as auto

    auto.SetGlobalSearchTimeout(0.3)
    try:
        control = auto.ControlFromHandle(int(handle))
    except Exception:
        return None
    if control is None:
        return None
    try:
        if control.ControlTypeName != "WindowControl":
            control = control.GetTopLevelControl()
    except Exception:
        pass
    return control


def flash_window(handle: int) -> None:
    info = _FLASHWINFO(ctypes.sizeof(_FLASHWINFO), wintypes.HWND(int(handle)), 3, 8, 0)
    user32.FlashWindowEx(ctypes.byref(info))


def inspect_homework_window(handle: int, flash: bool = True) -> dict:
    """读取选中窗口的作业名称、题量和地址，并闪烁该窗口。"""
    import uiautomation as auto

    auto.SetGlobalSearchTimeout(0.4)
    window = window_from_handle(handle)
    if window is None:
        return {"ok": False, "message": "选中的窗口已经关闭"}
    title = window.Name or ""
    url = _address_url(window)
    platform = detect_platform(title, url) or detect_platform(title) or "chaoxing"
    try:
        if platform == "zhihuishu":
            name, count = _zhihuishu_summary(window)
            work_id = _zhihuishu_exam_id(url)
        else:
            name, count = _page_summary(window)
            work_id = _query_value(url, "workId")
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": True,
            "browser": browser_name(title),
            "title": title,
            "url": url,
            "platform": platform,
            "work_id": _zhihuishu_exam_id(url) if platform == "zhihuishu" else _query_value(url, "workId"),
            "course_id": _query_value(url, "courseId"),
            "name": "",
            "count": "",
            "minimized": bool(user32.IsIconic(wintypes.HWND(int(handle)))),
            "flashed": False,
            "message": f"已识别窗口，详情读取较慢已跳过：{exc}",
        }
    if flash:
        flash_window(handle)
    minimized = bool(user32.IsIconic(wintypes.HWND(int(handle))))
    return {
        "ok": True,
        "browser": browser_name(title),
        "title": title,
        "url": url,
        "platform": platform,
        "work_id": work_id,
        "course_id": _query_value(url, "courseId"),
        "name": name,
        "count": count,
        "minimized": minimized,
        "flashed": flash,
    }


def copy_page_text(handle: int, log=print) -> str:
    """从指定作业窗口复制题目文本。不会点击提交。完成后回到原来的窗口。"""
    import uiautomation as auto

    previous = _foreground_handle()
    window = window_from_handle(handle)
    if window is None:
        raise SystemExit("选中的窗口已经关闭，请重新刷新")
    try:
        window.SetActive(waitTime=0.3)
        url = _address_url(window)
        platform = detect_platform(window.Name or "", url)
        if platform == "zhihuishu":
            from auto_answer.zhihuishu import copy_all_question_text

            return copy_all_question_text(handle, log=log)
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
    return int(user32.GetForegroundWindow() or 0)


def _restore_foreground(handle: int) -> None:
    if not handle:
        return
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
    if label.Exists(maxSearchSeconds=0.4):
        label.Click(simulateMove=False, waitTime=0.1)


def describe_inspection(info: dict) -> str:
    if not info.get("ok"):
        return info.get("message") or "没有读到窗口信息"
    platform = info.get("platform") or "chaoxing"
    label = "智慧树" if platform == "zhihuishu" else "超星"
    parts = [info.get("browser") or "浏览器", label]
    if info.get("name"):
        parts.append(info["name"])
    if info.get("count"):
        parts.append(f"题量 {info['count']}")
    if info.get("work_id"):
        key = "examId" if platform == "zhihuishu" else "workId"
        parts.append(f"{key} {info['work_id']}")
    if info.get("minimized"):
        parts.append("窗口已最小化")
    if info.get("flashed"):
        parts.append("任务栏已闪烁")
    lines = [" · ".join(parts)]
    if info.get("url"):
        lines.append(info["url"])
    if info.get("message"):
        lines.append(str(info["message"]))
    return "\n".join(lines)


def _address_url(window) -> str:
    """优先按地址栏名称定位，避免深度遍历整个 Chrome 树。"""
    import uiautomation as auto

    auto.SetGlobalSearchTimeout(0.25)
    for name in ("地址和搜索栏", "Address and search bar", "地址栏"):
        edit = window.EditControl(searchDepth=8, Name=name)
        if edit.Exists(maxSearchSeconds=0.25):
            value = _value_of(edit)
            if value:
                return value if value.startswith("http") else "https://" + value
    # 浅层兜底：只看前几层 Edit，找到疑似网址即返回
    for edit in _descendants(window, "EditControl", depth=6, limit=20):
        value = _value_of(edit)
        if not value:
            continue
        low = value.lower()
        if "chaoxing.com" in low or "zhihuishu.com" in low or "workId=" in value or value.startswith("http"):
            return value if value.startswith("http") else "https://" + value
    return ""


def _zhihuishu_exam_id(url: str) -> str:
    if not url:
        return ""
    matched = re.search(r"/dohomework/([^/]+)/", url)
    if matched:
        return matched.group(1)
    parts = [part for part in urlparse(url).fragment.split("/") if part]
    for index, part in enumerate(parts):
        if part == "dohomework" and index + 1 < len(parts):
            return parts[index + 1]
    return ""


def _zhihuishu_summary(window) -> tuple[str, str]:
    """轻量读取智慧树作业名/题量，不做整树兜底扫描。"""
    import uiautomation as auto

    auto.SetGlobalSearchTimeout(0.4)
    doc = window.DocumentControl(searchDepth=20, Name="作业考试_在线学堂_智慧树")
    if not doc.Exists(maxSearchSeconds=0.6):
        doc = window.DocumentControl(searchDepth=12, SubName="智慧树")
        if not doc.Exists(maxSearchSeconds=0.4):
            return "", ""
    pattern = doc.GetPattern(auto.PatternId.TextPattern)
    if not pattern:
        return "", ""
    text = pattern.DocumentRange.GetText(1200) or ""
    name = ""
    matched = re.search(r"(?m)^([^\n]{2,40}单元测试|[^\n]{2,40}测验)\s*$", text)
    if matched:
        name = matched.group(1).strip()
    if not name:
        course = re.search(r"名称([^\n]+)", text)
        if course:
            name = course.group(1).strip()
    count = ""
    found = re.search(r"题目数\s*(\d+)", text) or re.search(r"总题数:\s*(\d+)", text)
    if found:
        count = found.group(1)
    return name, count


def _page_summary(window) -> tuple[str, str]:
    import uiautomation as auto

    auto.SetGlobalSearchTimeout(0.4)
    document = window.DocumentControl(searchDepth=20, Name="作业作答")
    if not document.Exists(maxSearchSeconds=0.6):
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


def _descendants(control, type_name: str, depth: int, limit: int | None = None):
    found = []

    def walk(node, level: int) -> None:
        if limit is not None and len(found) >= limit:
            return
        if level > depth:
            return
        if level > 0 and node.ControlTypeName == type_name:
            found.append(node)
            if limit is not None and len(found) >= limit:
                return
        for child in node.GetChildren():
            walk(child, level + 1)
            if limit is not None and len(found) >= limit:
                return

    walk(control, 0)
    return found


def connect_window(title: str | None = None, handle: int | None = None):
    import uiautomation as auto

    auto.SetGlobalSearchTimeout(1)
    if handle:
        window = window_from_handle(handle)
        if window is None:
            raise SystemExit("选中的窗口已经关闭，请重新刷新")
        return window
    if title:
        window = auto.WindowControl(searchDepth=1, Name=title)
        if not window.Exists(maxSearchSeconds=1):
            raise SystemExit(f"没有找到窗口：{title}")
        return window
    found = list_answer_windows()
    if len(found) == 1:
        return window_from_handle(found[0]["handle"]) or auto.WindowControl(searchDepth=1, Name=found[0]["title"])
    if not found:
        raise SystemExit("没有找到 Chrome 或 Edge 中的超星「作业作答」或智慧树「作业考试」窗口")
    names = "；".join(item["title"] for item in found)
    raise SystemExit(f"找到多个作业窗口，请在界面中指定一个：{names}")
