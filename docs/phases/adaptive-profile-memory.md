# Adaptive Profile：用户特征仓库

日期：2026-09-05

## 交付范围

- 新增“用户特征仓库”页面，显示有效特征、系统理解、锁定数量和可恢复区。
- 提供手动新增、修正、锁定/解锁、收起和恢复操作。
- 手动确认默认锁定；未锁定画像允许 Provider 在后续长期对话中提议隐式合并、修正或删减。
- 新增 recoverable `REMOVE` lifecycle，自动操作不物理删除数据。
- 首次自动记忆也由 Provider 决定 `MemoryType` 与 granularity，移除固定 `EPISODIC_GRAPH` 首条写入。
- 网页端与桌面端复用同一组本地 API 和 SQLite 权威数据。

## 验证

- `test_ingestion_journal.py` 覆盖首次画像分类、未锁定自动移除、锁定保护与原子回滚。
- `test_personal_agent_demo.py` 覆盖 profile 读取及完整手动 CRUD API。
- 相关回归：60 passed，1 个 Windows 图形环境探测用例按既有方式排除。
- 完整测试运行中本次相关路径通过；其余失败来自既有 Windows 文件句柄、环境变量与平台路径/终端差异。
- PyInstaller desktop smoke test exit code 0；打包后 `GET /api/profile` 返回成功。
