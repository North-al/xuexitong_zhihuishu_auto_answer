"""超星作业答题的桌面操作窗口。"""

from __future__ import annotations

import ctypes
import queue
import threading
import tkinter as tk
from tkinter import messagebox, ttk

from auto_answer.browser import copy_page_text, describe_inspection, inspect_homework_window, list_homework_windows
from auto_answer.config import PROVIDERS, icon_file, load_store, save_store
from auto_answer.homework import (
    ANSWERS_PATH,
    PAGE_TEXT,
    apply_answers,
    load_page_text,
    describe_skipped,
    parse_questions,
    save_question_images,
    solve_questions,
    verify_answers,
)

TYPE_LABEL = {"single": "单选", "multi": "多选", "judge": "判断", "blank": "填空", "essay": "简答"}


def show_answer(answer) -> str:
    if isinstance(answer, list):
        return " / ".join(str(part) for part in answer)
    return str(answer or "")

INK = "#16324f"
INK_SOFT = "#8ea3b5"
PAPER = "#e7eef3"
CARD = "#ffffff"
LINE = "#d3dee6"
TEXT = "#1c2b36"
MUTED = "#5d6e7c"
TEAL = "#0f6e78"
TEAL_DEEP = "#0c5962"
BRASS = "#c4a15a"
WARN_BG = "#fff6e8"
WARN_FG = "#8a5a16"
OK_BG = "#e7f4f2"
OK_FG = "#0f6e56"
LOG_BG = "#12202c"
STEP_NAMES = ("选窗口", "更新题目", "生成答案", "写入核对")


class App(tk.Tk):
    def __init__(self) -> None:
        _enable_dpi()
        super().__init__()
        self.title("超星作业答题")
        icon = icon_file()
        if icon.exists():
            self.iconbitmap(str(icon))
        self.geometry("1180x760")
        self.minsize(980, 640)
        self.configure(bg=PAPER)
        self.events: queue.Queue = queue.Queue()
        self.worker: threading.Thread | None = None
        self.windows: list[dict] = []
        self.records: list[dict] = []
        self._inspect_token = 0
        self.questions_current = False
        self.question_work_id = ""
        self.question_name = ""
        self.detected_work_id = ""
        self.detected_name = ""
        self._profiles: dict = {}
        self._active_provider = ""
        self._build_style()
        self._build()
        self._load_config_into_form()
        self._load_saved_answers()
        self._refresh_question_banner()
        self.refresh_windows()
        self.after(120, self._poll)

    def _build_style(self) -> None:
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure(".", font=("Microsoft YaHei UI", 10), background=PAPER)
        style.configure("TFrame", background=PAPER)
        style.configure("TLabel", background=PAPER, foreground=TEXT)
        style.configure("TEntry", fieldbackground=CARD, bordercolor=LINE, lightcolor=LINE, darkcolor=LINE, padding=6)
        style.configure("TCombobox", fieldbackground=CARD, padding=6, arrowsize=14)
        style.map("TCombobox", fieldbackground=[("readonly", CARD)])
        style.configure(
            "TButton",
            padding=(14, 7),
            background="#ffffff",
            foreground="#606266",
            bordercolor="#dcdfe6",
            lightcolor="#dcdfe6",
            darkcolor="#dcdfe6",
            borderwidth=1,
            relief="solid",
            focuscolor="#dcdfe6",
        )
        style.map(
            "TButton",
            background=[("active", "#ecf5ff"), ("pressed", "#ecf5ff"), ("disabled", "#ffffff")],
            foreground=[("active", "#409eff"), ("pressed", "#409eff"), ("disabled", "#c0c4cc")],
            bordercolor=[("active", "#c6e2ff"), ("pressed", "#c6e2ff"), ("disabled", "#ebeef5")],
            lightcolor=[("active", "#c6e2ff"), ("disabled", "#ebeef5")],
            darkcolor=[("active", "#c6e2ff"), ("disabled", "#ebeef5")],
        )
        style.configure(
            "Primary.TButton",
            padding=(16, 7),
            background="#409eff",
            foreground="#ffffff",
            bordercolor="#409eff",
            lightcolor="#409eff",
            darkcolor="#409eff",
            borderwidth=1,
            relief="solid",
            focuscolor="#409eff",
            font=("Microsoft YaHei UI", 10),
        )
        style.map(
            "Primary.TButton",
            background=[("active", "#66b1ff"), ("pressed", "#3a8ee6"), ("disabled", "#a0cfff")],
            foreground=[("disabled", "#ffffff")],
            bordercolor=[("active", "#66b1ff"), ("pressed", "#3a8ee6"), ("disabled", "#a0cfff")],
            lightcolor=[("active", "#66b1ff"), ("pressed", "#3a8ee6"), ("disabled", "#a0cfff")],
            darkcolor=[("active", "#66b1ff"), ("pressed", "#3a8ee6"), ("disabled", "#a0cfff")],
        )
        style.configure(
            "Plain.TButton",
            padding=(14, 7),
            background="#ecf5ff",
            foreground="#409eff",
            bordercolor="#b3d8ff",
            lightcolor="#b3d8ff",
            darkcolor="#b3d8ff",
            borderwidth=1,
            relief="solid",
            focuscolor="#b3d8ff",
            font=("Microsoft YaHei UI", 10),
        )
        style.map(
            "Plain.TButton",
            background=[("active", "#409eff"), ("pressed", "#3a8ee6"), ("disabled", "#f4f4f5")],
            foreground=[("active", "#ffffff"), ("pressed", "#ffffff"), ("disabled", "#c0c4cc")],
            bordercolor=[("active", "#409eff"), ("pressed", "#3a8ee6"), ("disabled", "#e9e9eb")],
            lightcolor=[("active", "#409eff"), ("pressed", "#3a8ee6"), ("disabled", "#e9e9eb")],
            darkcolor=[("active", "#409eff"), ("pressed", "#3a8ee6"), ("disabled", "#e9e9eb")],
        )
        style.configure("Treeview", rowheight=30, font=("Microsoft YaHei UI", 10), background=CARD, fieldbackground=CARD, foreground=TEXT, borderwidth=0)
        style.configure("Treeview.Heading", font=("Microsoft YaHei UI", 10, "bold"), background="#f4f7f9", foreground="#3d5160", relief="flat")
        style.map("Treeview", background=[("selected", "#d7ecea")], foreground=[("selected", "#12323a")])
        style.configure("Vertical.TScrollbar", background=LINE, troughcolor="#f4f7f9", borderwidth=0, arrowsize=12)
        style.configure("TPanedwindow", background=PAPER)

    def _build(self) -> None:
        self._build_header()
        footer = tk.Frame(self, bg=INK, height=28)
        footer.pack(fill="x", side="bottom")
        self.footer_text = tk.Label(footer, text="就绪", bg=INK, fg=INK_SOFT, anchor="w", font=("Microsoft YaHei UI", 9), padx=16, pady=5)
        self.footer_text.pack(fill="x")
        outer = ttk.Frame(self, padding=(16, 14, 16, 10))
        outer.pack(fill="both", expand=True)

        browser_shell, browser = self._card(outer, "作业窗口")
        browser_shell.pack(fill="x")
        browser_row = tk.Frame(browser, bg=CARD)
        browser_row.pack(fill="x", pady=(8, 0))
        tools = tk.Frame(browser_row, bg=CARD)
        tools.pack(side="right")
        self.refresh_button = ttk.Button(tools, text="刷新窗口", style="Plain.TButton", command=self.refresh_windows)
        self.refresh_button.pack(side="left")
        self.detect_button = ttk.Button(tools, text="检测窗口", style="Plain.TButton", command=self.inspect_selected)
        self.detect_button.pack(side="left", padx=(8, 0))
        self.window_var = tk.StringVar()
        self.window_box = ttk.Combobox(browser_row, textvariable=self.window_var, state="readonly")
        self.window_box.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.window_box.bind("<<ComboboxSelected>>", lambda _event: self.inspect_selected())
        self.window_info = tk.Label(
            browser,
            text="选中窗口后会显示作业名称、题量和地址，并闪烁对应窗口。",
            bg=CARD,
            fg=MUTED,
            justify="left",
            anchor="w",
            font=("Microsoft YaHei UI", 10),
        )
        self.window_info.pack(fill="x", pady=(8, 0))
        self.window_url_var = tk.StringVar()
        self.window_url = ttk.Entry(browser, textvariable=self.window_url_var, state="readonly")
        self.window_url.pack(fill="x", pady=(6, 0))

        ai_shell, ai = self._card(outer, "答题接口")
        ai_shell.pack(fill="x", pady=(10, 0))
        form = tk.Frame(ai, bg=CARD)
        form.pack(fill="x", pady=(8, 0))
        form.columnconfigure(1, weight=1)
        form.columnconfigure(3, weight=1)
        self._field_label(form, "服务", 0, 0)
        self.provider_var = tk.StringVar(value="自定义")
        self.provider = ttk.Combobox(form, textvariable=self.provider_var, values=("OpenAI", "DeepSeek", "自定义"), state="readonly")
        self.provider.grid(row=0, column=1, sticky="ew", pady=4)
        self.provider.bind("<<ComboboxSelected>>", self._on_provider)
        self._field_label(form, "模型", 0, 2)
        self.model = ttk.Entry(form)
        self.model.grid(row=0, column=3, sticky="ew", pady=4)
        self._field_label(form, "接口地址", 1, 0)
        self.base_url = ttk.Entry(form)
        self.base_url.grid(row=1, column=1, columnspan=3, sticky="ew", pady=4)
        self._field_label(form, "API Key", 2, 0)
        self.api_key = ttk.Entry(form, show="*")
        self.api_key.grid(row=2, column=1, columnspan=2, sticky="ew", pady=4)
        self.save_button = ttk.Button(form, text="保存配置", style="Plain.TButton", command=self.save_settings)
        self.save_button.grid(row=2, column=3, sticky="e", padx=(12, 0))
        tk.Label(
            ai,
            text="切换服务会换上该服务已保存的地址、密钥和模型。模型请手动填写。",
            bg=CARD,
            fg=MUTED,
            anchor="w",
            font=("Microsoft YaHei UI", 9),
        ).pack(fill="x", pady=(6, 0))

        actions = ttk.Frame(outer)
        actions.pack(fill="x", pady=(12, 10))
        self.buttons = [
            ttk.Button(actions, text="更新题目", style="Primary.TButton", command=self.update_questions),
            ttk.Button(actions, text="生成答案", style="Primary.TButton", command=self.generate),
            ttk.Button(actions, text="写入页面", style="Primary.TButton", command=self.write_page),
            ttk.Button(actions, text="从剪贴板读取", command=self.read_clipboard),
            ttk.Button(actions, text="核对", command=self.verify),
        ]
        for button in self.buttons:
            button.pack(side="left", padx=(0, 8))
        ttk.Label(actions, text="题号").pack(side="left", padx=(10, 6))
        self.only_var = tk.StringVar()
        ttk.Entry(actions, textvariable=self.only_var, width=16).pack(side="left")
        ttk.Label(actions, text="留空为全部题目", foreground=MUTED).pack(side="left", padx=8)

        body = ttk.Panedwindow(outer, orient="horizontal")
        body.pack(fill="both", expand=True)
        left = tk.Frame(body, bg=PAPER)
        right = tk.Frame(body, bg=LINE, padx=1, pady=1)
        body.add(left, weight=3)
        body.add(right, weight=2)

        self.status_strip = tk.Frame(left, bg=WARN_BG)
        self.status_mark = tk.Frame(self.status_strip, bg=BRASS, width=4)
        self.status_mark.pack(side="left", fill="y")
        self.question_status = tk.Label(
            self.status_strip,
            text="",
            bg=WARN_BG,
            fg=WARN_FG,
            anchor="w",
            justify="left",
            font=("Microsoft YaHei UI", 10),
            padx=12,
            pady=8,
        )
        self.question_status.pack(side="left", fill="both", expand=True)
        self.status_strip.pack(fill="x", pady=(0, 8))

        table = tk.Frame(left, bg=LINE, padx=1, pady=1)
        table.pack(fill="both", expand=True)
        columns = ("no", "type", "answer", "stem")
        self.tree = ttk.Treeview(table, columns=columns, show="headings")
        self.tree.heading("no", text="题号")
        self.tree.heading("type", text="类型")
        self.tree.heading("answer", text="答案")
        self.tree.heading("stem", text="题干")
        self.tree.column("no", width=64, anchor="center", stretch=False)
        self.tree.column("type", width=72, anchor="center", stretch=False)
        self.tree.column("answer", width=88, anchor="center", stretch=False)
        self.tree.column("stem", width=460, stretch=True)
        self.tree.tag_configure("alt", background="#f7fafb")
        self.tree.tag_configure("stale", foreground="#8b97a1")
        self.tree.tag_configure("stale-alt", background="#f7fafb", foreground="#8b97a1")
        scroll = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="left", fill="y")

        log_panel = tk.Frame(right, bg=LOG_BG)
        log_panel.pack(fill="both", expand=True)
        tk.Label(log_panel, text="操作记录", bg=LOG_BG, fg=INK_SOFT, anchor="w", font=("Microsoft YaHei UI", 9, "bold"), padx=12, pady=8).pack(fill="x")
        self.log = tk.Text(
            log_panel,
            wrap="word",
            bg=LOG_BG,
            fg="#d5e0e8",
            insertbackground="#d5e0e8",
            font=("Consolas", 10),
            relief="flat",
            padx=12,
            pady=4,
            highlightthickness=0,
        )
        self.log.pack(fill="both", expand=True)
        self.log.configure(state="disabled")
        self.bind("<Configure>", self._fit_wraps, add="+")

    def _build_header(self) -> None:
        header = tk.Frame(self, bg=INK, padx=18, pady=14)
        header.pack(fill="x")
        title_row = tk.Frame(header, bg=INK)
        title_row.pack(fill="x")
        tk.Label(title_row, text="超星作业答题", bg=INK, fg="#f4f7f8", font=("Microsoft YaHei UI", 20, "bold")).pack(side="left")
        tk.Label(title_row, text="只选题和填空，不点击提交", bg=INK, fg=BRASS, font=("Microsoft YaHei UI", 10)).pack(side="right", pady=(8, 0))
        steps = tk.Frame(header, bg=INK)
        steps.pack(fill="x", pady=(12, 0))
        self.step_badges: list[tuple[tk.Label, tk.Label]] = []
        for index, name in enumerate(STEP_NAMES, start=1):
            item = tk.Frame(steps, bg=INK)
            item.pack(side="left", padx=(0, 22))
            badge = tk.Label(item, text=str(index), bg="#2a4662", fg=INK_SOFT, font=("Microsoft YaHei UI", 9, "bold"), width=2, padx=1, pady=1)
            badge.pack(side="left", padx=(0, 6))
            label = tk.Label(item, text=name, bg=INK, fg=INK_SOFT, font=("Microsoft YaHei UI", 10))
            label.pack(side="left")
            self.step_badges.append((badge, label))

    def _card(self, parent: tk.Misc, title: str) -> tuple[tk.Frame, tk.Frame]:
        shell = tk.Frame(parent, bg=LINE, padx=1, pady=1)
        inner = tk.Frame(shell, bg=CARD, padx=14, pady=12)
        inner.pack(fill="both", expand=True)
        tk.Label(inner, text=title, bg=CARD, fg=TEAL, font=("Microsoft YaHei UI", 9, "bold")).pack(anchor="w")
        return shell, inner

    def _field_label(self, parent: tk.Misc, text: str, row: int, column: int) -> None:
        tk.Label(parent, text=text, bg=CARD, fg=TEXT, font=("Microsoft YaHei UI", 10)).grid(row=row, column=column, sticky="w", padx=(0, 8), pady=4)

    def _set_window_detail(self, text: str, url: str = "") -> None:
        summary = text.split("\n", 1)[0]
        self.window_info.configure(text=summary)
        self.window_url.configure(state="normal")
        self.window_url_var.set(url)
        self.window_url.configure(state="readonly")

    def _fit_wraps(self, _event: tk.Event | None = None) -> None:
        width = max(self.winfo_width() - 80, 320)
        self.window_info.configure(wraplength=width)
        self.question_status.configure(wraplength=max(int(width * 0.58), 280))

    def _paint_status(self, text: str, tone: str) -> None:
        if tone == "ok":
            bg, fg, mark = OK_BG, OK_FG, TEAL
        else:
            bg, fg, mark = WARN_BG, WARN_FG, BRASS
        self.status_strip.configure(bg=bg)
        self.status_mark.configure(bg=mark)
        self.question_status.configure(text=text, bg=bg, fg=fg)

    def _refresh_steps(self) -> None:
        mismatched = bool(
            self.questions_current
            and self.detected_work_id
            and self.question_work_id
            and self.detected_work_id != self.question_work_id
        )
        has_answers = any(record.get("answer") for record in self.records)
        if not self.windows:
            current = 0
        elif not self.questions_current or mismatched:
            current = 1
        elif not has_answers:
            current = 2
        else:
            current = 3
        for index, (badge, label) in enumerate(self.step_badges):
            active = index == current
            done = index < current
            if active:
                badge.configure(bg=BRASS, fg=INK)
                label.configure(fg="#f7f1de", font=("Microsoft YaHei UI", 10, "bold"))
            elif done:
                badge.configure(bg=TEAL, fg="#f4f7f8")
                label.configure(fg="#d5e2ec", font=("Microsoft YaHei UI", 10))
            else:
                badge.configure(bg="#2a4662", fg=INK_SOFT)
                label.configure(fg=INK_SOFT, font=("Microsoft YaHei UI", 10))

    def _load_config_into_form(self) -> None:
        store = load_store()
        self._profiles = store["providers"]
        self._active_provider = store["provider"]
        self.provider_var.set(self._active_provider)
        self._fill_profile(self._profiles[self._active_provider])

    def _fill_profile(self, profile: dict) -> None:
        self.base_url.delete(0, "end")
        self.base_url.insert(0, profile.get("base_url") or "")
        self.model.delete(0, "end")
        self.model.insert(0, profile.get("model") or "")
        self.api_key.delete(0, "end")
        self.api_key.insert(0, profile.get("api_key") or "")

    def _remember_profile(self) -> None:
        if not self._active_provider:
            return
        profile = self.current_config()
        preset = PROVIDERS.get(self._active_provider)
        if preset and not profile["base_url"]:
            profile["base_url"] = preset["base_url"]
        self._profiles[self._active_provider] = profile

    def _on_provider(self, _event=None) -> None:
        name = self.provider_var.get()
        if not name or name == self._active_provider:
            return
        self._remember_profile()
        self._active_provider = name
        profile = dict(self._profiles.get(name) or {})
        preset = PROVIDERS.get(name)
        if preset and not profile.get("base_url"):
            profile["base_url"] = preset["base_url"]
        self._profiles[name] = profile
        self._fill_profile(profile)
        save_store(self._active_provider, self._profiles)
        self.write_log(f"已切换到{name}")

    def current_config(self) -> dict:
        return {
            "api_key": self.api_key.get().strip(),
            "base_url": self.base_url.get().strip(),
            "model": self.model.get().strip(),
        }

    def save_settings(self) -> None:
        config = self.current_config()
        if not config["api_key"] or not config["base_url"] or not config["model"]:
            messagebox.showwarning("配置不完整", "请填写接口地址、模型和 API Key")
            return
        self._remember_profile()
        save_store(self._active_provider, self._profiles)
        self.write_log(f"已保存{self._active_provider}的配置")

    def refresh_windows(self) -> None:
        try:
            self.windows = list_homework_windows()
        except Exception as exc:  # noqa: BLE001 - show discovery errors in the log
            self.windows = []
            self.write_log(f"刷新窗口失败：{exc}")
        labels = [f"{item['browser']}    {item['title']}" for item in self.windows]
        self.window_box["values"] = labels
        if labels:
            self.window_box.current(0)
            self.write_log(f"找到 {len(labels)} 个作业窗口")
            self.inspect_selected()
        else:
            self.window_var.set("")
            self._set_window_detail("没有找到 Chrome 或 Edge 中的「作业作答」窗口")
            self.write_log("没有找到 Chrome 或 Edge 中的「作业作答」窗口")
        self._refresh_steps()

    def selected_window(self) -> dict | None:
        index = self.window_box.current()
        if index < 0 or index >= len(self.windows):
            return None
        return self.windows[index]

    def selected_title(self) -> str | None:
        item = self.selected_window()
        return item["title"] if item else None

    def inspect_selected(self) -> None:
        item = self.selected_window()
        if not item:
            self._set_window_detail("未选中作业窗口")
            return
        self._inspect_token += 1
        token = self._inspect_token
        self._set_window_detail("正在检测选中窗口…")

        def work() -> None:
            try:
                info = inspect_homework_window(item["handle"])
            except Exception as exc:  # noqa: BLE001
                info = {"ok": False, "message": str(exc)}
            self.events.put(("inspect", (token, info)))

        threading.Thread(target=work, daemon=True).start()

    def parse_only(self) -> set[int] | None:
        text = self.only_var.get().strip()
        if not text:
            return None
        numbers: set[int] = set()
        for part in text.replace("，", " ").replace(",", " ").split():
            if "-" in part:
                start, end = part.split("-", 1)
                numbers.update(range(int(start), int(end) + 1))
            else:
                numbers.add(int(part))
        return numbers

    def read_clipboard(self) -> None:
        try:
            text = self.clipboard_get()
        except tk.TclError:
            messagebox.showwarning("剪贴板为空", "请先在作业页面全选并复制题目")
            return
        PAGE_TEXT.write_text(text, encoding="utf-8")
        try:
            questions, skipped = parse_questions(load_page_text(PAGE_TEXT))
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("题目解析失败", str(exc))
            return
        self.records = [
            {"no": item["no"], "type": item["type"], "answer": "", "stem": item["stem"], "options": item["options"]}
            for item in questions
        ]
        self._fill_tree()
        self.questions_current = True
        self.question_work_id = self.detected_work_id
        self.question_name = self.detected_name or "剪贴板"
        self._refresh_question_banner()
        self.write_log(f"已从剪贴板解析 {len(questions)} 题，保存在 _page_text.txt")
        note = describe_skipped(skipped)
        if note:
            self.write_log(note)

    def _load_saved_answers(self) -> None:
        if not ANSWERS_PATH.exists():
            return
        import json

        self.records = json.loads(ANSWERS_PATH.read_text(encoding="utf-8"))
        self._fill_tree()

    def _fill_tree(self) -> None:
        self.tree.delete(*self.tree.get_children())
        for index, record in enumerate(self.records):
            stem = " ".join((record.get("stem") or "").split())
            if record.get("images"):
                stem = f"［图］{stem}"
            if not self.questions_current:
                tag = "stale-alt" if index % 2 else "stale"
            elif index % 2:
                tag = "alt"
            else:
                tag = ""
            self.tree.insert(
                "",
                "end",
                tags=(tag,) if tag else (),
                values=(record["no"], TYPE_LABEL.get(record["type"], record["type"]), show_answer(record.get("answer", "")), stem[:80]),
            )

    def update_questions(self) -> None:
        item = self.selected_window()
        if not item:
            messagebox.showwarning("没有作业窗口", "请先打开作业页，再刷新并选中窗口")
            return

        def work() -> None:
            info = inspect_homework_window(item["handle"], flash=False)
            if not info.get("ok"):
                raise SystemExit(info.get("message") or "没有读到当前窗口")
            text = copy_page_text(item["handle"])
            PAGE_TEXT.write_text(text, encoding="utf-8")
            questions, skipped = parse_questions(load_page_text(PAGE_TEXT))
            try:
                images = save_question_images(item["handle"], questions)
            except Exception as exc:  # noqa: BLE001
                images = {}
                self.events.put(("log", f"图片截取失败：{exc}"))
            self.records = [
                {
                    "no": question["no"],
                    "type": question["type"],
                    "answer": "",
                    "stem": question["stem"],
                    "options": question["options"],
                    "images": images.get(question["no"]) or [],
                }
                for question in questions
            ]
            self.questions_current = True
            self.question_work_id = info.get("work_id") or ""
            self.question_name = info.get("name") or ""
            self.detected_work_id = self.question_work_id
            self.detected_name = self.question_name
            self.events.put(("tree", None))
            self.events.put(("banner", None))
            self.events.put(("log", f"已更新题目：{self.question_name or '当前作业'}，共 {len(questions)} 题"))
            pictured = [number for number, paths in images.items() if paths]
            if pictured:
                numbers = "、".join(str(number) for number in pictured)
                self.events.put(("log", f"第 {numbers} 题含图片，生成答案时会把截图发给模型"))
            note = describe_skipped(skipped)
            if note:
                self.events.put(("log", note))

        self._start("更新题目", work)

    def _refresh_question_banner(self) -> None:
        mismatched = bool(
            self.questions_current
            and self.detected_work_id
            and self.question_work_id
            and self.detected_work_id != self.question_work_id
        )
        if not self.records:
            text = "还没有题目。选中作业窗口后，点「更新题目」。"
        elif not self.questions_current:
            text = "列表里是上次保存的题目。要换成当前页面的题，再点「更新题目」。"
        elif mismatched:
            current = self.detected_name or self.detected_work_id
            text = f"当前窗口是「{current}」，列表仍是另一份作业。请重新点「更新题目」。"
        else:
            text = f"当前题目：{self.question_name or '已从当前窗口更新'} · {len(self.records)} 题"
            if self.question_work_id:
                text += f" · workId {self.question_work_id}"
            self._paint_status(text, "ok")
            self._refresh_steps()
            return
        self._paint_status(text, "warn")
        self._refresh_steps()

    def _questions_match_window(self) -> bool:
        if not self.questions_current:
            return False
        if self.detected_work_id and self.question_work_id and self.detected_work_id != self.question_work_id:
            return False
        return True

    def generate(self) -> None:
        if not self._questions_match_window():
            messagebox.showwarning("题目不是当前页面", "请先选中作业窗口并点「更新题目」。生成答案会使用更新后的题目，不会沿用上次保存的旧题。")
            return
        if not PAGE_TEXT.exists():
            messagebox.showwarning("还没有题目", "请先点「更新题目」")
            return
        config = self.current_config()
        if not config["api_key"]:
            messagebox.showwarning("缺少 API Key", "请先填写并保存 API Key")
            return

        def work() -> None:
            questions, skipped = parse_questions(load_page_text(PAGE_TEXT))
            note = describe_skipped(skipped)
            if note:
                self.events.put(("log", note))
            solve_questions(
                questions,
                config,
                log=lambda message: self.events.put(("log", message)),
                on_update=lambda records: self.events.put(("records", records)),
            )
            self.events.put(("log", f"答案已写入 {ANSWERS_PATH}"))

        self._start("生成答案", work)

    def write_page(self) -> None:
        if not self.records or not any(record.get("answer") for record in self.records):
            messagebox.showwarning("还没有答案", "请先生成答案")
            return
        item = self.selected_window()
        if not item:
            messagebox.showwarning("没有作业窗口", "请打开作业页后刷新窗口")
            return
        try:
            only = self.parse_only()
        except ValueError:
            messagebox.showwarning("题号格式不正确", "示例：71-80 或 96 97")
            return
        records = self.records

        def work() -> None:
            apply_answers(
                records,
                only,
                window_title=item["title"],
                window_handle=item["handle"],
                log=lambda message: self.events.put(("log", message)),
            )
            self.events.put(("log", "写入结束。没有点击提交。"))

        self._start("写入页面", work)

    def verify(self) -> None:
        if not self.records:
            messagebox.showwarning("还没有答案", "请先生成答案")
            return
        item = self.selected_window()
        if not item:
            messagebox.showwarning("没有作业窗口", "请打开作业页后刷新窗口")
            return
        try:
            only = self.parse_only()
        except ValueError:
            messagebox.showwarning("题号格式不正确", "留空表示全部题目。也可填写 71-80 或 96 97")
            return
        records = [record for record in self.records if not only or record["no"] in only]
        scope = "全部题目" if not only else "指定题号"

        def work() -> None:
            choice_count = sum(1 for record in records if record["type"] not in {"blank", "essay"})
            blank_count = sum(1 for record in records if record["type"] == "blank")
            essay_count = sum(1 for record in records if record["type"] == "essay")
            mismatches = verify_answers(records, window_title=item["title"], window_handle=item["handle"], only=only)
            if blank_count:
                self.events.put(("log", f"填空题 {blank_count} 题没有核对：页面上读不到已经填入的内容"))
            if essay_count:
                self.events.put(("log", f"简答题 {essay_count} 题没有核对：编辑区读不到已经写入的文字"))
            if not mismatches:
                self.events.put(("log", f"已核对{scope}里的选择题 {choice_count} 题，与答案一致"))
                return
            self.events.put(("log", f"已核对{scope}里的选择题 {choice_count} 题，有 {len(mismatches)} 题不一致"))
            for mismatch in mismatches:
                self.events.put(("log", f"第 {mismatch['no']} 题 期望 {mismatch['expected']} 实际 {mismatch['actual']}"))

        self._start("核对", work)

    def _start(self, name: str, work) -> None:
        if self.worker and self.worker.is_alive():
            self.write_log("上一项操作还在进行")
            return
        self._set_busy(True)
        self.footer_text.configure(text=f"正在{name}…")
        self.write_log(f"开始{name}")

        def target() -> None:
            try:
                work()
            except Exception as exc:  # noqa: BLE001
                self.events.put(("log", f"{name}失败：{exc}"))
            finally:
                self.events.put(("idle", ""))

        self.worker = threading.Thread(target=target, daemon=True)
        self.worker.start()

    def _set_busy(self, busy: bool) -> None:
        state = "disabled" if busy else "normal"
        for button in self.buttons:
            button.configure(state=state)
        self.refresh_button.configure(state=state)
        self.detect_button.configure(state=state)
        self.save_button.configure(state=state)

    def write_log(self, message: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", message + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _poll(self) -> None:
        while True:
            try:
                kind, payload = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == "log":
                self.write_log(str(payload))
            elif kind == "tree":
                self._fill_tree()
            elif kind == "records":
                self.records = payload
                self._fill_tree()
                self._refresh_question_banner()
                for child in reversed(self.tree.get_children()):
                    if self.tree.set(child, "answer"):
                        self.tree.see(child)
                        break
            elif kind == "banner":
                self._refresh_question_banner()
            elif kind == "inspect":
                token, info = payload
                if token == self._inspect_token:
                    text = describe_inspection(info)
                    self._set_window_detail(text, info.get("url") or "")
                    self.write_log(text.replace("\n", " | "))
                    if info.get("ok"):
                        self.detected_work_id = info.get("work_id") or ""
                        self.detected_name = info.get("name") or ""
                        self._refresh_question_banner()
            elif kind == "idle":
                self._set_busy(False)
                self.footer_text.configure(text="就绪")
        self.after(120, self._poll)


def _enable_dpi() -> None:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            return


def launch() -> None:
    _enable_dpi()
    app = App()
    app.mainloop()
