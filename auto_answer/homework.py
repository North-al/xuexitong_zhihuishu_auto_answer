"""读取作业文本，调用 OpenAI 兼容接口作答，并在已打开的浏览器里选择答案。

只做选择、勾选和填空。不会点击提交、交卷或暂时保存。
"""

from __future__ import annotations

import argparse
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

QUESTION_RE = re.compile(
    r"(?m)^(\d+)\. \((单选题|多选题|判断题|填空题)\)\n(.*?)(?=^\d+\. \((?:单选题|多选题|判断题|填空题)\)|\Z)",
    re.S,
)
OPTION_RE = re.compile(r"(?m)^([A-H])\n(.*?)(?=\n[A-H]\n|\Z)", re.S)
FORBIDDEN_NAMES = ("提交", "交卷", "暂时保存", "保存并提交", "确认提交")

TYPE_MAP = {
    "单选题": "single",
    "多选题": "multi",
    "判断题": "judge",
    "填空题": "blank",
}


def load_page_text(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    footers = list(re.finditer(r"\n一\. 单选题（\d+分）", text))
    if footers:
        text = text[: footers[-1].start()]
    return text.strip() + "\n"


def parse_questions(text: str) -> list[dict]:
    questions: list[dict] = []
    for match in QUESTION_RE.finditer(text):
        number = int(match.group(1))
        raw_type = match.group(2)
        body = match.group(3).strip("\n")
        kind = TYPE_MAP[raw_type]
        if kind == "blank":
            labels = re.findall(r"(?m)^第(\d+)空\s*$", body)
            stem = re.sub(r"(?m)^第\d+空\s*$\n?", "", body)
            stem = re.sub(r"(?m)^[一二三四五六七八九十]+\.\s*\S*题（[^）]*）\s*$", "", stem).strip()
            blanks = len(labels) or max(len(re.findall(r"_{3,}|＿{3,}", stem)), 1)
            options: dict[str, str] = {}
        else:
            option_match = re.search(r"(?m)^[A-H]\n", body)
            if not option_match:
                raise ValueError(f"第 {number} 题没有解析到选项")
            stem = body[: option_match.start()].strip()
            options = {}
            for opt in OPTION_RE.finditer(body[option_match.start() :]):
                letter = opt.group(1)
                content = re.sub(r"\n{2,}", "\n", opt.group(2).strip())
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
                "blanks": blanks if kind == "blank" else 1,
            }
        )
    questions.sort(key=lambda item: item["no"])
    numbers = [item["no"] for item in questions]
    if numbers != list(range(1, len(questions) + 1)):
        raise ValueError(f"题号不连续: {numbers}")
    return questions


def openai_config(override: dict | None = None) -> tuple[str, str, str]:
    config = resolve_config(override)
    if not config["api_key"]:
        raise SystemExit("缺少 API Key。请在界面中保存，或设置环境变量 OPENAI_API_KEY")
    return config["api_key"], config["base_url"], config["model"]


def chat_json(api_key: str, base_url: str, model: str, messages: list[dict]) -> dict:
    payload: dict = {
        "model": model,
        "messages": messages,
    }
    if "reasoner" not in model.lower():
        payload["temperature"] = 0
    request = urllib.request.Request(
        f"{base_url}/chat/completions",
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
    content = (data["choices"][0]["message"].get("content") or "").strip()
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
    if kind == "blank":
        count = int(question.get("blanks") or 1)
        parts = blank_parts(raw, count)
        if not parts or any(len(part) > 200 for part in parts):
            raise ValueError(f"第 {question['no']} 题填空答案异常: {raw!r}")
        return parts[0] if count <= 1 else parts
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


def solve_questions(questions: list[dict], config: dict | None = None, log=print, on_update=None) -> list[dict]:
    api_key, base_url, model = openai_config(config)
    solved: dict[int, str | list] = {}
    batch_size = 10
    for offset in range(0, len(questions), batch_size):
        batch = questions[offset : offset + batch_size]
        payload = []
        for item in batch:
            entry = {
                "no": item["no"],
                "type": item["raw_type"],
                "stem": item["stem"],
                "options": item["options"],
            }
            if item["type"] == "blank":
                entry["blanks"] = item.get("blanks") or 1
            payload.append(entry)
        messages = [
            {
                "role": "system",
                "content": (
                    "你是课程测验阅卷助手。只根据题干和选项作答。"
                    "单选题的 value 只能是一个大写字母。"
                    "多选题的 value 是所有正确选项字母按字母序连写，例如 AD。"
                    "判断题的 value 只能是「对」或「错」。选项顺序不固定，A 可能是对，也可能是错。"
                    "填空题只有一个空时，value 是字符串，只写应填入横线的内容。"
                    "填空题有多个空时，value 必须是数组，每个空一项，按空的顺序排列。"
                    "不要用逗号、分号、竖线或换行把多个空拼成一个字符串，答案内容里可以包含这些符号。"
                    "每个空只写该空的内容，不要解释。"
                    "代码题要按题目语言判断能否编译，并在能运行时追踪输出。"
                    "只输出一个 JSON 对象，格式为 "
                    '{"answers":[{"no":1,"value":"B"}]}，不要输出其他文字。'
                ),
            },
            {
                "role": "user",
                "content": json.dumps({"questions": payload}, ensure_ascii=False),
            },
        ]
        numbers = [item["no"] for item in batch]
        log(f"正在请求 {model}: 第 {numbers[0]}-{numbers[-1]} 题")
        last_error = None
        for _ in range(2):
            try:
                result = chat_json(api_key, base_url, model, messages)
                answers = result["answers"]
                got = {}
                for item in answers:
                    got[int(item["no"])] = item["value"]
                for question in batch:
                    solved[question["no"]] = normalize_answer(question, got[question["no"]])
                last_error = None
                break
            except Exception as exc:  # noqa: BLE001 - retry the whole batch once
                last_error = exc
                log(f"本批需要重试: {exc}")
                time.sleep(1)
        if last_error:
            raise SystemExit(f"第 {numbers[0]}-{numbers[-1]} 题作答失败: {last_error}")
        current = _answer_records(questions, solved)
        RUNTIME.mkdir(exist_ok=True)
        ANSWERS_PATH.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
        if on_update:
            on_update(current)
    return _answer_records(questions, solved)


def _answer_records(questions: list[dict], solved: dict[int, str | list]) -> list[dict]:
    return [
        {
            "no": question["no"],
            "type": question["type"],
            "answer": solved.get(question["no"], ""),
            "stem": question["stem"],
            "options": question["options"],
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
        if record["type"] == "blank":
            status = fill_blank(group, record["answer"], window)
        else:
            status = choose_option(group, record["type"], record["answer"], record.get("options") or {})
        line = {"no": number, "answer": record["answer"], "status": status}
        log(f"第 {number} 题 -> {record['answer']} ({status})")
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(line, ensure_ascii=False) + "\n")
        time.sleep(0.05)


def control_is_checked(control) -> bool:
    import uiautomation as auto

    legacy = control.GetPattern(auto.PatternId.LegacyIAccessiblePattern)
    action = legacy.DefaultAction if legacy else ""
    if "取消" in action:
        return True
    if "选定" in action:
        return False
    return control.GetTogglePattern().ToggleState == auto.ToggleState.On


def choose_option(group, kind: str, answer: str, options: dict | None = None) -> str:
    import uiautomation as auto

    controls = choice_controls(group)
    if not controls:
        return "no-controls"
    if kind == "judge":
        truth = desired_truth(answer, options)
        target = next((control for control in controls if option_truth(control.Name) == truth), None) if truth else None
        if target is None:
            target = next((control for control in controls if option_letter(control.Name) == answer), None)
        if target is None:
            return "missing"
        pattern = target.GetSelectionItemPattern()
        if not pattern.IsSelected:
            pattern.Select(waitTime=0.05)
        return "selected"
    wanted = set(answer)
    if kind == "multi":
        missed = []
        for control in controls:
            letter = option_letter(control.Name)
            should = letter in wanted
            if control_is_checked(control) == should:
                continue
            legacy = control.GetPattern(auto.PatternId.LegacyIAccessiblePattern)
            if legacy is None or not legacy.DoDefaultAction(waitTime=0.15):
                missed.append(letter)
                continue
            time.sleep(0.12)
            if control_is_checked(control) != should:
                missed.append(letter)
        return "checked" if not missed else "miss-" + "".join(missed)
    target = next((control for control in controls if option_letter(control.Name) == answer), None)
    if target is None:
        return "missing"
    pattern = target.GetSelectionItemPattern()
    if not pattern.IsSelected:
        pattern.Select(waitTime=0.05)
    picked = [
        option_letter(control.Name)
        for control in controls
        if control.GetSelectionItemPattern().IsSelected
    ]
    if picked == [answer]:
        return "selected"
    return "partial-" + "".join(picked)


def paste_into_editor(editor, answer: str, window) -> str:
    import uiautomation as auto

    bounds = window.BoundingRectangle
    rect = None
    for _ in range(4):
        scroll_into_view(editor)
        time.sleep(0.12)
        rect = editor.BoundingRectangle
        if bounds.top + 70 <= rect.ycenter() <= bounds.bottom - 20:
            break
    else:
        return "offscreen"
    auto.Click(rect.xcenter(), rect.ycenter(), waitTime=0.2)
    focused = auto.GetFocusedControl()
    if focused is None or focused.ControlTypeName != "DocumentControl":
        return "focus-failed"
    auto.SetClipboardText(answer)
    auto.SendKeys("{Ctrl}a", waitTime=0.05, charMode=False)
    auto.SendKeys("{Ctrl}v", waitTime=0.12, charMode=False)
    return "filled"


def fill_blank(group, answer: str | list, window=None) -> str:
    editors = blank_editors(group)
    if not editors or window is None:
        return "no-editor"
    parts = blank_parts(answer, len(editors))
    if not parts:
        return f"空位{len(editors)}与答案段数不符"
    for index, (editor, part) in enumerate(zip(editors, parts), start=1):
        status = paste_into_editor(editor, part, window)
        if status != "filled":
            return f"第{index}空{status}"
    if len(editors) == 1:
        return "filled"
    return f"filled-{len(editors)}空"


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
        if record["type"] == "blank":
            continue
        group = groups.get(record["no"])
        if group is None:
            mismatches.append({"no": record["no"], "expected": record["answer"], "actual": "页面上没有这题"})
            continue
        picked = []
        truths = []
        for control in choice_controls(group):
            letter = option_letter(control.Name)
            if control.ControlTypeName == "CheckBoxControl":
                on = control_is_checked(control)
            else:
                on = control.GetSelectionItemPattern().IsSelected
            if on:
                picked.append(letter)
                truth = option_truth(control.Name)
                if truth:
                    truths.append(truth)
        if record["type"] == "judge":
            expected_truth = desired_truth(record["answer"], record.get("options") or {})
            actual = truths[0] if len(truths) == 1 else "".join(picked)
            expected = expected_truth or record["answer"]
        else:
            actual = "".join(sorted(picked))
            expected = "".join(sorted(record["answer"]))
        if actual != expected:
            mismatches.append({"no": record["no"], "expected": expected, "actual": actual})
    return mismatches


def command_parse(args: argparse.Namespace) -> None:
    questions = parse_questions(load_page_text(args.page))
    counts: dict[str, int] = {}
    for question in questions:
        counts[question["type"]] = counts.get(question["type"], 0) + 1
    print(f"共 {len(questions)} 题 {counts}")
    for question in questions:
        letters = "".join(question["options"])
        stem = question["stem"].replace("\n", " ")
        print(f"{question['no']:03d} {question['type']:6} [{letters}] {stem[:48]}")


def command_solve(args: argparse.Namespace) -> None:
    questions = parse_questions(load_page_text(args.page))
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
