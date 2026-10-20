# ADR-0023：MAPAS 项目名称与仓库绑定

Status: **Accepted**。日期：2026-10-20。

## 背景

系统已加入可选的串行多 Agent P1 基础层，正式名称统一为 MAPAS（Multi-Agent Personalized Agentic System）。工作区目录与 GitHub 仓库地址分别管理，避免用本地文件夹名称猜测远端。

## 决定

- 项目、README、当前架构文档、发行包、Conda 环境、启动器和产品标识统一使用 MAPAS / `mapas`。
- 当前本地工作副本为 `D:\MAPAS\MAPAS`；唯一工作远端为 `https://github.com/pbw1212/nlp-`（`origin`，主分支 `main`）。同步前按[仓库绑定规则](../repository-policy.md)核验。
- 本地目录重命名不更改 GitHub 仓库 slug、提交历史、持久化数据目录或已有用户数据。
- 2026-10-14 维护证据中的品牌、仓库地址和路径标识已按 MAPAS 统一；日期、测量值、哈希和执行结果仍保留原记录。规范化范围见 `../maintenance/2026-10-14/README.md`。
- 2026-08 至 2026-09 的研究矩阵继续表示原核查时点的实现状态；文中“当前”按该时间上下文理解，术语按现名 MAPAS 展示。
- 新 Conda 环境为 `mapas`。启动器优先使用新环境，并保留旧环境回退，保证已有安装仍可启动。
- 发行包和外部请求 User-Agent 使用 `mapas-agent-memory`；Python import 路径继续为 `src.memory`。
- Provider 错误解析优先读取 `_mapas_error_body`，保留旧字段回退，兼容已有适配器。
- JSONL 服务对外产品名为 `mapas-agent`。现有 v1 协议标识保留为兼容契约；直接改写会使既有客户端无法初始化。旧标识只用于协议兼容，不代表当前系统名称。

## 后果

新安装、界面文案、当前文档和仓库路由均采用 MAPAS。已有 Conda 环境、Provider 适配器和 JSONL 客户端继续通过兼容入口工作；未来若退役兼容入口，应另行安排版本迁移。

Supersedes: None。
