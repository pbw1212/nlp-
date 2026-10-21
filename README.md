# MAPAS：A Multi-Agent Personalized Agentic System

MAPAS 是面向长程任务的多 Agent 个人智能体系统，基于 DeepSeek API，提供 CLI 与 JSONL 接口。当前默认使用 `single` 模式，也可显式启用实验性的四角色串行协作 `multi_fixed`（P1）。该基础层不代表系统已具备分布式多 Agent 能力，也不构成协作效果优于强单 Agent 的结论。

当前核心算法、四项协同记忆机制与实验路线见 [MAPAS 核心算法与技术方案](docs/research/mapas-core-algorithms.md)；实际进展见 [项目进展](docs/project-progress.md)。多 Agent P1 的实现边界与阶段证据分别见 [架构说明](docs/architecture/multi-agent-foundation.md) 和 [实施报告](docs/phases/multi-agent-p0-p1-20261014.md)。

## 仓库

本项目唯一工作远端为 [`pbw1212/nlp-`](https://github.com/pbw1212/nlp-)（Git 远端名 `origin`，主分支 `main`）。Git 网络操作前必须按[仓库绑定规则](docs/repository-policy.md)核验远端；历史记录中的地址不代表当前远端。

## 快速开始

需要 Python 3.10 或以上。

```powershell
python -m pip install -e .
$env:DEEPSEEK_API_KEY = '你的密钥'
.\code\start_agent.cmd
```

Linux/macOS：

```bash
python3 -m pip install -e .
export DEEPSEEK_API_KEY='your-key'
./code/start_agent.sh
```

也可以将 `.env.example` 复制到用户 Agent 主目录的 `config/.env.local`，填入自己的凭据。默认主目录为 `~/.agent_data`；`AGENT_HOME` 可修改主目录，`AGENT_DATA_DIR` 可隔离测试数据。密钥文件和产品数据库不提交 Git。

CLI 用法见 [操作说明](docs/cli-user-guide.md)。常用命令：

- `/remember 内容`：显式记忆生命周期处理；根据实际提交结果回应。
- `/memories`、`/memory-history id`、`/memory-provenance id`：查看记忆和来源。
- `/forget id`、`/restore-memory id`：移入回收站、恢复。
- `/new-session`、`/resume id`：开始或恢复对话。
- `/new 标题`、`/tasks`、`/switch id`：任务管理。
- `/tools`、`/workspace`：查看工具与授权工作目录。
- `/confirm`、`/reject`：处理有副作用的工具请求。

JSONL host：

```bash
python code/scripts/personal_agent_app_server.py
```

每行一个 JSON 请求；支持 initialize、thread/session、turn、事件读取、状态/上下文、工具目录、审批与 MCP。同步 host 暂不支持 `turn/interrupt`。

可选多 Agent 模式：

```bash
python code/scripts/personal_agent.py --execution-mode multi_fixed
```

该模式是实验性的 P1 串行协作链路；未指定参数时仍使用默认 `single` 模式。

## 系统边界

```text
CLI / JSONL → PersonalAgentApp → MainAgent（推理、工具、执行 Journal）
                               └→ AgentRuntime（turn/任务绑定）
                                   ├→ Task 1：长期记忆 identity/revision/provenance/retention
                                   ├→ Task 2：current-safe recall → applicability → EvidenceBundle
                                   └→ Task 3：任务状态与工作上下文
```

普通轮次由 Agent 按需调用 `update_long_term_memory`；工具使用原始用户事件，Provider 提议长期有效 episode 和 lifecycle 操作，经校验后原子写入。没有隐藏的定时或轮数自动沉淀。Task 1 的失败 ingestion 保留以便重试。

SQLite 是权威存储；对话原文也存 SQLite，当前没有另一个同步写入的 transcript 文件库。Task 2 每次从当前有效快照读取；旧字符 2-gram 索引只是兼容召回。默认 Task 2 用 BM25 和可选图扩展，dense/hybrid 为可插拔组件，不能将默认机制称为 embedding 语义检索。

## 验证

```bash
python -m pip install pytest
python -m pytest -q
python code/scripts/personal_agent_trial.py --output-json artifacts/product_trial/latest.json
```

全量回归、产品场景与有限真实 API 的最终结果见维护报告和 JSON 证据。真实 API 脚本 `code/scripts/maintenance_live_smoke.py` 使用隔离临时目录、最多 30 次请求；运行会消耗 API，离线 pytest 不需要真实密钥。

## 当前限制

- 默认词法检索无法保证改述或跨语言召回；提炼契约已要求保留原语言，旧英文记忆没有自动翻译。
- 用户删除是可恢复的回收操作；历史版本保留，不能当成隐私彻底擦除。
- 单进程执行为主，尚未具备多 Agent 分布式一致性、租约与跨服务幂等协议。
- Windows/macOS 终端沙箱工具按既有契约拒绝执行；文件工具仍受授权目录限制。
- 真实 Provider 语义质量只有小样本证据；不能由回归通过推导长期运行全面可靠。

架构/ADR、benchmarks、项目历史和研究结论保留为 MAPAS 的可追溯资料。第三方源码快照不随工作仓库分发；研究文档记录必要的上游仓库与 revision。`phases/` 和旧评估描述对应历史快照，不覆盖当前架构及进展文档。
