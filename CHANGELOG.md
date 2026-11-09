# 更新日志

本文件遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

发版时：先在本文件写入对应版本说明，再打 `vX.Y.Z` 标签并推送；GitHub Actions 会打包 exe，并把该版本章节写入 Release 说明。

## [Unreleased]

### 计划

- （在此记录未发布改动）

## [1.0.0] - 2026-11-09

### 新增

- 支持智慧树「作业考试 / 单元测试」：逐题收集、按选项正文匹配写入、多选勾选态识别
- 桌面界面品牌与说明改为「超星 / 智慧树」双平台
- GitHub 仓库文档：`README`、`LICENSE`、`SECURITY`、示例配置与 Issue 模板
- 推送 `v*` 标签后自动构建 Windows exe 并发布到 GitHub Releases

### 变更

- 窗口标题与侧栏文案统一为「作业答题 · 超星 / 智慧树」
- 打包产物命名为 `作业答题-超星智慧树.exe`

### 修复

- 智慧树多选误判（整行蓝字干扰、漏点 / 误取消）：改为只检测勾选图标，并增加写入复核

### 安全

- `runtime/`、API Key、答案与截图默认不进入版本库；提供 `openai.example.json` / `.env.example`

## [0.1.0] - 2026-11-01

### 新增

- 超星学习通「作业作答」本地辅助：读题、OpenAI 兼容接口生成答案、写回浏览器（不自动提交）
- 支持单选 / 多选 / 判断 / 填空 / 简答与带图题目
- 桌面 GUI 与 CLI（`parse` / `solve` / `apply` / `verify` / `gui`）
- 答题服务与验证服务可分开配置

[Unreleased]: https://github.com/North-al/xuexitong_zhihuishu_auto_answer/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/North-al/xuexitong_zhihuishu_auto_answer/releases/tag/v1.0.0
[0.1.0]: https://github.com/North-al/xuexitong_zhihuishu_auto_answer/releases/tag/v0.1.0
