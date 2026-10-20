# 代码与文档布局

仓库采用“代码集中、文档外置”的布局：

```text
code/
├── src/       生产代码
├── tests/     自动化测试
├── scripts/   CLI、验收与评估脚本
└── start_agent.*  启动入口

benchmarks/    评测数据与 gold fixtures
docs/          架构、决策、阶段记录和使用说明
~/.agent_data/  Agent 全局运行数据（长期记忆、对话、Task 3、Journal、CLI 历史，按固定子目录归档）
.runtime/      旧测试数据库、评测产物、缓存和构建生成物（本地忽略）
```

根目录保留项目入口配置（`pyproject.toml`、`setup.cfg`、`environment.yml`、
`.env.example`）和 README。原始需求材料仅保存在维护者本地归档，不随仓库分发；
设计依据与当前实现边界见 `docs/architecture/` 和 `docs/research/`。项目进展
总览放在 `docs/project-progress.md`。生产包的 Python import 名称仍为
`src.memory`，只是物理路径统一迁移到 `code/src/memory`；启动器和测试会自动
加入 `code/` 到 Python 搜索路径，因此可以从仓库根目录运行：

```bash
./code/start_agent.sh
conda run -n mapas python -m pytest -q
```

## 当前运行入口

- CLI：`./code/start_agent.sh`，或直接运行 `code/scripts/personal_agent.py`。
- JSONL host：`code/scripts/personal_agent_app_server.py`，供程序客户端调用；
  当前支持 initialize、thread/session、turn、事件读取、状态/上下文、工具目录、审批
  查询和 MCP 状态查询，同步 host 暂不支持 `turn/interrupt`。

默认运行数据不写入当前工作目录，而写入用户级 `~/.agent_data/`。其中配置、会话、
长期记忆、任务状态、执行 journal、CLI 历史和事件存储按职责分目录；测试可用
`AGENT_DATA_DIR` 隔离数据，只有明确设置 `AGENT_HOME` 时才改变全局 Agent 主目录。
