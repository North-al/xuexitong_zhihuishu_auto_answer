# 作业答题 · 超星 / 智慧树

本地桌面工具：从 Chrome / Edge 读取超星学习通「作业作答」或智慧树「作业考试 / 单元测试」题目，调用 OpenAI 兼容接口生成答案，再写回浏览器。

**不会点击「提交 / 交卷 / 暂时保存 / 提交作业 / 暂存作业」。** 是否交卷由你本人在网页上决定。

> 仅供个人学习与技术研究。请遵守学校与平台规定，勿用于违规代考或批量作弊。

## 功能

| 平台 | 能力 |
| --- | --- |
| 超星学习通 | 一页多题；单选 / 多选 / 判断 / 填空 / 简答；选项打乱时按正文匹配 |
| 智慧树 | 一题一页自动翻页收集；单选 / 多选 / 判断写入；选项随机排序按正文匹配；多选按勾选图标识别状态 |

其它：

- 桌面 GUI：选窗口 → 更新题目 → 生成答案 →（可选）AI 交叉验证 → 写入 / 核对
- CLI：`parse` / `solve` / `apply` / `verify` / `gui`
- 答题服务与验证服务可分开配置（OpenAI / DeepSeek / 自定义 Base URL）
- 带图题目可截图后发给多模态模型

## 环境要求

- Windows 10/11（依赖 UI Automation）
- Python **≥ 3.14**
- Google Chrome 或 Microsoft Edge
- 可用的 OpenAI 兼容 API（密钥自备）

## 快速开始

### 1. 安装

推荐 [uv](https://github.com/astral-sh/uv)：

```bash
uv sync
```

或使用 pip：

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -e .
pip install pillow uiautomation
```

开发打包额外依赖：

```bash
uv sync --group dev
```

### 2. 配置 API

任选其一：

1. **界面**：启动后打开「AI 设置」，填写接口地址、模型名、API Key 并保存（写入 `runtime/openai.json`，该目录默认不入库）。
2. **示例文件**：复制 `runtime/openai.example.json` 为 `runtime/openai.json` 后填入密钥。
3. **环境变量**：

```text
OPENAI_API_KEY=sk-...
OPENAI_BASE_URL=https://api.deepseek.com
OPENAI_MODEL=deepseek-chat
```

可参考根目录 `.env.example`（应用读取的是进程环境变量，不会自动加载 `.env` 文件）。

### 3. 启动

```bash
# 桌面窗口（推荐）
uv run python -m auto_answer
# 或
uv run python run_gui.py
```

使用步骤：

1. 用 Chrome / Edge 打开超星「作业作答」或智慧树测验页，保持该标签可见。
2. 在本程序中「刷新窗口」并选中对应项（会标注平台）。
3. 「更新题目」→「生成答案」→（可选）「AI验证」→「写入页面」。

### 4. 命令行

```bash
uv run python -m auto_answer parse
uv run python -m auto_answer solve --model gpt-4o-mini
uv run python -m auto_answer apply --window "作业作答 - Google Chrome"
uv run python -m auto_answer verify
uv run python -m auto_answer gui
```

## 打包成 exe

```bash
uv sync --group dev
uv run pyinstaller --noconfirm auto_answer.spec
```

产物：`dist/作业答题-超星智慧树.exe`（无控制台窗口）。配置与答案仍落在 exe 同级的 `runtime/`。

## 目录结构

```text
auto_answer/          # 主代码
  browser.py          # 枚举超星 / 智慧树窗口
  homework.py         # 超星解析、作答、写入
  zhihuishu.py        # 智慧树解析、作答、写入
  gui.py              # 桌面界面
  config.py           # API 配置
assets/               # 图标
docs/                 # 实现说明（控件识别与点击流程）
runtime/              # 本地配置与答案（gitignore，仅保留示例）
run_gui.py            # 打包入口
auto_answer.spec      # PyInstaller 配置
```

更细的控件识别说明见 [docs/作业作答流程.md](docs/作业作答流程.md)。

## 安全与隐私

- **不要提交** `runtime/openai.json`、答案 JSON、题目文本、截图、`.env`。
- API Key 只保存在本机 `runtime/` 或环境变量中。
- 程序不会把密钥上传到除你配置的模型接口以外的地址。

若曾误把密钥提交进 Git，请立刻在服务商控制台轮换密钥，并用 `git filter-repo` / BFG 清理历史。

## 已知限制

- 仅 Windows；依赖前台浏览器窗口与 UI Automation。
- 智慧树填空 / 简答暂不支持自动写入。
- 高 DPI / 多显示器下多选勾选态依赖窗口截图像素判断，极端缩放偶发需单题重写。
- 页面结构若被平台大幅改版，需更新选择器逻辑。

## 许可证

[MIT](LICENSE)
