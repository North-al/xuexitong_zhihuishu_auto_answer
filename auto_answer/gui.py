"""超星学习通与智慧树作业测验的桌面操作窗口。"""

from __future__ import annotations

import ctypes
import json
import queue
import threading
import tkinter as tk
from tkinter import messagebox, ttk

from auto_answer.browser import (
    copy_page_text,
    describe_inspection,
    detect_platform,
    inspect_homework_window,
    list_homework_windows,
)
from auto_answer.config import PROVIDER_NAMES, PROVIDERS, icon_file, load_store, save_store
from auto_answer.homework import (
    ANSWERS_PATH,
    PAGE_TEXT,
    apply_answers,
    load_page_text,
    describe_skipped,
    parse_questions,
    regenerate_one,
    review_answers,
    save_question_images,
    solve_questions,
    verify_answers,
)
from auto_answer import zhihuishu

TYPE_LABEL = {"single": "单选", "multi": "多选", "judge": "判断", "blank": "填空", "essay": "简答"}

# 参考 BriefForge：浅色工作区 + 左侧导航 + 白卡片，偏天空蓝而不是模板紫。
BG = "#EEF4FA"
SIDEBAR = "#FFFFFF"
SIDEBAR_LINE = "#E2EAF3"
SIDEBAR_ACTIVE = "#E8F1FF"
ACCENT = "#3B82F6"
ACCENT_DEEP = "#2563EB"
CARD = "#FFFFFF"
LINE = "#D7E3EF"
TEXT = "#0F172A"
MUTED = "#64748B"
INK = "#0B3A6E"
WARN_BG = "#FFF7ED"
WARN_FG = "#9A3412"
OK_BG = "#ECFDF5"
OK_FG = "#047857"
LOG_BG = "#0F172A"
LOG_FG = "#E2E8F0"
APP_NAME = "作业答题 · 超星 / 智慧树"
APP_TAGLINE = "学习通作业 + 智慧树测验"

NAV_ITEMS = (
    ("work", "作答"),
    ("settings", "AI 设置"),
    ("help", "使用说明"),
)

HELP_TEXT = """作业答题 · 超星 / 智慧树 · 使用说明

本程序只在本地辅助作答，不会点击「提交 / 交卷 / 暂时保存 / 提交作业 / 暂存作业」。

支持平台：
· 超星学习通「作业作答」（可一页多题）
· 智慧树「作业考试 / 单元测试」（一题一页；选项常随机排序，按选项正文匹配写入）


一、开始前请准备好

1. 用 Chrome 或 Edge 打开超星「作业作答」或智慧树「作业考试」页，并保持该标签在前台。
2. 打开本程序，点左侧「AI 设置」。
3. 在「答题服务」里填写：接口地址、模型名、API Key，然后点「保存配置」。
   · 模型名由你自己填，不限于官方名字（例如 deepseek-flash、gpt-4o-mini、自建名均可）。
   · 只要接口返回值符合 OpenAI 的 chat/completions 格式即可。
4. 如果要用「AI验证」，再选一个「验证服务」，把该服务的地址 / 模型 / Key 也保存好。
   · 验证服务同样不限模型，任意 OpenAI 兼容接口都可以。
   · 建议验证服务与答题服务用不同配置，便于交叉核对。


二、日常怎么用（按这个顺序）

1. 刷新窗口 → 在下拉里选中对应作答窗口（会标注「超星」或「智慧树」）。
   可点「检测窗口」，确认作业名称和题量。
2. 更新题目
   从当前页面读取题目到左侧列表。智慧树会自动逐题翻页收集，不会提交。
3. 生成答案
   用「答题服务」逐题作答，答案会出现在列表的「答案」列。
4. （可选）AI验证
   用「验证服务」再独立答一遍。两边不一致时，会弹出窗口让你逐题选择保留哪边。
5. （可选）单题重做
   在列表里选中某题，点「重新生成本题」，或直接双击该行。
6. 写入页面
   把列表里的答案写回浏览器。超星直接勾选；智慧树按选项正文点选，并会点「下一题 / 保存」；都不会点提交。
7. （可选）核对
   检查选择题是否与列表一致。填空、简答目前还不能自动核对。


三、题号怎么填

工具栏右侧「题号」：
· 留空 = 处理全部题目
· 写 71-80 = 只处理第 71 到 80 题
· 写 21 23 = 只处理第 21、23 题

「更新题目 / 生成答案 / AI验证 / 写入页面 / 核对」都会遵守这个范围。


四、支持哪些题型

· 单选题、多选题、判断题
· 填空题（包括一题多空）——超星可写入；智慧树暂不支持填空/简答写入
· 简答题——同上
· 带图片的题目：更新题目时会截图；生成答案时发给支持看图的模型

暂不支持：编程题、需要上传图片作答的题。


五、容易踩坑的地方

· 请用项目虚拟环境，或打包好的 exe 运行。系统自带的 Python 可能缺识别库。
· 写过答案后再点「更新题目」也没关系：页面上的「我的答案」会被自动去掉，不会带给模型。
· 打回重做或智慧树测验，选项字母可能被打乱。程序会按选项文字匹配，不以页面上的 A/B/C 为准。
· 填空若禁止粘贴，会改成逐字输入；写入前会先清掉旧内容。
· 日志若出现 miss / 写入未生效 / skip-unknown-state，说明这一题页面结果与期望不一致，可选中后单独再写一次。
· 最终是否交卷，由你在浏览器页面上自行决定。
"""


def show_answer(answer) -> str:
    if isinstance(answer, list):
        return " / ".join(str(part) for part in answer)
    return str(answer or "")


class App(tk.Tk):
    def __init__(self) -> None:
        _enable_dpi()
        super().__init__()
        self.title(APP_NAME)
        icon = icon_file()
        if icon.exists():
            self.iconbitmap(str(icon))
        self.geometry("1280x800")
        self.minsize(1080, 680)
        self.configure(bg=BG)
        self.events: queue.Queue = queue.Queue()
        self.worker: threading.Thread | None = None
        self.windows: list[dict] = []
        self.records: list[dict] = []
        self._inspect_token = 0
        self._refresh_token = 0
        self._refreshing = False
        self.questions_current = False
        self.question_work_id = ""
        self.question_name = ""
        self.question_platform = "chaoxing"
        self.detected_work_id = ""
        self.detected_name = ""
        self._profiles: dict = {}
        self._active_provider = ""
        self._verify_provider = "DeepSeek"
        self._page = "work"
        self._nav_buttons: dict[str, tk.Label] = {}
        self._pages: dict[str, tk.Frame] = {}
        self._build_style()
        self._build()
        self._load_config()
        self._load_saved_answers()
        self._refresh_question_banner()
        self.after(120, self._poll)
        # 启动后再后台刷新，避免构造界面时卡死
        self.after(200, self.refresh_windows)

    def _build_style(self) -> None:
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure(".", font=("Microsoft YaHei UI", 10), background=BG)
        style.configure("TFrame", background=BG)
        style.configure("TLabel", background=BG, foreground=TEXT)
        style.configure("TEntry", fieldbackground=CARD, bordercolor=LINE, lightcolor=LINE, darkcolor=LINE, padding=7)
        style.configure("TCombobox", fieldbackground=CARD, padding=7, arrowsize=14)
        style.map("TCombobox", fieldbackground=[("readonly", CARD)])
        style.configure(
            "TButton",
            padding=(14, 8),
            background="#FFFFFF",
            foreground="#475569",
            bordercolor=LINE,
            lightcolor=LINE,
            darkcolor=LINE,
            borderwidth=1,
            relief="solid",
            focuscolor=LINE,
        )
        style.map(
            "TButton",
            background=[("active", "#F8FBFF"), ("pressed", "#F8FBFF"), ("disabled", "#F8FAFC")],
            foreground=[("active", ACCENT), ("pressed", ACCENT), ("disabled", "#CBD5E1")],
            bordercolor=[("active", "#BFDBFE"), ("pressed", "#BFDBFE"), ("disabled", "#E2E8F0")],
        )
        style.configure(
            "Primary.TButton",
            padding=(16, 8),
            background=ACCENT,
            foreground="#FFFFFF",
            bordercolor=ACCENT,
            lightcolor=ACCENT,
            darkcolor=ACCENT,
            borderwidth=1,
            relief="solid",
            focuscolor=ACCENT,
        )
        style.map(
            "Primary.TButton",
            background=[("active", ACCENT_DEEP), ("pressed", ACCENT_DEEP), ("disabled", "#93C5FD")],
            foreground=[("disabled", "#FFFFFF")],
            bordercolor=[("active", ACCENT_DEEP), ("pressed", ACCENT_DEEP), ("disabled", "#93C5FD")],
        )
        style.configure(
            "Plain.TButton",
            padding=(14, 8),
            background="#EFF6FF",
            foreground=ACCENT,
            bordercolor="#BFDBFE",
            lightcolor="#BFDBFE",
            darkcolor="#BFDBFE",
            borderwidth=1,
            relief="solid",
        )
        style.map(
            "Plain.TButton",
            background=[("active", ACCENT), ("pressed", ACCENT_DEEP), ("disabled", "#F1F5F9")],
            foreground=[("active", "#FFFFFF"), ("pressed", "#FFFFFF"), ("disabled", "#94A3B8")],
            bordercolor=[("active", ACCENT), ("pressed", ACCENT_DEEP), ("disabled", "#E2E8F0")],
        )
        style.configure("Treeview", rowheight=32, font=("Microsoft YaHei UI", 10), background=CARD, fieldbackground=CARD, foreground=TEXT, borderwidth=0)
        style.configure("Treeview.Heading", font=("Microsoft YaHei UI", 10, "bold"), background="#F8FBFF", foreground="#334155", relief="flat")
        style.map("Treeview", background=[("selected", "#DBEAFE")], foreground=[("selected", "#1E3A8A")])
        style.configure("Vertical.TScrollbar", background=LINE, troughcolor="#F8FBFF", borderwidth=0, arrowsize=12)
        style.configure("TPanedwindow", background=BG)

    def _build(self) -> None:
        shell = tk.Frame(self, bg=BG)
        shell.pack(fill="both", expand=True)
        self._build_sidebar(shell)
        right = tk.Frame(shell, bg=BG)
        right.pack(side="left", fill="both", expand=True)
        self._build_topbar(right)
        self.content = tk.Frame(right, bg=BG)
        self.content.pack(fill="both", expand=True, padx=18, pady=16)
        self._pages["work"] = self._build_work_page(self.content)
        self._pages["settings"] = self._build_settings_page(self.content)
        self._pages["help"] = self._build_help_page(self.content)
        footer = tk.Frame(right, bg=SIDEBAR, highlightbackground=SIDEBAR_LINE, highlightthickness=1)
        footer.pack(fill="x", side="bottom")
        self.footer_text = tk.Label(
            footer,
            text="就绪",
            bg=SIDEBAR,
            fg=MUTED,
            anchor="w",
            font=("Microsoft YaHei UI", 9),
            padx=18,
            pady=8,
        )
        self.footer_text.pack(fill="x")
        self._show_page("work")
        self.bind("<Configure>", self._fit_wraps, add="+")

    def _build_sidebar(self, parent: tk.Misc) -> None:
        side = tk.Frame(parent, bg=SIDEBAR, width=208, highlightbackground=SIDEBAR_LINE, highlightthickness=1)
        side.pack(side="left", fill="y")
        side.pack_propagate(False)
        brand = tk.Frame(side, bg=SIDEBAR)
        brand.pack(fill="x", padx=18, pady=22)
        tk.Label(brand, text="作业答题", bg=SIDEBAR, fg=INK, font=("Microsoft YaHei UI", 14, "bold"), anchor="w").pack(fill="x")
        tk.Label(brand, text="超星 · 智慧树", bg=SIDEBAR, fg=ACCENT_DEEP, font=("Microsoft YaHei UI", 10, "bold"), anchor="w").pack(fill="x", pady=(2, 0))
        tk.Label(brand, text=APP_TAGLINE, bg=SIDEBAR, fg=MUTED, font=("Microsoft YaHei UI", 8), anchor="w").pack(fill="x", pady=(4, 0))
        tk.Label(side, text="工作区", bg=SIDEBAR, fg=MUTED, font=("Microsoft YaHei UI", 9), anchor="w").pack(fill="x", padx=18, pady=(8, 6))
        for key, title in NAV_ITEMS:
            item = tk.Label(
                side,
                text=f"  {title}",
                bg=SIDEBAR,
                fg=TEXT,
                anchor="w",
                font=("Microsoft YaHei UI", 11),
                padx=14,
                pady=12,
                cursor="hand2",
            )
            item.pack(fill="x", padx=10, pady=2)
            item.bind("<Button-1>", lambda _e, page=key: self._show_page(page))
            self._nav_buttons[key] = item

    def _build_topbar(self, parent: tk.Misc) -> None:
        bar = tk.Frame(parent, bg=SIDEBAR, highlightbackground=SIDEBAR_LINE, highlightthickness=1)
        bar.pack(fill="x")
        inner = tk.Frame(bar, bg=SIDEBAR)
        inner.pack(fill="x", padx=18, pady=14)
        left = tk.Frame(inner, bg=SIDEBAR)
        left.pack(side="left", fill="x", expand=True)
        self.page_title = tk.Label(left, text="作答", bg=SIDEBAR, fg=TEXT, font=("Microsoft YaHei UI", 16, "bold"), anchor="w")
        self.page_title.pack(anchor="w")
        self.page_subtitle = tk.Label(
            left,
            text="超星作业 / 智慧树测验 · 选窗口 → 更新题目 → 生成答案 → 写入页面",
            bg=SIDEBAR,
            fg=MUTED,
            font=("Microsoft YaHei UI", 9),
            anchor="w",
        )
        self.page_subtitle.pack(anchor="w", pady=2)

    def _show_page(self, page: str) -> None:
        self._page = page
        titles = {
            "work": ("作答", "超星作业 / 智慧树测验 · 选窗口 → 更新题目 → 生成答案 → 写入页面"),
            "settings": ("AI 设置", "答题 / 验证分开保存；模型名自定义，兼容 OpenAI 返回格式即可"),
            "help": ("使用说明", "超星与智慧树通用流程；程序不会点击提交"),
        }
        self.page_title.configure(text=titles[page][0])
        self.page_subtitle.configure(text=titles[page][1])
        for key, button in self._nav_buttons.items():
            active = key == page
            button.configure(bg=SIDEBAR_ACTIVE if active else SIDEBAR, fg=ACCENT_DEEP if active else TEXT, font=("Microsoft YaHei UI", 11, "bold" if active else "normal"))
        for key, frame in self._pages.items():
            if key == page:
                frame.pack(fill="both", expand=True)
            else:
                frame.pack_forget()
        if page == "settings":
            self._fill_settings_form()

    def _card(self, parent: tk.Misc, title: str) -> tuple[tk.Frame, tk.Frame]:
        shell = tk.Frame(parent, bg=LINE, padx=1, pady=1)
        inner = tk.Frame(shell, bg=CARD, padx=16, pady=14)
        inner.pack(fill="both", expand=True)
        tk.Label(inner, text=title, bg=CARD, fg=ACCENT_DEEP, font=("Microsoft YaHei UI", 9, "bold")).pack(anchor="w")
        return shell, inner

    def _field_label(self, parent: tk.Misc, text: str, row: int, column: int) -> None:
        tk.Label(parent, text=text, bg=CARD, fg=TEXT, font=("Microsoft YaHei UI", 10)).grid(row=row, column=column, sticky="w", padx=(0, 8), pady=5)

    def _build_work_page(self, parent: tk.Misc) -> tk.Frame:
        page = tk.Frame(parent, bg=BG)
        browser_shell, browser = self._card(page, "作答窗口")
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
            text="同时支持超星「作业作答」与智慧树「作业考试 / 单元测试」。选中后会显示平台、名称、题量和地址。",
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

        ai_shell, ai = self._card(page, "当前 AI")
        ai_shell.pack(fill="x", pady=(12, 0))
        ai_row = tk.Frame(ai, bg=CARD)
        ai_row.pack(fill="x", pady=(8, 0))
        self.ai_summary = tk.Label(ai_row, text="尚未配置 AI", bg=CARD, fg=TEXT, justify="left", anchor="w", font=("Microsoft YaHei UI", 10))
        self.ai_summary.pack(side="left", fill="x", expand=True)
        ttk.Button(ai_row, text="去设置", style="Plain.TButton", command=lambda: self._show_page("settings")).pack(side="right")

        actions = tk.Frame(page, bg=BG)
        actions.pack(fill="x", pady=(12, 10))
        self.buttons = [
            ttk.Button(actions, text="更新题目", style="Primary.TButton", command=self.update_questions),
            ttk.Button(actions, text="生成答案", style="Primary.TButton", command=self.generate),
            ttk.Button(actions, text="AI验证", style="Primary.TButton", command=self.ai_review),
            ttk.Button(actions, text="重新生成本题", style="Primary.TButton", command=self.regenerate_selected),
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

        body = ttk.Panedwindow(page, orient="horizontal")
        body.pack(fill="both", expand=True)
        left = tk.Frame(body, bg=BG)
        right = tk.Frame(body, bg=LINE, padx=1, pady=1)
        body.add(left, weight=3)
        body.add(right, weight=2)

        self.status_strip = tk.Frame(left, bg=WARN_BG)
        self.status_mark = tk.Frame(self.status_strip, bg="#F59E0B", width=4)
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
        self.tree.tag_configure("alt", background="#F8FBFF")
        self.tree.tag_configure("stale", foreground="#94A3B8")
        self.tree.tag_configure("stale-alt", background="#F8FBFF", foreground="#94A3B8")
        self.tree.tag_configure("diff", background="#FFF7ED")
        self.tree.tag_configure("diff-alt", background="#FFEDD5")
        scroll = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="left", fill="y")
        self.tree.bind("<Double-1>", lambda _event: self.regenerate_selected())

        log_panel = tk.Frame(right, bg=LOG_BG)
        log_panel.pack(fill="both", expand=True)
        tk.Label(log_panel, text="操作记录", bg=LOG_BG, fg="#94A3B8", anchor="w", font=("Microsoft YaHei UI", 9, "bold"), padx=12, pady=10).pack(fill="x")
        self.log = tk.Text(
            log_panel,
            wrap="word",
            bg=LOG_BG,
            fg=LOG_FG,
            insertbackground=LOG_FG,
            font=("Consolas", 10),
            relief="flat",
            padx=12,
            pady=4,
            highlightthickness=0,
        )
        self.log.pack(fill="both", expand=True)
        self.log.configure(state="disabled")
        return page

    def _build_settings_page(self, parent: tk.Misc) -> tk.Frame:
        page = tk.Frame(parent, bg=BG)
        shell, card = self._card(page, "答题服务")
        shell.pack(fill="x")
        form = tk.Frame(card, bg=CARD)
        form.pack(fill="x", pady=(8, 0))
        form.columnconfigure(1, weight=1)
        form.columnconfigure(3, weight=1)
        self._field_label(form, "服务", 0, 0)
        self.provider_var = tk.StringVar(value="自定义")
        self.provider = ttk.Combobox(form, textvariable=self.provider_var, values=PROVIDER_NAMES, state="readonly")
        self.provider.grid(row=0, column=1, sticky="ew", pady=5)
        self.provider.bind("<<ComboboxSelected>>", self._on_settings_provider)
        self._field_label(form, "模型", 0, 2)
        self.model = ttk.Entry(form)
        self.model.grid(row=0, column=3, sticky="ew", pady=5)
        self._field_label(form, "接口地址", 1, 0)
        self.base_url = ttk.Entry(form)
        self.base_url.grid(row=1, column=1, columnspan=3, sticky="ew", pady=5)
        self._field_label(form, "API Key", 2, 0)
        self.api_key = ttk.Entry(form, show="*")
        self.api_key.grid(row=2, column=1, columnspan=3, sticky="ew", pady=5)
        tk.Label(
            card,
            text="切换服务会带出该服务已保存的地址、密钥和模型。模型名任意填写；DeepSeek 地址可填 https://api.deepseek.com",
            bg=CARD,
            fg=MUTED,
            anchor="w",
            font=("Microsoft YaHei UI", 9),
            wraplength=860,
            justify="left",
        ).pack(fill="x", pady=(8, 0))

        shell2, card2 = self._card(page, "验证服务")
        shell2.pack(fill="x", pady=(12, 0))
        row = tk.Frame(card2, bg=CARD)
        row.pack(fill="x", pady=(8, 0))
        self._field_label(row, "服务", 0, 0)
        self.verify_provider_var = tk.StringVar(value="DeepSeek")
        self.verify_provider = ttk.Combobox(row, textvariable=self.verify_provider_var, values=PROVIDER_NAMES, state="readonly", width=18)
        self.verify_provider.grid(row=0, column=1, sticky="w", pady=5)
        tk.Label(
            card2,
            text="AI验证使用这里选中的服务已保存配置，不限 OpenAI 官方模型；自建名、第三方中转均可，只要返回 OpenAI 兼容的 chat/completions 即可。先切到该服务填好并保存，再改回答题服务。",
            bg=CARD,
            fg=MUTED,
            anchor="w",
            font=("Microsoft YaHei UI", 9),
            wraplength=860,
            justify="left",
        ).pack(fill="x", pady=(8, 0))

        actions = tk.Frame(page, bg=BG)
        actions.pack(fill="x", pady=16)
        self.save_button = ttk.Button(actions, text="保存配置", style="Primary.TButton", command=self.save_settings)
        self.save_button.pack(side="left")
        ttk.Button(actions, text="返回作答", style="Plain.TButton", command=lambda: self._show_page("work")).pack(side="left", padx=(8, 0))
        return page

    def _build_help_page(self, parent: tk.Misc) -> tk.Frame:
        page = tk.Frame(parent, bg=BG)
        shell, card = self._card(page, "操作指南")
        shell.pack(fill="both", expand=True)
        body = tk.Frame(card, bg=CARD)
        body.pack(fill="both", expand=True, pady=(8, 0))
        text = tk.Text(
            body,
            wrap="word",
            bg=CARD,
            fg=TEXT,
            font=("Microsoft YaHei UI", 13),
            relief="flat",
            padx=4,
            pady=4,
            highlightthickness=0,
            spacing1=4,
            spacing3=8,
        )
        scroll = ttk.Scrollbar(body, orient="vertical", command=text.yview)
        text.configure(yscrollcommand=scroll.set)
        text.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        text.insert("1.0", HELP_TEXT.strip() + "\n")
        text.configure(state="disabled")
        return page

    def _set_window_detail(self, text: str, url: str = "") -> None:
        summary = text.split("\n", 1)[0]
        self.window_info.configure(text=summary)
        self.window_url.configure(state="normal")
        self.window_url_var.set(url)
        self.window_url.configure(state="readonly")

    def _fit_wraps(self, _event: tk.Event | None = None) -> None:
        width = max(self.winfo_width() - 280, 320)
        if hasattr(self, "window_info"):
            self.window_info.configure(wraplength=width)
        if hasattr(self, "question_status"):
            self.question_status.configure(wraplength=max(int(width * 0.58), 280))
        if hasattr(self, "ai_summary"):
            self.ai_summary.configure(wraplength=max(width - 140, 240))

    def _paint_status(self, text: str, tone: str) -> None:
        if tone == "ok":
            bg, fg, mark = OK_BG, OK_FG, "#10B981"
        else:
            bg, fg, mark = WARN_BG, WARN_FG, "#F59E0B"
        self.status_strip.configure(bg=bg)
        self.status_mark.configure(bg=mark)
        self.question_status.configure(text=text, bg=bg, fg=fg)

    def _load_config(self) -> None:
        store = load_store()
        self._profiles = store["providers"]
        self._active_provider = store["provider"]
        self._verify_provider = store.get("verify_provider") or "DeepSeek"
        self.provider_var.set(self._active_provider)
        self.verify_provider_var.set(self._verify_provider)
        self._fill_settings_form()
        self._refresh_ai_summary()

    def _persist_store(self) -> None:
        save_store(self._active_provider, self._profiles, self._verify_provider)

    def _profile_config(self, name: str) -> dict:
        profile = dict(self._profiles.get(name) or {})
        preset = PROVIDERS.get(name)
        if preset and not profile.get("base_url"):
            profile["base_url"] = preset["base_url"]
        return {
            "api_key": str(profile.get("api_key") or "").strip(),
            "base_url": str(profile.get("base_url") or "").strip(),
            "model": str(profile.get("model") or "").strip(),
        }

    def current_config(self) -> dict:
        return self._profile_config(self._active_provider)

    def verify_config(self) -> dict:
        return self._profile_config(self._verify_provider)

    def _refresh_ai_summary(self) -> None:
        answer = self.current_config()
        review = self.verify_config()
        answer_model = answer["model"] or "未填模型"
        review_model = review["model"] or "未填模型"
        answer_ok = "已配置" if answer["api_key"] and answer["base_url"] and answer["model"] else "未完成"
        review_ok = "已配置" if review["api_key"] and review["base_url"] and review["model"] else "未完成"
        text = (
            f"答题：{self._active_provider} · {answer_model}（{answer_ok}）    "
            f"验证：{self._verify_provider} · {review_model}（{review_ok}）"
        )
        if hasattr(self, "ai_summary"):
            self.ai_summary.configure(text=text)

    def _settings_form(self) -> dict:
        return {
            "api_key": self.api_key.get().strip(),
            "base_url": self.base_url.get().strip(),
            "model": self.model.get().strip(),
        }

    def _fill_settings_form(self) -> None:
        profile = self._profiles.get(self._active_provider) or {}
        preset = PROVIDERS.get(self._active_provider)
        if preset and not profile.get("base_url"):
            profile = {**profile, "base_url": preset["base_url"]}
        self.provider_var.set(self._active_provider)
        self.verify_provider_var.set(self._verify_provider)
        self.base_url.delete(0, "end")
        self.base_url.insert(0, profile.get("base_url") or "")
        self.model.delete(0, "end")
        self.model.insert(0, profile.get("model") or "")
        self.api_key.delete(0, "end")
        self.api_key.insert(0, profile.get("api_key") or "")

    def _remember_settings_form(self) -> None:
        profile = self._settings_form()
        preset = PROVIDERS.get(self._active_provider)
        if preset and not profile["base_url"]:
            profile["base_url"] = preset["base_url"]
        self._profiles[self._active_provider] = profile

    def _on_settings_provider(self, _event=None) -> None:
        name = self.provider_var.get()
        if not name or name == self._active_provider:
            return
        self._remember_settings_form()
        self._active_provider = name
        self._fill_settings_form()

    def save_settings(self) -> None:
        self._remember_settings_form()
        config = self._profiles[self._active_provider]
        if not config.get("api_key") or not config.get("base_url") or not config.get("model"):
            messagebox.showwarning("配置不完整", "请填写当前答题服务的接口地址、模型和 API Key")
            return
        verify_name = self.verify_provider_var.get()
        if verify_name not in PROVIDER_NAMES:
            messagebox.showwarning("验证服务无效", "请选择有效的验证服务")
            return
        verify = self._profiles.get(verify_name) or {}
        if verify_name != self._active_provider and (
            not verify.get("api_key") or not verify.get("base_url") or not verify.get("model")
        ):
            if not messagebox.askyesno(
                "验证服务尚未配完",
                f"「{verify_name}」还没有完整的地址、模型或密钥。仍要保存吗？\n以后用 AI验证前请先把它配好。",
            ):
                return
        self._verify_provider = verify_name
        self._persist_store()
        self._refresh_ai_summary()
        self.write_log(f"已保存 AI 设置：答题 {self._active_provider}，验证 {self._verify_provider}")
        messagebox.showinfo("已保存", "AI 配置已保存")

    def open_ai_settings(self) -> None:
        self._show_page("settings")

    def open_help(self) -> None:
        self._show_page("help")

    def refresh_windows(self) -> None:
        if self._refreshing:
            self.write_log("正在刷新窗口，请稍候")
            return
        self._refreshing = True
        self._refresh_token += 1
        token = self._refresh_token
        self.refresh_button.configure(state="disabled")
        self.detect_button.configure(state="disabled")
        self._set_window_detail("正在刷新窗口列表…")
        self.write_log("开始刷新窗口")

        def work() -> None:
            try:
                windows = list_homework_windows()
                error = ""
            except Exception as exc:  # noqa: BLE001
                windows = []
                error = str(exc)
            self.events.put(("refresh", (token, windows, error)))

        threading.Thread(target=work, daemon=True).start()

    def _apply_refresh(self, token: int, windows: list[dict], error: str) -> None:
        if token != self._refresh_token:
            return
        self._refreshing = False
        if not (self.worker and self.worker.is_alive()):
            self.refresh_button.configure(state="normal")
            self.detect_button.configure(state="normal")
        if error:
            self.windows = []
            self.window_box["values"] = []
            self.window_var.set("")
            self._set_window_detail("刷新窗口失败")
            self.write_log(f"刷新窗口失败：{error}")
            return
        self.windows = windows
        labels = []
        for item in self.windows:
            plat = "智慧树" if item.get("platform") == "zhihuishu" else "超星"
            labels.append(f"{item['browser']} · {plat} · {item['title']}")
        self.window_box["values"] = labels
        if labels:
            self.window_box.current(0)
            self.write_log(f"找到 {len(labels)} 个作业窗口")
            self.inspect_selected()
        else:
            self.window_var.set("")
            self._set_window_detail("没有找到超星「作业作答」或智慧树「作业考试」窗口")
            self.write_log("没有找到超星「作业作答」或智慧树「作业考试」窗口")

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
        handle = item["handle"]
        self.detect_button.configure(state="disabled")
        self._set_window_detail("正在检测选中窗口…")

        def work() -> None:
            try:
                # 后台线程里初始化 COM，避免 UIA 偶发卡死
                try:
                    import comtypes

                    comtypes.CoInitialize()
                except Exception:
                    pass
                info = inspect_homework_window(handle)
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

    def _has_page_text(self) -> bool:
        if self.question_platform == "zhihuishu":
            return zhihuishu.PAGE_TEXT.exists() or PAGE_TEXT.exists()
        return PAGE_TEXT.exists()

    def _resolve_platform(self, item: dict | None = None) -> str:
        """按当前窗口决定平台，避免重启后仍走超星写入路径。"""
        item = item or self.selected_window()
        platform = ""
        if item:
            platform = str(item.get("platform") or "")
            if not platform:
                platform = detect_platform(item.get("title") or "")
        if not platform:
            platform = self.question_platform or "chaoxing"
        if platform not in {"chaoxing", "zhihuishu"}:
            platform = "chaoxing"
        self.question_platform = platform
        return platform

    def read_clipboard(self) -> None:
        try:
            text = self.clipboard_get()
        except tk.TclError:
            messagebox.showwarning("剪贴板为空", "请先在作业页面全选并复制题目")
            return
        PAGE_TEXT.write_text(text, encoding="utf-8")
        try:
            if "【单选题】" in text or "【多选题】" in text or "【判断题】" in text:
                self.question_platform = "zhihuishu"
                zhihuishu.PAGE_TEXT.write_text(text, encoding="utf-8")
                questions, skipped = zhihuishu.parse_questions(text)
            else:
                self.question_platform = "chaoxing"
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
        self.write_log(f"已从剪贴板解析 {len(questions)} 题（{self.question_platform}）")
        note = describe_skipped(skipped)
        if note:
            self.write_log(note)

    def _load_saved_answers(self) -> None:
        if not ANSWERS_PATH.exists():
            return
        self.records = json.loads(ANSWERS_PATH.read_text(encoding="utf-8"))
        # 重启后根据已保存题目文本恢复平台，避免写入误走超星
        for path in (zhihuishu.PAGE_TEXT, PAGE_TEXT):
            if not path.exists():
                continue
            try:
                sample = path.read_text(encoding="utf-8")[:800]
            except Exception:
                continue
            if "【单选题】" in sample or "【多选题】" in sample or "【判断题】" in sample:
                self.question_platform = "zhihuishu"
                break
        self._fill_tree()

    def _fill_tree(self, diff_nos: set[int] | None = None) -> None:
        self.tree.delete(*self.tree.get_children())
        for index, record in enumerate(self.records):
            stem = " ".join((record.get("stem") or "").split())
            if record.get("images"):
                stem = f"［图］{stem}"
            if not self.questions_current:
                tag = "stale-alt" if index % 2 else "stale"
            elif diff_nos and record["no"] in diff_nos:
                tag = "diff-alt" if index % 2 else "diff"
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

    def _parse_current_questions(self):
        if self.question_platform == "zhihuishu":
            path = zhihuishu.PAGE_TEXT if zhihuishu.PAGE_TEXT.exists() else PAGE_TEXT
            return zhihuishu.parse_questions(path.read_text(encoding="utf-8"))
        return parse_questions(load_page_text(PAGE_TEXT))

    def update_questions(self) -> None:
        item = self.selected_window()
        if not item:
            messagebox.showwarning("没有作业窗口", "请先打开作业页，再刷新并选中窗口")
            return

        def work() -> None:
            info = inspect_homework_window(item["handle"], flash=False)
            if not info.get("ok"):
                raise SystemExit(info.get("message") or "没有读到当前窗口")
            platform = info.get("platform") or item.get("platform") or "chaoxing"
            self.question_platform = platform
            text = copy_page_text(
                item["handle"],
                log=lambda message: self.events.put(("log", message)),
            )
            PAGE_TEXT.write_text(text, encoding="utf-8")
            if platform == "zhihuishu":
                zhihuishu.PAGE_TEXT.write_text(text, encoding="utf-8")
                questions, skipped = zhihuishu.parse_questions(text)
                images = {}
            else:
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
            plat_name = "智慧树" if platform == "zhihuishu" else "超星"
            self.events.put(
                ("log", f"已更新题目（{plat_name}）：{self.question_name or '当前作业'}，共 {len(questions)} 题")
            )
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
            return
        self._paint_status(text, "warn")

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
        if not self._has_page_text():
            messagebox.showwarning("还没有题目", "请先点「更新题目」")
            return
        config = self.current_config()
        if not config["api_key"]:
            messagebox.showwarning("缺少 API Key", "请先到「AI 设置」填写并保存 API Key")
            return

        def work() -> None:
            questions, skipped = self._parse_current_questions()
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

    def ai_review(self) -> None:
        if not self.records or not any(record.get("answer") for record in self.records):
            messagebox.showwarning("还没有答案", "请先生成答案，再点「AI验证」")
            return
        if not self._has_page_text():
            messagebox.showwarning("还没有题目", "请先点「更新题目」")
            return
        config = self.verify_config()
        provider = self._verify_provider
        if not config["api_key"] or not config["base_url"] or not config["model"]:
            messagebox.showwarning(
                "验证接口未配置",
                f"请先在「AI 设置」里为「{provider}」填写地址、模型和 API Key 并保存。",
            )
            return
        try:
            only = self.parse_only()
        except ValueError:
            messagebox.showwarning("题号格式不正确", "留空表示全部题目。也可填写 71-80 或 96 97")
            return
        if provider == self._active_provider:
            if not messagebox.askyesno(
                "验证服务与答题服务相同",
                "验证服务和当前答题服务是同一个配置，结果可能接近。仍要继续吗？",
            ):
                return
        original = [dict(record) for record in self.records if not only or record["no"] in only]
        if not original:
            messagebox.showwarning("没有可验证的题", "题号范围内没有题目")
            return

        def work() -> None:
            questions, skipped = self._parse_current_questions()
            note = describe_skipped(skipped)
            if note:
                self.events.put(("log", note))
            self.events.put(("log", f"使用验证服务 {provider} · {config['model']}"))
            _reviewed, diffs = review_answers(
                questions,
                original,
                config=config,
                log=lambda message: self.events.put(("log", message)),
            )
            self.events.put(("review", diffs))

        self._start("AI验证", work)

    def _show_review_diffs(self, diffs: list[dict]) -> None:
        if not diffs:
            self.write_log("AI验证完成：与现有答案没有分歧")
            messagebox.showinfo("AI验证", "验证服务给出的答案与当前答案一致")
            return
        numbers = "、".join(str(item["no"]) for item in diffs)
        self.write_log(f"AI验证发现 {len(diffs)} 题有分歧：第 {numbers} 题")
        choices = DiffChoiceDialog(self, diffs).result
        if choices is None:
            self.write_log("已取消分歧选用，当前答案未改动")
            return
        by_no = {record["no"]: record for record in self.records}
        changed = 0
        for item in diffs:
            pick = choices.get(item["no"], "original")
            record = by_no.get(item["no"])
            if record is None:
                continue
            if pick == "reviewed":
                record["answer"] = item["reviewed"]
                changed += 1
        ANSWERS_PATH.parent.mkdir(exist_ok=True)
        ANSWERS_PATH.write_text(json.dumps(self.records, ensure_ascii=False, indent=2), encoding="utf-8")
        self._fill_tree(diff_nos={item["no"] for item in diffs})
        self._refresh_question_banner()
        self.write_log(f"已确认分歧题选用，其中 {changed} 题改用验证答案")

    def _selected_record(self) -> dict | None:
        selected = self.tree.selection()
        if not selected:
            return None
        values = self.tree.item(selected[0], "values")
        if not values:
            return None
        try:
            number = int(values[0])
        except (TypeError, ValueError):
            return None
        return next((record for record in self.records if record["no"] == number), None)

    def regenerate_selected(self) -> None:
        record = self._selected_record()
        if record is None:
            messagebox.showwarning("未选中题目", "请先在列表里点选一道题，再点「重新生成本题」")
            return
        if not self._has_page_text():
            messagebox.showwarning("还没有题目", "请先点「更新题目」")
            return
        config = self.current_config()
        if not config["api_key"] or not config["base_url"] or not config["model"]:
            messagebox.showwarning("缺少配置", "请先到「AI 设置」填写并保存接口地址、模型和 API Key")
            return
        previous = record.get("answer")
        if previous in ("", None, []):
            if not messagebox.askyesno("本题还没有答案", "当前这题还没有答案，要直接生成吗？"):
                return
        elif not messagebox.askyesno(
            "重新生成本题",
            f"第 {record['no']} 题当前答案是 {show_answer(previous)}。\n将要求 AI 给出不同答案，是否继续？",
        ):
            return
        number = record["no"]

        def work() -> None:
            questions, skipped = self._parse_current_questions()
            note = describe_skipped(skipped)
            if note:
                self.events.put(("log", note))
            question = next((item for item in questions if item["no"] == number), None)
            if question is None:
                raise SystemExit(f"题目文本里没有第 {number} 题，请先更新题目")
            answer = regenerate_one(
                question,
                previous,
                config=config,
                log=lambda message: self.events.put(("log", message)),
            )
            self.events.put(("regen", {"no": number, "answer": answer, "previous": previous}))

        self._start(f"重新生成第{number}题", work)

    def _apply_regenerated(self, payload: dict) -> None:
        number = int(payload["no"])
        answer = payload.get("answer")
        for record in self.records:
            if record["no"] == number:
                record["answer"] = answer
                break
        else:
            self.write_log(f"列表里没有第 {number} 题，无法写入新答案")
            return
        ANSWERS_PATH.parent.mkdir(exist_ok=True)
        ANSWERS_PATH.write_text(json.dumps(self.records, ensure_ascii=False, indent=2), encoding="utf-8")
        self._fill_tree(diff_nos={number})
        self._refresh_question_banner()
        self.write_log(
            f"第 {number} 题已重新生成：{show_answer(payload.get('previous'))} -> {show_answer(answer)}"
        )
        for child in self.tree.get_children():
            if str(self.tree.set(child, "no")) == str(number):
                self.tree.selection_set(child)
                self.tree.see(child)
                break

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
        records = json.loads(json.dumps(self.records, ensure_ascii=False))
        platform = self._resolve_platform(item)

        def work() -> None:
            writer = zhihuishu.apply_answers if platform == "zhihuishu" else apply_answers
            self.events.put(("log", f"按「{'智慧树' if platform == 'zhihuishu' else '超星'}」方式写入"))
            writer(
                records,
                only,
                window_title=item["title"],
                window_handle=item["handle"],
                log=lambda message: self.events.put(("log", message)),
            )
            self.events.put(("log", "写入结束。没有点击提交。若出现 miss / 写入未生效，请单独重写该题。"))

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
        platform = self._resolve_platform(item)

        def work() -> None:
            choice_count = sum(1 for record in records if record["type"] not in {"blank", "essay"})
            blank_count = sum(1 for record in records if record["type"] == "blank")
            essay_count = sum(1 for record in records if record["type"] == "essay")
            checker = zhihuishu.verify_answers if platform == "zhihuishu" else verify_answers
            mismatches = checker(records, window_title=item["title"], window_handle=item["handle"], only=only)
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
            except SystemExit as exc:
                self.events.put(("log", f"{name}失败：{exc}"))
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
            elif kind == "review":
                self._show_review_diffs(payload or [])
            elif kind == "regen":
                self._apply_regenerated(payload or {})
            elif kind == "banner":
                self._refresh_question_banner()
            elif kind == "refresh":
                token, windows, error = payload
                self._apply_refresh(token, windows or [], error or "")
            elif kind == "inspect":
                token, info = payload
                if token == self._inspect_token:
                    text = describe_inspection(info)
                    self._set_window_detail(text, info.get("url") or "")
                    self.write_log(text.replace("\n", " | "))
                    if info.get("ok"):
                        self.detected_work_id = info.get("work_id") or ""
                        self.detected_name = info.get("name") or ""
                        item = self.selected_window()
                        if item is not None and info.get("platform"):
                            item["platform"] = info["platform"]
                        if info.get("platform"):
                            self.question_platform = info["platform"]
                        self._refresh_question_banner()
                    if not self._refreshing and not (self.worker and self.worker.is_alive()):
                        self.detect_button.configure(state="normal")
                        self.refresh_button.configure(state="normal")
            elif kind == "idle":
                self._set_busy(False)
                self.footer_text.configure(text="就绪")
        self.after(120, self._poll)


class DiffChoiceDialog(tk.Toplevel):
    """对有分歧的题，让用户逐题选用原答案或验证答案。"""

    def __init__(self, master: tk.Misc, diffs: list[dict]) -> None:
        super().__init__(master)
        self.title("AI验证分歧")
        self.configure(bg=BG)
        self.transient(master)
        self.grab_set()
        self.result: dict[int, str] | None = None
        self._vars: dict[int, tk.StringVar] = {}
        self.geometry("880x640")
        self.minsize(720, 480)

        header = tk.Frame(self, bg=SIDEBAR, highlightbackground=SIDEBAR_LINE, highlightthickness=1)
        header.pack(fill="x")
        head_inner = tk.Frame(header, bg=SIDEBAR)
        head_inner.pack(fill="x", padx=16, pady=12)
        tk.Label(
            head_inner,
            text=f"有 {len(diffs)} 题意见不同，请选择保留哪一个",
            bg=SIDEBAR,
            fg=TEXT,
            font=("Microsoft YaHei UI", 12, "bold"),
        ).pack(anchor="w")
        tk.Label(
            head_inner,
            text="确认后才会改动当前答案。关闭窗口表示取消。",
            bg=SIDEBAR,
            fg=MUTED,
            font=("Microsoft YaHei UI", 9),
        ).pack(anchor="w", pady=4)

        canvas_wrap = tk.Frame(self, bg=BG)
        canvas_wrap.pack(fill="both", expand=True, padx=12, pady=12)
        canvas = tk.Canvas(canvas_wrap, bg=BG, highlightthickness=0)
        scroll = ttk.Scrollbar(canvas_wrap, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        body = tk.Frame(canvas, bg=BG)
        window_id = canvas.create_window((0, 0), window=body, anchor="nw")

        def _resize(_event=None) -> None:
            canvas.configure(scrollregion=canvas.bbox("all"))
            canvas.itemconfigure(window_id, width=canvas.winfo_width())

        body.bind("<Configure>", _resize)
        canvas.bind("<Configure>", _resize)

        def _on_mousewheel(event) -> None:
            canvas.yview_scroll(int(-event.delta / 120), "units")

        canvas.bind("<Enter>", lambda _e: canvas.bind_all("<MouseWheel>", _on_mousewheel))
        canvas.bind("<Leave>", lambda _e: canvas.unbind_all("<MouseWheel>"))

        for item in diffs:
            card = tk.Frame(body, bg=CARD, padx=12, pady=10, highlightbackground=LINE, highlightthickness=1)
            card.pack(fill="x", pady=5)
            title = f"第 {item['no']} 题 · {TYPE_LABEL.get(item['type'], item['type'])}"
            tk.Label(card, text=title, bg=CARD, fg=ACCENT_DEEP, font=("Microsoft YaHei UI", 10, "bold")).pack(anchor="w")
            stem = " ".join((item.get("stem") or "").split())
            stem_label = tk.Label(
                card,
                text=stem,
                bg=CARD,
                fg=MUTED,
                wraplength=780,
                justify="left",
                anchor="w",
                font=("Microsoft YaHei UI", 9),
            )
            stem_label.pack(fill="x", pady=6)
            choice = tk.StringVar(value="original")
            self._vars[item["no"]] = choice
            self._add_answer_choice(card, choice, "original", "保留原答案", show_answer(item.get("original")))
            self._add_answer_choice(card, choice, "reviewed", "采用验证答案", show_answer(item.get("reviewed")))

            def _sync_wrap(event, label=stem_label) -> None:
                label.configure(wraplength=max(event.width - 24, 320))

            card.bind("<Configure>", _sync_wrap)

        actions = tk.Frame(self, bg=BG)
        actions.pack(fill="x", padx=12, pady=12)
        ttk.Button(actions, text="全部保留原答案", command=self._keep_all).pack(side="left")
        ttk.Button(actions, text="全部采用验证", command=self._use_all).pack(side="left", padx=(8, 0))
        ttk.Button(actions, text="取消", command=self._cancel).pack(side="right")
        ttk.Button(actions, text="确认选用", style="Primary.TButton", command=self._confirm).pack(side="right", padx=(0, 8))
        self.protocol("WM_DELETE_WINDOW", self._cancel)

        def _cleanup() -> None:
            try:
                canvas.unbind_all("<MouseWheel>")
            except Exception:
                pass

        self.bind("<Destroy>", lambda _e: _cleanup())
        self.wait_window(self)

    def _add_answer_choice(self, parent: tk.Misc, variable: tk.StringVar, value: str, title: str, answer: str) -> None:
        block = tk.Frame(parent, bg=CARD)
        block.pack(fill="x", pady=4)
        ttk.Radiobutton(block, text=title, variable=variable, value=value).pack(anchor="w")
        box = tk.Frame(block, bg=CARD)
        box.pack(fill="x", pady=(4, 0))
        lines = max(3, min(12, answer.count("\n") + max(len(answer), 1) // 36 + 1))
        text = tk.Text(
            box,
            wrap="word",
            height=lines,
            bg="#F8FAFC",
            fg=TEXT,
            font=("Microsoft YaHei UI", 10),
            relief="solid",
            borderwidth=1,
            highlightthickness=0,
            padx=8,
            pady=6,
        )
        bar = ttk.Scrollbar(box, orient="vertical", command=text.yview)
        text.configure(yscrollcommand=bar.set)
        text.pack(side="left", fill="both", expand=True)
        bar.pack(side="right", fill="y")
        text.insert("1.0", answer or "（空）")
        text.configure(state="disabled")

    def _keep_all(self) -> None:
        for var in self._vars.values():
            var.set("original")

    def _use_all(self) -> None:
        for var in self._vars.values():
            var.set("reviewed")

    def _confirm(self) -> None:
        self.result = {number: var.get() for number, var in self._vars.items()}
        self.destroy()

    def _cancel(self) -> None:
        self.result = None
        self.destroy()


def _enable_dpi() -> None:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            return


def launch() -> None:
    app = App()
    app.mainloop()
