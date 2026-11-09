"""智慧树「作业考试」网页测验：读题、作答、写入。

页面一次只显示一题，需通过右侧题号或「下一题」切换。
选择题选项顺序会随机打乱，写入时按选项正文匹配，不按字母。
不会点击「提交作业」或「暂存作业」。
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

from auto_answer.browser import connect_window, window_from_handle
from auto_answer.config import app_root

ROOT = app_root()
RUNTIME = ROOT / "runtime"
PAGE_TEXT = ROOT / "_zhihuishu_page_text.txt"
ANSWERS_PATH = RUNTIME / "zhihuishu_answers.json"

DOCUMENT_NAME = "作业考试_在线学堂_智慧树"
FORBIDDEN_NAMES = ("提交作业", "暂存作业", "提交", "交卷")

HEAD_RE = re.compile(
    r"(?ms)^(\d+)\.\s*\n【((?:单选|多选|判断|填空|简答)题)】\s*\(([\d.]+)\s*分\)\s*\n(.*?)(?=^\d+\.\s*\n【|\n上一题|\n下一题|\n保存\b|\Z)"
)
OPTION_RE = re.compile(r"(?ms)^([A-H])\.\s*\n(.*?)(?=\n[A-H]\.\s*\n|\Z)")
BLOCK_STOP = r"(?=^\d+\.\s*\n【|\n上一题|\n下一题|\n保存\b|\Z)"
TYPE_MAP = {
    "单选题": "single",
    "多选题": "multi",
    "判断题": "judge",
    "填空题": "blank",
    "简答题": "essay",
}


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", (text or "").replace("\uFFFC", ""))


def document_control(window):
    import uiautomation as auto

    auto.SetGlobalSearchTimeout(0.5)
    doc = window.DocumentControl(searchDepth=20, Name=DOCUMENT_NAME)
    if doc.Exists(maxSearchSeconds=0.8):
        return doc
    # 不用整树遍历兜底，改为按子串短超时查找
    doc = window.DocumentControl(searchDepth=16, SubName="智慧树")
    if doc.Exists(maxSearchSeconds=0.5):
        return doc
    doc = window.DocumentControl(searchDepth=16, SubName="作业考试")
    if doc.Exists(maxSearchSeconds=0.5):
        return doc
    raise SystemExit("没有在浏览器里找到智慧树作业考试页面")


def page_text(window) -> str:
    import uiautomation as auto

    doc = document_control(window)
    pattern = doc.GetPattern(auto.PatternId.TextPattern)
    if not pattern:
        raise SystemExit("智慧树页面无法读取文本")
    return (pattern.DocumentRange.GetText(-1) or "").replace("\r\n", "\n").replace("\r", "\n")


def page_meta(window) -> dict:
    """检测窗口用的轻量摘要，只读前一段文本。"""
    import uiautomation as auto

    doc = document_control(window)
    pattern = doc.GetPattern(auto.PatternId.TextPattern)
    if not pattern:
        return {"name": "", "count": "", "text": ""}
    text = (pattern.DocumentRange.GetText(1500) or "").replace("\r\n", "\n").replace("\r", "\n")
    name = ""
    matched = re.search(r"(?m)^([^\n]{2,40}单元测试|[^\n]{2,40}测验|[^\n]{2,40}考试)\s*$", text)
    if matched:
        name = matched.group(1).strip()
    if not name:
        course = re.search(r"名称([^\n]+)", text)
        chapter = re.search(r"对应章节([^\n]+)", text)
        if course and chapter:
            name = f"{course.group(1).strip()} · {chapter.group(1).strip()}"
        elif course:
            name = course.group(1).strip()
    count = ""
    found = re.search(r"题目数\s*(\d+)", text) or re.search(r"总题数:\s*(\d+)", text)
    if found:
        count = found.group(1)
    return {"name": name, "count": count, "text": text}


def parse_questions(text: str) -> tuple[list[dict], list[tuple[int, str]]]:
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    # 去掉页脚导航干扰
    text = re.split(r"\n上一题\n|\n下一题\n|\n保存\n", text, maxsplit=1)[0]
    heads = list(HEAD_RE.finditer(text))
    if not heads:
        raise ValueError("没有解析到智慧树题目")
    questions: list[dict] = []
    skipped: list[tuple[int, str]] = []
    for head in heads:
        number = int(head.group(1))
        raw_type = head.group(2).strip()
        body = head.group(4).strip()
        kind = TYPE_MAP.get(raw_type)
        if kind is None:
            skipped.append((number, raw_type))
            continue
        options: dict[str, str] = {}
        blanks = 1
        if kind in {"blank", "essay"}:
            stem = re.sub(r"\uFFFC", "", body).strip()
        else:
            option_match = re.search(r"(?m)^[A-H]\.\s*$", body)
            if not option_match:
                # 选项可能紧挨着字母行
                option_match = re.search(r"(?m)^[A-H]\.\s*\n", body)
            if not option_match:
                raise ValueError(f"第 {number} 题没有解析到选项")
            stem = re.sub(r"\uFFFC", "", body[: option_match.start()]).strip()
            for opt in OPTION_RE.finditer(body[option_match.start() :]):
                options[opt.group(1)] = re.sub(r"\uFFFC", "", opt.group(2)).strip()
            if not options:
                raise ValueError(f"第 {number} 题选项为空")
            if kind == "blank":
                blanks = max(len(re.findall(r"[（(]_{2,}[）)]|_{3,}", stem)), 1)
        questions.append(
            {
                "no": number,
                "type": kind,
                "raw_type": raw_type,
                "stem": stem,
                "options": options,
                "blanks": blanks,
            }
        )
    if not questions:
        raise ValueError("没有可作答的智慧树题目")
    questions.sort(key=lambda item: item["no"])
    return questions, skipped


def collect_questions(window, log=print) -> tuple[list[dict], list[tuple[int, str]], str]:
    """逐题切换，汇总全部题目文本并解析。"""
    meta = page_meta(window)
    total = int(meta["count"] or "0")
    if total <= 0:
        total = len(_nav_items(window))
    if total <= 0:
        raise SystemExit("没有读到智慧树题量")
    chunks: list[str] = []
    for number in range(1, total + 1):
        if not goto_question(window, number):
            raise SystemExit(f"无法跳到第 {number} 题")
        piece = _wait_question_block(window, number)
        if not piece:
            raise SystemExit(f"第 {number} 题文本为空")
        chunks.append(piece)
        log(f"已读取第 {number}/{total} 题")
    combined = "\n\n".join(chunks) + "\n"
    PAGE_TEXT.write_text(combined, encoding="utf-8")
    questions, skipped = parse_questions(combined)
    return questions, skipped, combined


def _extract_current_block(text: str, number: int) -> str:
    matched = re.search(
        rf"(?ms)^{number}\.\s*\n【(?:单选|多选|判断|填空|简答)题】\s*\([\d.]+\s*分\)\s*\n.*?{BLOCK_STOP}",
        text,
    )
    if matched:
        return matched.group(0).strip()
    # 长题干时题号行可能滚出 TextPattern 范围；仅在页面确实没有其它题号时启用兜底
    other = re.search(r"(?m)^(\d+)\.\s*$", text)
    if other and int(other.group(1)) != int(number):
        return ""
    matched = re.search(
        rf"(?ms)(【(?:单选|多选|判断|填空|简答)题】\s*\([\d.]+\s*分\)\s*\n.*?{BLOCK_STOP})",
        text,
    )
    if matched:
        return f"{number}.\n{matched.group(1).strip()}"
    return ""


def _wait_question_block(window, number: int, tries: int = 8) -> str:
    """等待 SPA 切到目标题后再抽取文本。"""
    for attempt in range(tries):
        time.sleep(0.35 if attempt == 0 else 0.45)
        text = page_text(window)
        piece = _extract_current_block(text, number)
        if piece:
            return piece
        if attempt in {2, 4, 6}:
            _scroll_question_into_view(window)
        elif attempt == 3:
            goto_question(window, number)
    return ""


def _scroll_question_into_view(window) -> None:
    """把题干滚回可视区，避免长题只读到后半截。"""
    import uiautomation as auto

    doc = document_control(window)
    rect = doc.BoundingRectangle
    if rect.isempty():
        return
    x = rect.left + min(max(rect.width() // 3, 120), 480)
    y = rect.top + min(max(rect.height() // 3, 160), 320)
    auto.Click(x, y, waitTime=0.08)
    auto.WheelUp(10, waitTime=0.06)
    time.sleep(0.15)


def _walk(control, max_depth: int = 12):
    found = []

    def walk(node, depth: int) -> None:
        if depth > max_depth:
            return
        found.append(node)
        for child in node.GetChildren():
            walk(child, depth + 1)

    walk(control, 0)
    return found


def _nav_items(window) -> dict[int, object]:
    doc = document_control(window)
    items: dict[int, object] = {}
    for control in _walk(doc, max_depth=14):
        if control.ControlTypeName != "ListItemControl":
            continue
        name = (control.Name or "").strip()
        if not name.isdigit():
            continue
        rect = control.BoundingRectangle
        if rect.isempty() or rect.width() < 8:
            continue
        # 右侧答题卡题号；排除左侧偶尔同名项
        number = int(name)
        current = items.get(number)
        if current is None or control.BoundingRectangle.left > current.BoundingRectangle.left:
            items[number] = control
    return items


def goto_question(window, number: int) -> bool:
    import uiautomation as auto

    items = _nav_items(window)
    target = items.get(int(number))
    if target is None:
        return False
    scroll_into_view(target)
    rect = target.BoundingRectangle
    if rect.isempty():
        return False
    auto.Click(rect.xcenter(), rect.ycenter(), waitTime=0.2)
    return True


def scroll_into_view(control) -> None:
    import uiautomation as auto

    pattern = control.GetPattern(auto.PatternId.ScrollItemPattern)
    if pattern:
        pattern.ScrollIntoView(waitTime=0.02)


def option_rows(window) -> list[dict]:
    """当前页选项行：字母、正文、可点击的行控件。

    单选常见：行 Group 直接含子节点 TextControl「A.」
    多选常见：字母藏在嵌套 Group（勾选图标 + A.）里，正文在另一个子 Group
    """
    doc = document_control(window)
    rows: list[dict] = []

    def dig_letter(node) -> str:
        name = (node.Name or "").strip()
        if re.fullmatch(r"[A-H]\.", name):
            return name[0]
        for child in node.GetChildren():
            got = dig_letter(child)
            if got:
                return got
        return ""

    def dig_text(node) -> str:
        name = (node.Name or "").strip()
        if name and not re.fullmatch(r"[A-H]\.", name):
            return name
        for child in node.GetChildren():
            got = dig_text(child)
            if got:
                return got
        return ""

    for control in _walk(doc, max_depth=14):
        if control.ControlTypeName != "GroupControl":
            continue
        rect = control.BoundingRectangle
        if rect.isempty() or rect.width() < 200 or rect.height() < 28 or rect.height() > 90:
            continue
        children = control.GetChildren()
        if not children:
            continue
        letter = ""
        body = ""
        for child in children:
            name = (child.Name or "").strip()
            if re.fullmatch(r"[A-H]\.", name):
                letter = name[0]
                continue
            found_letter = dig_letter(child)
            found_text = dig_text(child)
            if found_letter and not letter:
                letter = found_letter
            if found_text and (not body or len(found_text) > len(body)):
                body = found_text
        if not letter:
            continue
        # 正文不能只是字母本身
        if _compact(body) in {letter, letter + ".", ""}:
            body = ""
        rows.append({"letter": letter, "text": body, "control": control})
    # 同一字母可能被内外层 Group 重复收录，保留更宽的那一层
    best: dict[str, dict] = {}
    for row in rows:
        prev = best.get(row["letter"])
        width = row["control"].BoundingRectangle.width()
        if prev is None or width > prev["control"].BoundingRectangle.width():
            best[row["letter"]] = row
        elif width == prev["control"].BoundingRectangle.width() and row["text"] and not prev["text"]:
            best[row["letter"]] = row
    return sorted(best.values(), key=lambda item: item["control"].BoundingRectangle.top)


def _click_option_row(row: dict) -> None:
    """多屏下优先 Win32 绝对坐标点击左侧勾选区。"""
    import ctypes
    from ctypes import wintypes

    control = row["control"]
    try:
        scroll_into_view(control)
    except Exception:
        pass
    rect = control.BoundingRectangle
    if rect.isempty():
        return
    x = int(rect.left + min(28, max(rect.width() // 14, 12)))
    y = int(rect.ycenter())
    user32 = ctypes.windll.user32
    vx, vy = user32.GetSystemMetrics(76), user32.GetSystemMetrics(77)
    vw, vh = user32.GetSystemMetrics(78), user32.GetSystemMetrics(79)
    if vw <= 0 or vh <= 0:
        return
    abs_x = int((x - vx) * 65535 / vw)
    abs_y = int((y - vy) * 65535 / vh)

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [
            ("dx", wintypes.LONG),
            ("dy", wintypes.LONG),
            ("mouseData", wintypes.DWORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
        ]

    class INPUT(ctypes.Structure):
        _fields_ = [("type", wintypes.DWORD), ("mi", MOUSEINPUT)]

    extra = ctypes.c_ulong(0)
    move = 0x0001 | 0x8000 | 0x4000  # MOVE|ABSOLUTE|VIRTUALDESK
    down = 0x0002 | 0x8000 | 0x4000
    up = 0x0004 | 0x8000 | 0x4000
    for flags in (move, down, up):
        inp = INPUT(type=0, mi=MOUSEINPUT(abs_x, abs_y, 0, flags, 0, ctypes.pointer(extra)))
        user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(inp))
    time.sleep(0.15)


def _blue_score_from_image(image) -> int:
    """统计图中蓝像素数量。"""
    image = image.convert("RGB")
    width, height = image.size
    if width < 4 or height < 4:
        return -1
    pixels = list(image.getdata())
    if not pixels:
        return -1
    return sum(
        1
        for pixel in pixels
        if pixel[2] > pixel[0] + 20 and pixel[2] > 140 and pixel[0] < 210
    )


def _find_option_flag(control):
    """多选行内的勾选图标 ImageControl（class 常为 flagChecked，与是否勾选无关）。"""
    if control is None:
        return None
    try:
        children = control.GetChildren()
    except Exception:
        return None
    for child in children:
        try:
            if child.ControlTypeName == "ImageControl" and "flag" in (child.ClassName or "").lower():
                return child
        except Exception:
            pass
        try:
            for grand in child.GetChildren():
                if grand.ControlTypeName == "ImageControl" and "flag" in (grand.ClassName or "").lower():
                    return grand
        except Exception:
            pass
    return None


def _capture_window_image(window):
    """截顶层浏览器窗口。CaptureToImage 返回物理像素，UIA 矩形多为逻辑像素，需按尺寸比缩放。"""
    try:
        from PIL import Image
    except Exception:
        return None, None
    RUNTIME.mkdir(exist_ok=True)
    path = RUNTIME / "_zhihuishu_window.png"
    try:
        top = window
        try:
            top = window.GetTopLevelControl() or window
        except Exception:
            top = window
        if not top.CaptureToImage(str(path)):
            return None, None
        image = Image.open(path).convert("RGB")
    except Exception:
        return None, None
    rect = top.BoundingRectangle
    if rect.isempty() or image.size[0] < 50 or image.size[1] < 50:
        return None, None
    return image, rect


def _crop_scaled_box(image, win_rect, left: int, top: int, right: int, bottom: int, pad: int = 2):
    """按窗口物理/逻辑比例裁剪逻辑坐标框。"""
    scale_x = image.size[0] / max(win_rect.width(), 1)
    scale_y = image.size[1] / max(win_rect.height(), 1)
    x0 = int((left - win_rect.left) * scale_x) - pad
    y0 = int((top - win_rect.top) * scale_y) - pad
    x1 = int((right - win_rect.left) * scale_x) + pad
    y1 = int((bottom - win_rect.top) * scale_y) + pad
    x0 = max(0, min(x0, image.size[0] - 1))
    y0 = max(0, min(y0, image.size[1] - 1))
    x1 = max(x0 + 1, min(x1, image.size[0]))
    y1 = max(y0 + 1, min(y1, image.size[1]))
    return image.crop((x0, y0, x1, y1))


def snapshot_checked_map(rows: list[dict], window=None) -> dict[str, bool | None]:
    """截窗口后只裁勾选图标判断状态，避免行内蓝色题干文字干扰。"""
    result = {str(row.get("letter") or ""): None for row in rows}
    if window is None or not rows:
        return result
    image, win_rect = _capture_window_image(window)
    if image is None or win_rect is None:
        return result
    scores: dict[str, int] = {}
    for row in rows:
        letter = str(row.get("letter") or "")
        control = row.get("control")
        if not letter or control is None:
            continue
        flag = _find_option_flag(control)
        box = None
        if flag is not None:
            rect = flag.BoundingRectangle
            if not rect.isempty():
                box = (rect.left, rect.top, rect.right, rect.bottom)
        if box is None:
            # 退回：只取行首勾选方块，避免整行蓝字抬高分数
            row_rect = control.BoundingRectangle
            if row_rect.isempty():
                continue
            side = 18
            top = row_rect.top + max(0, (row_rect.height() - side) // 2)
            box = (row_rect.left + 8, top, row_rect.left + 8 + side, top + side)
        try:
            crop = _crop_scaled_box(image, win_rect, *box, pad=2)
            score = _blue_score_from_image(crop)
        except Exception:
            continue
        if score >= 0:
            scores[letter] = score
            try:
                crop.save(RUNTIME / f"_zhihuishu_flag_{letter}.png")
            except Exception:
                pass
    if not scores:
        return result
    # 勾选图标约 17–30px：已选蓝量通常 >200，未选接近 0
    peak = max(scores.values())
    for letter, score in scores.items():
        if peak >= 40:
            if score >= max(30, int(peak * 0.35)):
                result[letter] = True
            elif score <= max(12, int(peak * 0.12)):
                result[letter] = False
            else:
                result[letter] = None
        else:
            result[letter] = False
    return result


def option_is_checked(row: dict, window=None) -> bool | None:
    """单选项判断；优先走整页快照。"""
    if window is not None:
        return snapshot_checked_map([row], window=window).get(row.get("letter") or "")
    return None


def _resolve_multi_targets(rows: list[dict], answer: str, options: dict) -> list[dict]:
    wanted_letters = [ch for ch in str(answer).upper() if ch in "ABCDEFGH"]
    targets = []
    for letter in wanted_letters:
        text = _compact(options.get(letter) or "")
        hit = None
        if text:
            hit = next((row for row in rows if _compact(row["text"]) == text), None)
            if hit is None:
                hit = next(
                    (
                        row
                        for row in rows
                        if text in _compact(row["text"]) or _compact(row["text"]) in text
                    ),
                    None,
                )
        if hit is None:
            hit = next((row for row in rows if row["letter"] == letter), None)
        if hit is not None and hit not in targets:
            targets.append(hit)
    return targets


def choose_option(window, kind: str, answer: str, options: dict | None = None, log=None) -> str:
    options = options or {}
    _scroll_question_into_view(window)
    rows = []
    for _ in range(8):
        rows = option_rows(window)
        if rows:
            break
        _scroll_question_into_view(window)
        time.sleep(0.35)
    if not rows:
        return "no-controls"
    if kind == "multi":
        wanted_letters = [ch for ch in str(answer).upper() if ch in "ABCDEFGH"]
        targets = _resolve_multi_targets(rows, answer, options)
        if len(targets) < len(wanted_letters):
            return "missing-" + "".join(wanted_letters)
        wanted = {row["letter"] for row in targets}
        # 等页面绘制稳定后再截；只对高置信度错误态点击；需要勾选但未知时按未选处理
        time.sleep(0.25)
        all_actions: list[str] = []
        for pass_no in range(2):
            rows = option_rows(window) or rows
            checked_map = snapshot_checked_map(rows, window=window)
            if log:
                detail = ", ".join(
                    f"{letter}:{checked_map.get(letter)}" for letter in sorted(checked_map)
                )
                log(f"多选快照#{pass_no + 1} {detail}")
            known = [state for state in checked_map.values() if state is not None]
            if not known:
                if pass_no == 0:
                    time.sleep(0.35)
                    continue
                if log:
                    log("多选勾选态识别失败，跳过点击以免误取消已选项")
                return "skip-unknown-state"
            actions = []
            changed = False
            for row in rows:
                letter = row["letter"]
                should = letter in wanted
                state = checked_map.get(letter)
                if state is None:
                    # 需要选中却未知：按未选补点；不需要的未知：绝不点，避免误取消
                    if should:
                        _click_option_row(row)
                        time.sleep(0.22)
                        actions.append(f"{letter}=on?")
                        changed = True
                    else:
                        actions.append(f"{letter}=skip")
                    continue
                if should and state is False:
                    _click_option_row(row)
                    time.sleep(0.22)
                    actions.append(f"{letter}=on")
                    changed = True
                elif (not should) and state is True:
                    _click_option_row(row)
                    time.sleep(0.22)
                    actions.append(f"{letter}=off")
                    changed = True
                else:
                    actions.append(f"{letter}=keep")
            all_actions.extend(actions)
            if log:
                log(f"多选动作#{pass_no + 1} " + ", ".join(actions))
            if not changed:
                break
            time.sleep(0.3)
        return "checked"
    # 单选 / 判断：按正文匹配
    letter = answer[:1].upper() if answer else ""
    wanted = _compact(options.get(letter) or "")
    target = None
    if wanted:
        target = next((row for row in rows if _compact(row["text"]) == wanted), None)
        if target is None:
            target = next(
                (
                    row
                    for row in rows
                    if wanted in _compact(row["text"]) or _compact(row["text"]) in wanted
                ),
                None,
            )
    if target is None and letter:
        target = next((row for row in rows if row["letter"] == letter), None)
    if target is None and kind == "judge":
        truth = "对" if answer in {"对", "正确", "true", "True", "A"} else "错" if answer in {"错", "错误", "false", "False", "B"} else answer
        target = next((row for row in rows if truth and truth in (row["text"] or "")), None)
    if target is None:
        return "missing"
    _click_option_row(target)
    page_letter = target["letter"]
    return "selected" if page_letter == letter else f"selected@{page_letter}"


def _click_named_button(window, names: tuple[str, ...]) -> str:
    """点击底部导航按钮。只允许下一题/保存，绝不点提交或暂存。"""
    import ctypes
    from ctypes import wintypes

    doc = document_control(window)
    for name in names:
        if any(word in name for word in FORBIDDEN_NAMES):
            continue
        button = doc.ButtonControl(searchDepth=12, Name=name)
        if not button.Exists(maxSearchSeconds=0.5):
            continue
        rect = button.BoundingRectangle
        if rect.isempty():
            continue
        user32 = ctypes.windll.user32
        user32.SetCursorPos(int(rect.xcenter()), int(rect.ycenter()))
        time.sleep(0.04)

        class MOUSEINPUT(ctypes.Structure):
            _fields_ = [
                ("dx", wintypes.LONG),
                ("dy", wintypes.LONG),
                ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
            ]

        class INPUT(ctypes.Structure):
            _fields_ = [("type", wintypes.DWORD), ("mi", MOUSEINPUT)]

        extra = ctypes.c_ulong(0)
        for flag in (0x0002, 0x0004):
            inp = INPUT(type=0, mi=MOUSEINPUT(0, 0, 0, flag, 0, ctypes.pointer(extra)))
            user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(inp))
        time.sleep(0.2)
        return name
    return ""


def click_next(window) -> bool:
    """点「下一题」保存当前答案。"""
    return bool(_click_named_button(window, ("下一题",)))


def click_save(window) -> bool:
    """末题点「保存」。不会点暂存作业/提交作业。"""
    return bool(_click_named_button(window, ("保存",)))


def apply_answers(
    records: list[dict],
    only: set[int] | None = None,
    window_title: str | None = None,
    window_handle: int | None = None,
    log=print,
) -> None:
    import ctypes

    ctypes.windll.user32.SetProcessDPIAware()
    window = connect_window(window_title, handle=window_handle)
    try:
        ctypes.windll.user32.SetForegroundWindow(int(window.NativeWindowHandle))
    except Exception:
        pass
    window.SetActive(waitTime=0.25)
    selected = [item for item in records if not only or item["no"] in only]
    if not selected:
        raise SystemExit("没有要写入的题目")
    log_path = RUNTIME / "zhihuishu_applied.jsonl"
    RUNTIME.mkdir(exist_ok=True)
    for index, record in enumerate(selected):
        number = record["no"]
        expected = record["answer"]
        status = "pending"
        try:
            if not goto_question(window, number):
                raise RuntimeError(f"页面上没有第 {number} 题")
            if not _wait_question_block(window, number, tries=6):
                raise RuntimeError(f"第 {number} 题页面未加载完成")
            if record["type"] in {"blank", "essay"}:
                status = "unsupported-blank"
                log(f"第 {number} 题暂不支持智慧树填空/简答写入")
            else:
                status = choose_option(
                    window,
                    record["type"],
                    expected,
                    record.get("options") or {},
                    log=log,
                )
                # 非末题点下一题；末题点「保存」
                if index < len(selected) - 1:
                    if not click_next(window):
                        nxt = selected[index + 1]["no"]
                        goto_question(window, nxt)
                    time.sleep(0.3)
                else:
                    click_save(window)
                    time.sleep(0.25)
        except Exception as exc:  # noqa: BLE001
            # 常见：(-2147220991, 事件无法调用任何订户) —— UIA 事件订阅失败，不中断整页
            status = f"com-error"
            log(f"第 {number} 题写入异常 -> 期望 {expected} (com-error: {exc})")
        else:
            if status.startswith("partial") or status.startswith("miss") or status in {"missing", "no-controls"}:
                log(f"第 {number} 题写入异常 -> 期望 {expected} ({status})")
            else:
                log(f"第 {number} 题 -> {expected} ({status})")
        line = {"no": number, "answer": expected, "status": status}
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(line, ensure_ascii=False) + "\n")
        time.sleep(0.1)


def verify_answers(
    records: list[dict],
    window_title: str | None = None,
    window_handle: int | None = None,
    only: set[int] | None = None,
) -> list[dict]:
    window = connect_window(window_title, handle=window_handle)
    mismatches = []
    for record in records:
        if only and record["no"] not in only:
            continue
        if record["type"] in {"blank", "essay"}:
            continue
        if not goto_question(window, record["no"]):
            mismatches.append({"no": record["no"], "expected": record["answer"], "actual": "页面上没有这题"})
            continue
        time.sleep(0.25)
        options = record.get("options") or {}
        expected = record["answer"]
        wanted = _compact(options.get(expected[:1], "") if record["type"] == "single" else "")
        rows = option_rows(window)
        # 智慧树选中态在 UIA 里不明显，这里只核对选项正文是否仍能对应到当前字母
        if record["type"] == "single" and wanted:
            hit = next((row for row in rows if _compact(row["text"]) == wanted or wanted in _compact(row["text"])), None)
            if hit is None:
                mismatches.append({"no": record["no"], "expected": expected, "actual": "未找到对应选项正文"})
        elif record["type"] == "multi":
            for letter in expected:
                text = _compact(options.get(letter) or "")
                if text and not any(text == _compact(row["text"]) or text in _compact(row["text"]) for row in rows):
                    mismatches.append({"no": record["no"], "expected": expected, "actual": f"缺少选项 {letter}"})
                    break
    return mismatches


def copy_all_question_text(handle: int, log=print) -> str:
    """供 GUI「更新题目」使用：激活窗口并逐题复制。"""
    window = window_from_handle(handle)
    if window is None:
        raise SystemExit("选中的窗口已经关闭，请重新刷新")
    window.SetActive(waitTime=0.25)
    questions, skipped, combined = collect_questions(window, log=log)
    if skipped:
        log("已跳过：" + "、".join(f"{no}:{typ}" for no, typ in skipped))
    log(f"共收集 {len(questions)} 题")
    return combined
