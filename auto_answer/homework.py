"""读取作业文本，调用 OpenAI 兼容接口作答，并在已打开的浏览器里选择答案。

只做选择、勾选、填空和简答。不会点击提交、交卷或暂时保存。
"""

from __future__ import annotations

import argparse
import base64
import json
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

from auto_answer.browser import connect_window
from auto_answer.config import app_root, resolve_config

ROOT = app_root()
RUNTIME = ROOT / "runtime"
PAGE_TEXT = ROOT / "_page_text.txt"
ANSWERS_PATH = RUNTIME / "answers.json"
IMAGES_PATH = RUNTIME / "images.json"
IMAGE_DIR = RUNTIME / "images"
IMAGE_UPLOAD_ANSWER = "需上传图片"

QUESTION_HEAD_RE = re.compile(
    r"(?m)^(\d+)\.\s*\(([^)\n]*题)(?:\s*[,，]\s*[\d.]+\s*分)?\)\s*(.*)$"
)
SECTION_RE = re.compile(r"(?m)^[一二三四五六七八九十]+\.\s*\S*题（[^）]*）\s*$")
OPTION_RE = re.compile(r"(?m)^([A-H])\n(.*?)(?=\n[A-H]\n|\Z)", re.S)
# 写入后再更新题目时，页面会带上「我的答案」，必须丢掉，否则模型会跟着旧答案走
FILLED_ANSWER_RE = re.compile(
    r"(?ms)^我的答案\s*[:：]?.*?(?=^\d+\.\s*\(|^[一二三四五六七八九十]+\.\s*\S*题|\Z)"
)
NAV_FOOTER_RE = re.compile(r"\n[一二三四五六七八九十]+\.\s*\S*题（[\d.]+分）")
FORBIDDEN_NAMES = ("提交", "交卷", "暂时保存", "保存并提交", "确认提交")

TYPE_MAP = {
    "单选题": "single",
    "多选题": "multi",
    "判断题": "judge",
    "填空题": "blank",
    "简答题": "essay",
}


def load_page_text(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    # 页脚导航是「一. 单选题（60.4分）」，题型标题是「一. 单选题（共14题，60.4分）」
    # 从第一个页脚导航处截断，避免题号列表粘到最后一题
    footer = NAV_FOOTER_RE.search(text)
    if footer:
        text = text[: footer.start()]
    text = FILLED_ANSWER_RE.sub("", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() + "\n"


def _clean_question_text(text: str) -> str:
    text = FILLED_ANSWER_RE.sub("", text or "")
    text = re.sub(r"(?m)^我的答案\s*[:：]?.*$", "", text)
    text = re.sub(r"(?m)^\d{5,}\s*$", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def describe_skipped(skipped: list[tuple[int, str]]) -> str:
    if not skipped:
        return ""
    grouped: dict[str, list[str]] = {}
    for number, raw_type in skipped:
        grouped.setdefault(raw_type, []).append(str(number))
    parts = [f"{name}（第 {'、'.join(numbers)} 题）" for name, numbers in grouped.items()]
    return "已跳过未支持的题型：" + "；".join(parts)


def parse_questions(text: str) -> tuple[list[dict], list[tuple[int, str]]]:
    heads = list(QUESTION_HEAD_RE.finditer(text))
    if not heads:
        raise ValueError("没有解析到题目")
    questions: list[dict] = []
    skipped: list[tuple[int, str]] = []
    for index, head in enumerate(heads):
        number = int(head.group(1))
        raw_type = head.group(2).strip()
        end = heads[index + 1].start() if index + 1 < len(heads) else len(text)
        body = _clean_question_text(f"{head.group(3)}\n{text[head.end() : end]}")
        kind = TYPE_MAP.get(raw_type)
        if kind is None:
            skipped.append((number, raw_type))
            continue
        if kind == "blank":
            labels = re.findall(r"(?m)^第(\d+)空\s*$", body)
            stem = re.sub(r"(?m)^第\d+空\s*$\n?", "", body)
            stem = SECTION_RE.sub("", stem).strip()
            stem = _clean_question_text(stem)
            blanks = len(labels) or max(len(re.findall(r"_{3,}|＿{3,}", stem)), 1)
            options: dict[str, str] = {}
        elif kind == "essay":
            stem = _clean_question_text(SECTION_RE.sub("", body))
            blanks = 1
            options = {}
        else:
            option_match = re.search(r"(?m)^[A-H]\n", body)
            if not option_match:
                raise ValueError(f"第 {number} 题没有解析到选项")
            stem = _clean_question_text(body[: option_match.start()])
            options = {}
            for opt in OPTION_RE.finditer(body[option_match.start() :]):
                letter = opt.group(1)
                content = SECTION_RE.sub("", opt.group(2))
                content = _clean_question_text(content)
                content = re.sub(r"\n{2,}", "\n", content)
                options[letter] = content
            if not options:
                raise ValueError(f"第 {number} 题选项为空")
        questions.append(
            {
                "no": number,
                "type": kind,
                "raw_type": raw_type,
                "stem": stem,
                "options": options,
                "blanks": blanks if kind in {"blank", "essay"} else 1,
            }
        )
    if not questions:
        detail = describe_skipped(skipped) or "没有可作答的题目"
        raise ValueError(detail)
    questions.sort(key=lambda item: item["no"])
    numbers = [item["no"] for item in questions]
    if len(numbers) != len(set(numbers)):
        raise ValueError(f"题号重复: {numbers}")
    return questions, skipped


def openai_config(override: dict | None = None) -> tuple[str, str, str]:
    config = resolve_config(override)
    if not config["api_key"]:
        raise SystemExit("缺少 API Key。请在界面中保存，或设置环境变量 OPENAI_API_KEY")
    return config["api_key"], config["base_url"], config["model"]


def _message_text(message: dict) -> str:
    """从 OpenAI 兼容的 message 里取出文本。模型名不限，只要返回结构兼容即可。"""
    content = message.get("content")
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict):
                parts.append(str(part.get("text") or part.get("content") or ""))
        content = "".join(parts)
    text = str(content or "").strip()
    if text:
        return text
    for key in ("reasoning_content", "reasoning", "output_text"):
        extra = message.get(key)
        if isinstance(extra, str) and extra.strip():
            return extra.strip()
    return ""


def chat_json(api_key: str, base_url: str, model: str, messages: list[dict]) -> dict:
    payload: dict = {
        "model": model,
        "messages": messages,
    }
    if "reasoner" not in model.lower():
        payload["temperature"] = 0
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        raise RuntimeError(f"接口调用失败 {exc.code}: {detail[:500]}") from exc
    try:
        message = data["choices"][0]["message"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(f"接口返回不是 OpenAI 兼容格式: {str(data)[:300]}") from exc
    content = _message_text(message if isinstance(message, dict) else {})
    content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content, flags=re.I)
    start = content.find("{")
    end = content.rfind("}")
    if start == -1 or end == -1:
        raise RuntimeError(f"模型没有返回 JSON: {content[:300]}")
    return json.loads(content[start : end + 1])


def option_truth(name: str) -> str:
    """从选项名称判断它表示「对」还是「错」。A/B 的顺序不固定。"""
    text = name or ""
    if any(word in text for word in ("错误", "不正确", "错", "否", "×", "✗")):
        return "错"
    if any(word in text for word in ("正确", "对", "是", "√", "✓")):
        return "对"
    return ""


def desired_truth(answer: str, options: dict | None = None) -> str:
    value = (answer or "").strip()
    direct = option_truth(value)
    if direct and value.upper() not in set("ABCDEFGH"):
        return direct
    if value in {"对", "错", "正确", "错误", "TRUE", "FALSE", "T", "F"}:
        return "错" if value.upper() in {"FALSE", "F"} or "错" in value else "对"
    letter = value[:1].upper()
    if letter in set("ABCDEFGH"):
        return option_truth((options or {}).get(letter, ""))
    return direct


def normalize_answer(question: dict, raw: str | list) -> str | list:
    kind = question["type"]
    if kind in {"blank", "essay"} and str(raw).strip() == IMAGE_UPLOAD_ANSWER:
        return IMAGE_UPLOAD_ANSWER
    if kind == "blank":
        count = int(question.get("blanks") or 1)
        parts = blank_parts(raw, count)
        if not parts or any(len(part) > 200 for part in parts):
            raise ValueError(f"第 {question['no']} 题填空答案异常: {raw!r}")
        return parts[0] if count <= 1 else parts
    if kind == "essay":
        text = str(raw).strip().strip('"').strip("“”")
        text = re.sub(r"^(答|答案)\s*[:：]\s*", "", text).strip()
        if not text or len(text) > 800:
            raise ValueError(f"第 {question['no']} 题简答答案异常: {raw!r}")
        return text
    value = str(raw).strip()
    if kind == "judge":
        truth = desired_truth(value, question["options"])
        if truth:
            return truth
        raise ValueError(f"第 {question['no']} 题判断答案无法识别: {value!r}")
    letters = "".join(ch for ch in value.upper() if ch in question["options"])
    letters = "".join(dict.fromkeys(letters))
    if kind == "single":
        if len(letters) != 1:
            raise ValueError(f"第 {question['no']} 题应为单个选项，得到 {value!r}")
        return letters
    if not letters:
        raise ValueError(f"第 {question['no']} 题多选答案为空: {value!r}")
    return "".join(sorted(letters))


def _text_has_image(question: dict) -> bool:
    chunks = [question.get("stem") or ""]
    chunks.extend((question.get("options") or {}).values())
    return any("\uFFFC" in chunk for chunk in chunks)


def _vision_rejected(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(word in text for word in ("image", "vision", "multimodal", "image_url", "图片"))


def _payload_question(item: dict) -> dict:
    stem = (item.get("stem") or "").replace("\uFFFC", "").strip()
    options = {
        letter: (text or "").replace("\uFFFC", "").strip()
        for letter, text in (item.get("options") or {}).items()
    }
    if item.get("images"):
        stem = f"{stem}\n（本题含图片，见附图）".strip()
    entry = {
        "no": item["no"],
        "type": item["raw_type"],
        "stem": stem,
        "options": options,
    }
    if item["type"] == "blank":
        entry["blanks"] = item.get("blanks") or 1
    return entry


def _image_part(path: str) -> dict:
    encoded = base64.b64encode(Path(path).read_bytes()).decode("ascii")
    return {
        "type": "image_url",
        "image_url": {"url": f"data:image/jpeg;base64,{encoded}"},
    }


def _user_content(batch: list[dict]) -> str | list[dict]:
    payload = [_payload_question(item) for item in batch]
    text = json.dumps({"questions": payload}, ensure_ascii=False)
    images = [(item["no"], path) for item in batch for path in item.get("images") or []]
    if not images:
        return text
    parts: list[dict] = [{"type": "text", "text": text}]
    for number, path in images:
        parts.append({"type": "text", "text": f"附图是第 {number} 题"})
        parts.append(_image_part(path))
    return parts


def solve_questions(
    questions: list[dict],
    config: dict | None = None,
    log=print,
    on_update=None,
    persist: bool = True,
) -> list[dict]:
    api_key, base_url, model = openai_config(config)
    attach_saved_images(questions)
    for question in questions:
        if _text_has_image(question) and not question.get("images"):
            log(f"第 {question['no']} 题含图片，但没有截图。请重新点「更新题目」")
    solved: dict[int, str | list] = {}
    plain = [item for item in questions if not item.get("images")]
    pictured = [item for item in questions if item.get("images")]
    # 每批少一点，减少模型串题、漏题
    _solve_batches(questions, plain, 5, api_key, base_url, model, solved, log, on_update, persist)
    _solve_batches(questions, pictured, 1, api_key, base_url, model, solved, log, on_update, persist)
    return _answer_records(questions, solved)


def answers_equal(kind: str, left, right, options: dict | None = None) -> bool:
    if kind == "judge":
        return desired_truth(str(left or ""), options) == desired_truth(str(right or ""), options)
    if kind == "blank":
        left_parts = left if isinstance(left, list) else [left]
        right_parts = right if isinstance(right, list) else [right]
        if len(left_parts) != len(right_parts):
            return False
        return all(
            re.sub(r"\s+", "", str(a or "")) == re.sub(r"\s+", "", str(b or ""))
            for a, b in zip(left_parts, right_parts)
        )
    if kind == "essay":
        return re.sub(r"\s+", "", str(left or "")) == re.sub(r"\s+", "", str(right or ""))
    return str(left or "").strip().upper() == str(right or "").strip().upper()


def find_answer_diffs(original: list[dict], reviewed: list[dict]) -> list[dict]:
    by_no = {item["no"]: item for item in reviewed}
    diffs = []
    for item in original:
        other = by_no.get(item["no"])
        if other is None:
            continue
        if answers_equal(item["type"], item.get("answer"), other.get("answer"), item.get("options") or {}):
            continue
        diffs.append(
            {
                "no": item["no"],
                "type": item["type"],
                "stem": item.get("stem") or "",
                "options": item.get("options") or {},
                "original": item.get("answer"),
                "reviewed": other.get("answer"),
            }
        )
    return diffs


def review_answers(
    questions: list[dict],
    original: list[dict],
    config: dict | None = None,
    log=print,
    on_progress=None,
) -> tuple[list[dict], list[dict]]:
    """用另一套接口独立作答，不改写 answers.json。返回验证结果和分歧题。"""
    wanted = {item["no"] for item in original if item.get("answer") not in ("", None, [])}
    selected = [item for item in questions if item["no"] in wanted]
    if not selected:
        raise SystemExit("当前没有可验证的答案")

    def progress(records: list[dict]) -> None:
        if on_progress:
            on_progress(records)

    reviewed = solve_questions(selected, config=config, log=log, on_update=progress, persist=False)
    # 补上未验证题，便于整表对照
    by_no = {item["no"]: item for item in reviewed}
    merged = []
    for item in original:
        copy = dict(item)
        if item["no"] in by_no:
            copy["answer"] = by_no[item["no"]].get("answer")
            copy["images"] = by_no[item["no"]].get("images") or item.get("images") or []
        merged.append(copy)
    return merged, find_answer_diffs(original, reviewed)


def regenerate_one(
    question: dict,
    previous,
    config: dict | None = None,
    log=print,
) -> str | list:
    """针对单题重新作答。明确告诉模型旧答案是错的，必须换一个。"""
    api_key, base_url, model = openai_config(config)
    attach_saved_images([question])
    entry = _payload_question(question)
    entry["wrong_answer"] = previous
    system = (
        "你是课程测验阅卷助手。只根据题干、选项和附图独立作答。"
        "用户已经确认 wrong_answer 是错误答案，禁止再返回相同答案。"
        "必须给出另一个更合理的正确答案。"
        "单选题的 value 只能是一个大写字母。"
        "多选题的 value 是所有正确选项字母按字母序连写，例如 AD。"
        "判断题的 value 只能是「对」或「错」。"
        "填空题只有一个空时，value 是字符串；多个空时 value 必须是数组。"
        "简答题的 value 是一段直接回答题干的文字，300字以内。"
        "如果题目要求上传或绘制图片作为答案，value 填「需上传图片」。"
        "只输出一个 JSON 对象，格式为 "
        '{"answers":[{"no":1,"value":"B"}]}，不要输出其他文字。'
    )
    user_payload = {"questions": [entry], "instruction": "wrong_answer 已被确认错误，请给出不同答案"}
    content: str | list[dict]
    if question.get("images"):
        content = [
            {"type": "text", "text": json.dumps(user_payload, ensure_ascii=False)},
            {"type": "text", "text": f"附图是第 {question['no']} 题"},
            *[_image_part(path) for path in question["images"]],
        ]
    else:
        content = json.dumps(user_payload, ensure_ascii=False)
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": content},
    ]
    log(f"正在重新生成第 {question['no']} 题（排除旧答案 {previous}）")
    last_error = None
    for _ in range(3):
        try:
            result = chat_json(api_key, base_url, model, messages)
            raw = next(item["value"] for item in result["answers"] if int(item["no"]) == question["no"])
            answer = normalize_answer(question, raw)
            if answers_equal(question["type"], answer, previous, question.get("options") or {}):
                last_error = RuntimeError(f"模型仍返回旧答案 {answer!r}")
                log(f"第 {question['no']} 题仍得到旧答案，重试")
                time.sleep(0.6)
                continue
            return answer
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            log(f"第 {question['no']} 题重试: {exc}")
            time.sleep(0.8)
    raise SystemExit(f"第 {question['no']} 题重新生成失败: {last_error}")


def _solve_batches(
    questions: list[dict],
    selected: list[dict],
    batch_size: int,
    api_key: str,
    base_url: str,
    model: str,
    solved: dict[int, str | list],
    log,
    on_update,
    persist: bool = True,
) -> None:
    system = (
        "你是课程测验阅卷助手。只根据题干、选项和附图独立作答。"
        "如果题干或选项里出现「我的答案」或已填内容，一律忽略，不要照抄。"
        "先判断题意和每个选项，再给出最终答案，不要猜测。"
        "单选题的 value 只能是一个大写字母。"
        "多选题的 value 是所有正确选项字母按字母序连写，例如 AD。"
        "判断题的 value 只能是「对」或「错」。选项顺序不固定，A 可能是对，也可能是错。"
        "填空题只有一个空时，value 是字符串，只写应填入横线的内容。"
        "填空题有多个空时，value 必须是数组，每个空一项，按空的顺序排列。"
        "不要用逗号、分号、竖线或换行把多个空拼成一个字符串，答案内容里可以包含这些符号。"
        "每个空只写该空的内容，不要解释。"
        "简答题的 value 是一段直接回答题干的文字，300字以内，不要写题号，不要加“答：”。"
        "如果题目要求上传或绘制图片作为答案，value 填「需上传图片」。"
        "选项即使是图片，选择题也只返回字母。"
        "代码题要按题目语言判断能否编译，并在能运行时追踪输出。"
        "只输出一个 JSON 对象，格式为 "
        '{"answers":[{"no":1,"value":"B"}]}，不要输出其他文字。'
    )
    for offset in range(0, len(selected), batch_size):
        batch = selected[offset : offset + batch_size]
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": _user_content(batch)},
        ]
        numbers = [item["no"] for item in batch]
        label = f"第 {numbers[0]} 题" if numbers[0] == numbers[-1] else f"第 {numbers[0]}-{numbers[-1]} 题"
        log(f"正在请求 {model}: {label}")
        last_error = None
        for _ in range(2):
            try:
                result = chat_json(api_key, base_url, model, messages)
                got = {int(item["no"]): item["value"] for item in result["answers"]}
                for question in batch:
                    solved[question["no"]] = normalize_answer(question, got[question["no"]])
                last_error = None
                break
            except Exception as exc:  # noqa: BLE001 - retry the whole batch once
                last_error = exc
                log(f"本批需要重试: {exc}")
                time.sleep(1)
        if last_error:
            if any(item.get("images") for item in batch) and _vision_rejected(last_error):
                log(f"{label}的图片当前模型无法识别，请换用支持看图的模型")
                for question in batch:
                    solved[question["no"]] = ""
            else:
                raise SystemExit(f"{label}作答失败: {last_error}")
        current = _answer_records(questions, solved)
        if persist:
            RUNTIME.mkdir(exist_ok=True)
            ANSWERS_PATH.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
        if on_update:
            on_update(current)


def _answer_records(questions: list[dict], solved: dict[int, str | list]) -> list[dict]:
    return [
        {
            "no": question["no"],
            "type": question["type"],
            "answer": solved.get(question["no"], ""),
            "stem": question["stem"],
            "options": question["options"],
            "images": question.get("images") or [],
        }
        for question in questions
    ]


def scroll_into_view(control) -> None:
    import uiautomation as auto

    pattern = control.GetPattern(auto.PatternId.ScrollItemPattern)
    if pattern:
        pattern.ScrollIntoView(waitTime=0.02)


def option_letter(name: str) -> str:
    name = (name or "").strip()
    if name and name[0] in "ABCDEFGH":
        return name[0]
    return ""


def assert_not_forbidden(name: str) -> None:
    for word in FORBIDDEN_NAMES:
        if word in (name or ""):
            raise SystemExit(f"停止操作：遇到禁止点击的控件「{name}」")


def collect_question_groups(window) -> dict[int, object]:
    import uiautomation as auto

    document = window.DocumentControl(searchDepth=25, Name="作业作答")
    if not document.Exists(maxSearchSeconds=3):
        raise SystemExit("没有在浏览器窗口里找到作业文档")
    found: dict[int, object] = {}

    def walk(control, depth: int) -> None:
        if depth > 6:
            return
        name = control.Name or ""
        matched = re.fullmatch(r"题目 (\d+)\. ", name)
        if matched:
            found[int(matched.group(1))] = control
            return
        for child in control.GetChildren():
            walk(child, depth + 1)

    walk(document, 0)
    return found


def _in_toolbar(control) -> bool:
    current = control
    for _ in range(6):
        if current is None:
            return False
        text = f"{current.ClassName or ''} {current.Name or ''}".lower()
        if "toolbar" in text or "edui-editor-toolbar" in text:
            return True
        current = current.GetParentControl()
    return False


def _clip_box(rect, bounds) -> tuple[int, int, int, int] | None:
    left = max(rect.left, bounds.left + 4)
    top = max(rect.top, bounds.top + 80)
    right = min(rect.right, bounds.right - 4)
    bottom = min(rect.bottom, bounds.bottom - 8)
    if right - left < 24 or bottom - top < 24:
        return None
    return (left, top, right, bottom)


def _question_image_boxes(group, window, question: dict) -> list[tuple[int, int, int, int]]:
    scroll_into_view(group)
    time.sleep(0.12)
    bounds = window.BoundingRectangle
    boxes = []

    def walk(control, depth: int) -> None:
        if depth > 8:
            return
        rect = control.BoundingRectangle
        if (
            control.ControlTypeName == "ImageControl"
            and not _in_toolbar(control)
            and rect.width() >= 48
            and rect.height() >= 48
        ):
            scroll_into_view(control)
            time.sleep(0.08)
            box = _clip_box(control.BoundingRectangle, bounds)
            if box:
                boxes.append(box)
            return
        for child in control.GetChildren():
            walk(child, depth + 1)

    walk(group, 0)
    if boxes:
        return boxes[:4]
    if not _text_has_image(question):
        return []
    box = _clip_box(group.BoundingRectangle, bounds)
    return [box] if box else []


def _write_jpeg(box: tuple[int, int, int, int], dest: Path) -> None:
    from PIL import Image, ImageGrab

    image = ImageGrab.grab(bbox=box, all_screens=True).convert("RGB")
    if image.width > 1280:
        height = max(int(image.height * 1280 / image.width), 1)
        image = image.resize((1280, height), Image.Resampling.LANCZOS)
    if image.height > 1600:
        width = max(int(image.width * 1600 / image.height), 1)
        image = image.resize((width, 1600), Image.Resampling.LANCZOS)
    dest.parent.mkdir(parents=True, exist_ok=True)
    image.save(dest, "JPEG", quality=75)


def save_question_images(handle: int, questions: list[dict]) -> dict[int, list[str]]:
    """截下题干或选项里的图片。编辑器工具栏上的小图标不算。"""
    import ctypes

    from auto_answer.browser import _foreground_handle, _restore_foreground, window_from_handle

    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    if IMAGE_DIR.exists():
        for old in IMAGE_DIR.iterdir():
            if old.is_file():
                old.unlink()
    IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    RUNTIME.mkdir(exist_ok=True)
    IMAGES_PATH.write_text("{}", encoding="utf-8")
    previous = _foreground_handle()
    saved: dict[str, list[str]] = {}
    try:
        window = window_from_handle(handle)
        if window is None:
            raise SystemExit("选中的窗口已经关闭，请重新刷新")
        window.SetActive(waitTime=0.2)
        groups = collect_question_groups(window)
        for question in questions:
            group = groups.get(question["no"])
            if group is None:
                continue
            paths = []
            for index, box in enumerate(_question_image_boxes(group, window, question), start=1):
                name = f"{question['no']}-{index}.jpg"
                _write_jpeg(box, IMAGE_DIR / name)
                paths.append(f"images/{name}")
            if paths:
                saved[str(question["no"])] = paths
    finally:
        _restore_foreground(previous)
    RUNTIME.mkdir(exist_ok=True)
    IMAGES_PATH.write_text(json.dumps(saved, ensure_ascii=False, indent=2), encoding="utf-8")
    return {int(number): paths for number, paths in saved.items()}


def attach_saved_images(questions: list[dict]) -> None:
    if not IMAGES_PATH.exists():
        return
    saved = json.loads(IMAGES_PATH.read_text(encoding="utf-8"))
    for question in questions:
        paths = []
        for relative in saved.get(str(question["no"])) or []:
            file = RUNTIME / relative
            if file.exists():
                paths.append(str(file))
        if paths:
            question["images"] = paths


def choice_controls(group) -> list:
    import uiautomation as auto

    controls = []
    for child in group.GetChildren():
        kind = child.ControlTypeName
        if kind in {"RadioButtonControl", "CheckBoxControl"}:
            if child.ControlTypeName == "RadioButtonControl":
                controls.append(auto.RadioButtonControl(element=child.Element))
            else:
                controls.append(auto.CheckBoxControl(element=child.Element))
    return controls


def blank_parts(answer: str | list | None, count: int) -> list[str] | None:
    """多个空的答案必须已经是列表。字符串保持原样，不按标点拆开。"""
    count = max(int(count or 1), 1)
    if isinstance(answer, list):
        parts = [str(part).strip() for part in answer]
    else:
        if count > 1:
            return None
        text = str(answer or "").strip().strip('"').strip("“”")
        parts = [text]
    if len(parts) != count or any(not part for part in parts):
        return None
    return parts


def blank_editors(group) -> list:
    """按第1空、第2空的顺序返回每个空的编辑区。"""
    import uiautomation as auto

    ordered = []

    def walk(control, depth: int) -> None:
        if depth > 8:
            return
        ordered.append(control)
        for child in control.GetChildren():
            walk(child, depth + 1)

    walk(group, 0)
    editors = []
    expect = 1
    waiting = False
    for control in ordered:
        name = (control.Name or "").strip()
        if re.fullmatch(rf"第{expect}空", name):
            waiting = True
            expect += 1
            continue
        if waiting and control.ControlTypeName == "DocumentControl":
            editors.append(auto.DocumentControl(element=control.Element))
            waiting = False
    if editors:
        return editors
    found = [control for control in ordered if control.ControlTypeName == "DocumentControl"]
    if not found:
        return []
    return [auto.DocumentControl(element=found[-1].Element)]


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
    window.SetActive(waitTime=0.2)
    groups = collect_question_groups(window)
    log(f"页面上找到 {len(groups)} 道题")
    log_path = RUNTIME / "applied.jsonl"
    for record in records:
        number = record["no"]
        if only and number not in only:
            continue
        group = groups.get(number)
        if group is None:
            raise SystemExit(f"页面上没有第 {number} 题")
        scroll_into_view(group)
        time.sleep(0.08)
        expected = record["answer"]
        if record["type"] in {"blank", "essay"}:
            status = fill_blank(
                group,
                expected,
                window,
                multiline=record["type"] == "essay",
            )
        else:
            status = choose_option(group, record["type"], expected, record.get("options") or {})
            # 再读一次页面，防止 UIA 报成功但页面仍是旧选项
            if status.startswith("partial") or status.startswith("miss"):
                time.sleep(0.12)
                status = choose_option(group, record["type"], expected, record.get("options") or {})
        line = {"no": number, "answer": expected, "status": status}
        if status.startswith("partial") or status.startswith("miss") or status in {"missing", "no-controls", "写入未生效"}:
            log(f"第 {number} 题写入异常 -> 期望 {expected} ({status})")
        else:
            log(f"第 {number} 题 -> {expected} ({status})")
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(line, ensure_ascii=False) + "\n")
        time.sleep(0.08)


def control_is_checked(control) -> bool:
    """综合判断是否已选中。

    打回重做页常见：Legacy 默认动作全部仍是「选定」，不能据此判未选；
    此时改看 SelectionItem.IsSelected。若 Legacy 明确是「取消选定」则可信。
    """
    import uiautomation as auto

    legacy = control.GetPattern(auto.PatternId.LegacyIAccessiblePattern)
    action = legacy.DefaultAction if legacy else ""
    if "取消" in action:
        return True
    try:
        if bool(control.GetSelectionItemPattern().IsSelected):
            return True
    except Exception:
        pass
    try:
        if control.GetTogglePattern().ToggleState == auto.ToggleState.On:
            return True
    except Exception:
        pass
    return False


def filled_answer_from_group(group) -> str:
    """读取题目下方「我的答案 / 您的答案」。比控件勾选状态更贴近页面显示。"""
    names: list[str] = []

    def walk(control, depth: int) -> None:
        if depth > 7:
            return
        name = (control.Name or "").strip()
        if name:
            names.append(name)
        for child in control.GetChildren():
            walk(child, depth + 1)

    walk(group, 0)
    blob = " ".join(names)
    matched = re.search(r"(?:我的答案|您的答案)\s*[:：]?\s*([A-H]+|对|错)", blob)
    if not matched:
        return ""
    return matched.group(1).replace(" ", "").upper()


def option_body(name: str) -> str:
    """从控件名里取出选项正文。页面常把「选择」粘在末尾，且打回后字母会乱序。"""
    text = (name or "").strip()
    text = re.sub(r"^[A-H]\s*", "", text)
    text = re.sub(r"(取消选定|选定|选择)$", "", text)
    return _compact_text(text)


def _texts_match(left: str, right: str) -> bool:
    a = _compact_text(left)
    b = _compact_text(right)
    if not a or not b:
        return False
    if a == b:
        return True
    # 只允许较长包含，避免「左」之类短串误命中
    shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
    return len(shorter) >= 8 and shorter in longer


def _control_for_text(controls, wanted_text: str):
    wanted = _compact_text(wanted_text)
    if not wanted:
        return None
    scored = []
    for control in controls:
        body = option_body(control.Name)
        if not body:
            continue
        if body == wanted:
            return control
        if _texts_match(body, wanted):
            scored.append((min(len(body), len(wanted)), control))
    if not scored:
        return None
    scored.sort(key=lambda item: item[0], reverse=True)
    return scored[0][1]


def _resolve_choice_targets(controls, kind: str, answer: str, options: dict | None) -> list:
    """按选项文字定位控件。打回重做后 A/B/C 字母会换位，不能只认字母。"""
    options = options or {}
    if kind == "judge":
        truth = desired_truth(answer, options)
        if truth:
            matched = [control for control in controls if option_truth(control.Name) == truth]
            if matched:
                return matched
        letter = answer[:1].upper() if answer else ""
        if letter in options:
            target = _control_for_text(controls, options[letter])
            if target is not None:
                return [target]
        return [control for control in controls if option_letter(control.Name) == letter]
    letters = list(answer) if kind == "multi" else [answer]
    targets = []
    for letter in letters:
        text = options.get(letter) or ""
        target = _control_for_text(controls, text) if text else None
        if target is None:
            target = next((control for control in controls if option_letter(control.Name) == letter), None)
        if target is not None and target not in targets:
            targets.append(target)
    return targets


def _selected_controls(controls) -> list:
    return [control for control in controls if control_is_checked(control)]


def _selected_letters(controls) -> list[str]:
    return [letter for control in _selected_controls(controls) if (letter := option_letter(control.Name))]


def _selected_truths(controls) -> list[str]:
    return [truth for control in _selected_controls(controls) if (truth := option_truth(control.Name))]


def _activate_choice(control) -> None:
    """打回重做页优先鼠标点击，再退回默认动作。"""
    import uiautomation as auto

    scroll_into_view(control)
    time.sleep(0.06)
    rect = control.BoundingRectangle
    if not rect.isempty():
        # 点偏左的圆点，避免点到整行文字区失效
        x = rect.left + min(28, max(rect.width() // 10, 12))
        y = rect.ycenter()
        auto.Click(x, y, waitTime=0.14)
        time.sleep(0.08)
        if control_is_checked(control):
            return
    legacy = control.GetPattern(auto.PatternId.LegacyIAccessiblePattern)
    if legacy is not None:
        action = legacy.DefaultAction or ""
        if "取消" not in action:
            legacy.DoDefaultAction(waitTime=0.14)
            time.sleep(0.08)
            if control_is_checked(control):
                return
    try:
        pattern = control.GetSelectionItemPattern()
        if pattern is not None:
            pattern.Select(waitTime=0.1)
    except Exception:
        pass


def _single_choice_ok(group, target, wanted_text: str) -> tuple[bool, str]:
    """确认单选是否落到目标正文。优先「我的答案」，再看勾选控件。"""
    controls = choice_controls(group)
    page_letter = filled_answer_from_group(group)
    target_letter = option_letter(target.Name) or ""
    if page_letter:
        if target_letter and page_letter == target_letter:
            return True, page_letter
        # 页面字母对上某控件后，再核对正文
        matched = next((control for control in controls if option_letter(control.Name) == page_letter), None)
        if matched is not None and wanted_text and _texts_match(option_body(matched.Name), wanted_text):
            return True, page_letter
    selected = _selected_controls(controls)
    if len(selected) == 1:
        pick = selected[0]
        pick_letter = option_letter(pick.Name) or "?"
        if pick is target or (wanted_text and _texts_match(option_body(pick.Name), wanted_text)):
            return True, pick_letter
    return False, page_letter or "".join(_selected_letters(controls))


def choose_option(group, kind: str, answer: str, options: dict | None = None) -> str:
    options = options or {}
    controls = choice_controls(group)
    if not controls:
        return "no-controls"
    targets = _resolve_choice_targets(controls, kind, answer, options)
    if not targets:
        return "missing"
    if kind == "judge":
        truth = desired_truth(answer, options)
        target = targets[0]
        for _ in range(4):
            if truth and _selected_truths(choice_controls(group)) == [truth]:
                return "selected"
            filled = filled_answer_from_group(group)
            if truth and filled in {truth, "对" if truth == "对" else "错"}:
                return "selected"
            if control_is_checked(target) and len(_selected_controls(choice_controls(group))) == 1:
                return "selected"
            _activate_choice(target)
            time.sleep(0.18)
        actual = "".join(_selected_truths(choice_controls(group)) or _selected_letters(choice_controls(group)))
        return "selected" if (truth and actual == truth) else f"partial-{actual or filled_answer_from_group(group) or '?'}"
    if kind == "multi":
        wanted_ids = {id(control) for control in targets}
        missed = []
        for control in controls:
            should = id(control) in wanted_ids
            if control_is_checked(control) == should:
                continue
            _activate_choice(control)
            time.sleep(0.14)
            if control_is_checked(control) != should:
                missed.append(option_letter(control.Name) or "?")
        return "checked" if not missed else "miss-" + "".join(missed)
    target = targets[0]
    wanted_text = options.get(answer) or option_body(target.Name)
    for _ in range(4):
        ok, page_letter = _single_choice_ok(group, target, wanted_text)
        if ok:
            return "selected" if page_letter == answer else f"selected@{page_letter or '?'}"
        _activate_choice(target)
        time.sleep(0.2)
    ok, page_letter = _single_choice_ok(group, target, wanted_text)
    if ok:
        return "selected" if page_letter == answer else f"selected@{page_letter or '?'}"
    return "partial-" + (page_letter or "?")


def _compact_text(text: str) -> str:
    # 去掉空白和编辑器常见的零宽字符，避免「左」误判成已写入「左外」
    cleaned = re.sub(r"[\u200b\u200c\u200d\ufeff\u2060]", "", text or "")
    return re.sub(r"\s+", "", cleaned)


def _answer_landed(actual: str | None, expected: str) -> bool:
    """必须整段一致。不能用包含判断，否则「左」会把残留的「左外」当成成功。"""
    wanted = _compact_text(expected)
    if actual is None or not wanted:
        return False
    return _compact_text(actual) == wanted


def _editor_text(editor) -> str | None:
    try:
        pattern = editor.GetTextPattern()
        if pattern:
            return pattern.DocumentRange.GetText(-1)
    except Exception:
        pass
    try:
        value = editor.GetValuePattern()
        if value is not None:
            return value.Value or ""
    except Exception:
        pass
    return None


def _focus_is_editor(focused, editor) -> bool:
    """填空获得光标后，Chrome 仍把焦点报成整页文档「作业作答」。

    只要该焦点的区域包含编辑区中心，就说明刚才点中的是这道题。
    地址栏不包含编辑区中心，不会被当成编辑区。
    """
    if focused is None or focused.ControlTypeName not in {"DocumentControl", "EditControl"}:
        return False
    editor_rect = editor.BoundingRectangle
    focused_rect = focused.BoundingRectangle
    if editor_rect.isempty() or focused_rect.isempty():
        return False
    if focused_rect.contains(editor_rect.xcenter(), editor_rect.ycenter()):
        if focused.ControlTypeName == "EditControl":
            overlap = editor_rect.intersect(focused_rect)
            if overlap.width() < 8 or overlap.height() < 8:
                return False
        return True
    try:
        return tuple(focused.GetRuntimeId() or []) == tuple(editor.GetRuntimeId() or [])
    except Exception:
        return False


def _dismiss_paste_block() -> None:
    import uiautomation as auto

    current = auto.GetFocusedControl()
    texts = []
    for _ in range(6):
        if current is None:
            break
        texts.append(current.Name or "")
        current = current.GetParentControl()
    blob = "\n".join(texts)
    if not any(phrase in blob for phrase in ("不允许粘贴", "禁止粘贴", "不能粘贴", "无法粘贴")):
        return
    auto.SendKeys("{Esc}", waitTime=0.12, charMode=False)


def _focus_editor(editor, window) -> str | None:
    import uiautomation as auto

    bounds = window.BoundingRectangle
    rect = None
    for _ in range(4):
        scroll_into_view(editor)
        time.sleep(0.12)
        rect = editor.BoundingRectangle
        if (
            bounds.contains(rect.xcenter(), rect.ycenter())
            and bounds.top + 70 <= rect.ycenter() <= bounds.bottom - 20
        ):
            break
    else:
        return "offscreen"
    auto.Click(rect.xcenter(), rect.ycenter(), waitTime=0.2)
    if _focus_is_editor(auto.GetFocusedControl(), editor):
        return None
    try:
        editor.SetFocus()
    except Exception:
        return "focus-failed"
    time.sleep(0.1)
    if _focus_is_editor(auto.GetFocusedControl(), editor):
        return None
    return "focus-failed"


def _clear_editor() -> None:
    import uiautomation as auto

    auto.SendKeys("{Ctrl}a", waitTime=0.05, charMode=False)
    auto.SendKeys("{Back}", waitTime=0.08, charMode=False)


def _type_answer(text: str, multiline: bool, interval: float) -> None:
    import uiautomation as auto

    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    for char in normalized:
        if char == "\n":
            if multiline:
                auto.SendKeys("{Enter}", interval=0, waitTime=interval, charMode=False)
            else:
                auto.SendUnicodeChar(" ", True)
                time.sleep(interval)
            continue
        if char == "\t":
            char = " "
        auto.SendUnicodeChar(char, True)
        time.sleep(interval)


def _selected_text(_window) -> str | None:
    """编辑区没有值接口。全选复制才能读到文字，空编辑区返回空字符串。"""
    import uiautomation as auto

    sentinel = "\u2060"
    auto.SetClipboardText(sentinel)
    auto.SendKeys("{Ctrl}a", waitTime=0.06, charMode=False)
    auto.SendKeys("{Ctrl}c", waitTime=0.12, charMode=False)
    copied = auto.GetClipboardText()
    if copied is None or copied == sentinel:
        return ""
    if len(copied) > 1500:
        auto.SendKeys("{Right}", waitTime=0.02, charMode=False)
        return None
    return copied


def _current_editor_text(editor, window) -> str | None:
    direct = _editor_text(editor)
    if direct:
        return direct
    return _selected_text(window)


def _ensure_editor_focus(editor, window) -> str | None:
    import uiautomation as auto

    if _focus_is_editor(auto.GetFocusedControl(), editor):
        return None
    return _focus_editor(editor, window)


def paste_into_editor(editor, answer: str, window, multiline: bool = False) -> str:
    """先清空再粘贴。页面禁止粘贴或内容对不上时，改为逐字输入。"""
    import uiautomation as auto

    error = _focus_editor(editor, window)
    if error:
        return error
    # 必须先清空旧内容，否则短答案会叠在「左外」这类旧词上
    _clear_editor()
    error = _ensure_editor_focus(editor, window)
    if error:
        return error
    auto.SetClipboardText(answer)
    auto.SendKeys("{Ctrl}v", waitTime=0.16, charMode=False)
    _dismiss_paste_block()
    error = _ensure_editor_focus(editor, window)
    if error:
        return error
    if _answer_landed(_current_editor_text(editor, window), answer):
        return "已粘贴"
    _clear_editor()
    for attempt, interval in enumerate((0.01, 0.02)):
        _type_answer(answer, multiline, interval)
        current = _current_editor_text(editor, window)
        if _answer_landed(current, answer):
            return "逐字写入"
        # 读不到内容时不贸然当成成功，再试一次更稳
        if current is None and attempt == 1:
            return "逐字写入"
        if attempt == 0:
            _clear_editor()
    return "写入未生效"


def _group_needs_upload(group) -> bool:
    found = False

    def walk(control, depth: int) -> None:
        nonlocal found
        if found or depth > 8 or control is None:
            return
        name = control.Name or ""
        if any(word in name for word in ("上传图片", "上传附件", "插入图片", "选择图片")):
            found = True
            return
        for child in control.GetChildren():
            walk(child, depth + 1)

    walk(group, 0)
    return found


def fill_blank(group, answer: str | list, window=None, multiline: bool = False) -> str:
    if str(answer).strip() == IMAGE_UPLOAD_ANSWER:
        return IMAGE_UPLOAD_ANSWER
    editors = blank_editors(group)
    if not editors or window is None:
        if group is not None and _group_needs_upload(group):
            return IMAGE_UPLOAD_ANSWER
        return "no-editor"
    parts = blank_parts(answer, len(editors))
    if not parts:
        return f"空位{len(editors)}与答案段数不符"
    statuses = []
    for index, (editor, part) in enumerate(zip(editors, parts), start=1):
        status = paste_into_editor(editor, part, window, multiline=multiline)
        if status not in {"已粘贴", "逐字写入"}:
            return f"第{index}空{status}"
        statuses.append(status)
    if len(editors) == 1:
        return statuses[0]
    if any(status == "逐字写入" for status in statuses):
        return f"逐字写入-{len(editors)}空"
    return f"已粘贴-{len(editors)}空"


def verify_answers(
    records: list[dict],
    window_title: str | None = None,
    window_handle: int | None = None,
    only: set[int] | None = None,
) -> list[dict]:
    window = connect_window(window_title, handle=window_handle)
    groups = collect_question_groups(window)
    mismatches = []
    for record in records:
        if only and record["no"] not in only:
            continue
        if record["type"] in {"blank", "essay"}:
            continue
        group = groups.get(record["no"])
        if group is None:
            mismatches.append({"no": record["no"], "expected": record["answer"], "actual": "页面上没有这题"})
            continue
        options = record.get("options") or {}
        expected = record["answer"]
        filled = filled_answer_from_group(group)
        selected = _selected_controls(choice_controls(group))
        if record["type"] == "judge":
            expected_truth = desired_truth(expected, options)
            truths = [truth for control in selected if (truth := option_truth(control.Name))]
            picked_letters = [letter for control in selected if (letter := option_letter(control.Name))]
            actual = truths[0] if len(truths) == 1 else (filled or "".join(picked_letters))
            wanted = expected_truth or expected
            if actual != wanted:
                mismatches.append({"no": record["no"], "expected": wanted, "actual": actual or "?"})
            continue
        if record["type"] == "multi":
            picked_letters = [letter for control in selected if (letter := option_letter(control.Name))]
            actual = "".join(sorted(picked_letters or list(filled)))
            wanted = "".join(sorted(expected))
            if actual != wanted:
                mismatches.append({"no": record["no"], "expected": wanted, "actual": actual or "?"})
            continue
        # 单选：按选项正文核对，兼容打回重做后字母换位
        wanted_text = options.get(expected) or ""
        ok = False
        picked_letters = [letter for control in selected if (letter := option_letter(control.Name))]
        actual = filled or "".join(picked_letters)
        if wanted_text and selected:
            ok = any(_texts_match(option_body(control.Name), wanted_text) for control in selected)
        elif wanted_text and filled:
            matched = next(
                (control for control in choice_controls(group) if option_letter(control.Name) == filled),
                None,
            )
            ok = matched is not None and _texts_match(option_body(matched.Name), wanted_text)
        else:
            ok = actual == expected
        if not ok:
            mismatches.append({"no": record["no"], "expected": expected, "actual": actual or "?"})
    return mismatches


def command_parse(args: argparse.Namespace) -> None:
    questions, skipped = parse_questions(load_page_text(args.page))
    note = describe_skipped(skipped)
    if note:
        print(note)
    counts: dict[str, int] = {}
    for question in questions:
        counts[question["type"]] = counts.get(question["type"], 0) + 1
    print(f"共 {len(questions)} 题 {counts}")
    for question in questions:
        letters = "".join(question["options"])
        stem = question["stem"].replace("\n", " ")
        print(f"{question['no']:03d} {question['type']:6} [{letters}] {stem[:48]}")


def command_solve(args: argparse.Namespace) -> None:
    questions, skipped = parse_questions(load_page_text(args.page))
    note = describe_skipped(skipped)
    if note:
        print(note)
    records = solve_questions(questions, config_from_args(args))
    RUNTIME.mkdir(exist_ok=True)
    ANSWERS_PATH.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已写入 {ANSWERS_PATH}")
    for record in records:
        print(f"{record['no']:03d} {record['answer']}")


def command_apply(args: argparse.Namespace) -> None:
    records = json.loads(ANSWERS_PATH.read_text(encoding="utf-8"))
    only = set(args.only) if args.only else None
    RUNTIME.mkdir(exist_ok=True)
    apply_answers(records, only, window_title=args.window or None)


def command_verify(args: argparse.Namespace) -> None:
    records = json.loads(ANSWERS_PATH.read_text(encoding="utf-8"))
    mismatches = verify_answers(records, window_title=args.window or None)
    if not mismatches:
        print("选择类题目与答案一致")
        return
    print(f"有 {len(mismatches)} 题不一致")
    for item in mismatches:
        print(f"第 {item['no']} 题 期望 {item['expected']} 实际 {item['actual']}")


def config_from_args(args: argparse.Namespace) -> dict:
    return {
        "api_key": getattr(args, "api_key", "") or "",
        "base_url": getattr(args, "base_url", "") or "",
        "model": getattr(args, "model", "") or "",
    }


def add_openai_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--api-key", default="", help="OpenAI API Key，也可用环境变量或界面保存")
    parser.add_argument("--base-url", default="", help="OpenAI 兼容接口地址")
    parser.add_argument("--model", default="", help="模型名，例如 gpt-4o-mini、gpt-4.1、o4-mini")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="作业读题、生成答案并在浏览器中选择，不提交")
    parser.add_argument("--page", type=Path, default=PAGE_TEXT)
    sub = parser.add_subparsers(dest="command", required=True)
    parse_cmd = sub.add_parser("parse")
    parse_cmd.set_defaults(func=command_parse)
    solve_cmd = sub.add_parser("solve")
    add_openai_args(solve_cmd)
    solve_cmd.set_defaults(func=command_solve)
    apply_cmd = sub.add_parser("apply")
    apply_cmd.add_argument("--only", type=int, nargs="*")
    apply_cmd.add_argument("--window", default="", help="作业窗口标题，多个窗口时必填")
    apply_cmd.set_defaults(func=command_apply)
    verify_cmd = sub.add_parser("verify")
    verify_cmd.add_argument("--window", default="", help="作业窗口标题，多个窗口时必填")
    verify_cmd.set_defaults(func=command_verify)
    gui_cmd = sub.add_parser("gui")
    gui_cmd.set_defaults(func=lambda _args: launch_gui())
    args = parser.parse_args(argv)
    args.func(args)


def launch_gui() -> None:
    from auto_answer.gui import launch

    launch()


if __name__ == "__main__":
    main()
