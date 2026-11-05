# 系统运行边界复检（2026-11-05）

基线：`c1b079c`。范围是启动、JSONL 宿主、工具执行、MCP 扩展、长会话作业诊断和既有记忆/任务/协作链路。
本轮使用隔离临时目录与真实 SQLite；不对本机产品数据库执行修复或重置。

## 基线与新增故障

原有全量基线为 571 passed、1 skipped，见
[`runtime-reaudit-baseline-20261105.xml`](../evaluations/runtime-reaudit-baseline-20261105.xml)。
这组既有用例通过不代表所有运行边界均已覆盖。本轮额外故障注入发现并修复：

| 边界 | 可复现的原问题 | 当前行为 |
| --- | --- | --- |
| JSONL 解析 | 深度过大或整数转换超过解释器限制时抛出非 JSONDecodeError，宿主退出 | 返回 -32700，继续处理下一行 |
| JSONL 参数 | []、false、0、空字符串被 `or {}` 静默当成空参数 | 返回无效请求，不执行操作 |
| 工具结果归属 | 执行器返回其他 call_id、tool_name 或参数，仍按当前调用保存成功 | 验证返回 DTO、状态枚举、调用与参数身份；不接纳错配内容 |
| 工具观察元数据 | 不可 JSON 序列化或超过回执上限时在落库阶段抛异常，留下 DISPATCHING | 在受保护边界内校验，与 Journal 使用相同上限；只读故障用例留下真实 FAILED 回执 |
| CLI/JSONL 启动 | 数据主目录实际上是文件时，日志/目录初始化在异常边界外抛出 traceback | CLI 给出启动失败提示；JSONL 返回 -32000；均退出 1，不改写既有文件 |
| MCP 配置 | 启动 refresh 清掉 CONFIGURATION_ERROR；极深配置 JSON 可使应用初始化异常 | 保留可见配置诊断，配置解析资源错误同样记录为配置错误 |
| MCP 初始化 | 尚未收到 initialize 成功响应就发送 initialized 和业务请求；初始化错误/版本不支持仍可显示 READY | 先等初始化响应，核验成功与支持版本，再进入业务阶段；失败不注册工具 |
| MCP 写入超时 | 子进程不读取 stdin 时，大参数写入阻塞在既有响应超时之前 | 初始化、写入和响应共享总时限；写入超时终止子进程释放管道，避免卡住主线程 |
| 长会话状态 | 查询超过 100 条作业后仍只显示最旧 100 条；taskId 不过滤 jobs | 返回最新有界窗口并保持时间正序；指定任务只返回该任务作业 |
| Windows 启动脚本 | 丢弃命令行参数，--help / --execution-mode 无法转交 | 原样转交参数；本机实际 cmd.exe 启动验证帮助输出成功且未初始化产品数据 |

工具返回失败描述的是结果未被宿主接纳，不应由此推断外部副作用一定没有发生。
既有非幂等执行与审批规则继续生效；本轮工具畸形返回的故障注入使用只读执行器。
协作执行契约增加工具结果身份校验版本，旧契约不能被静默认证为新契约。

MCP 握手顺序、版本与超时处理参考项目所声明的
[MCP 2024-11-05 生命周期规范](https://modelcontextprotocol.io/specification/2024-11-05/basic/lifecycle)。
没有擅自更换协议版本；不能把本次局部兼容修复表述为支持全部 MCP 特性或所有服务端版本。
测试使用真实 Python 子进程模拟拒绝初始化、版本不匹配、严格握手时序和停止读取 stdin，
而非仅用 mock 函数绕过传输。写入阻塞场景修复前约 4 秒才返回，即使配置时限只有 0.5 秒；
修复后按总时限结束，并对清理时间保留有界余量。

## 验收证据

- 故障注入：`code/tests/test_runtime_reaudit.py`。
- 全量回归与快照：[`runtime-reaudit-20261105.json`](../evaluations/runtime-reaudit-20261105.json)、
  [`runtime-reaudit-20261105.xml`](../evaluations/runtime-reaudit-20261105.xml)。
- 扩展产品场景：[`runtime-reaudit-product-trial-20261105.json`](../evaluations/runtime-reaudit-product-trial-20261105.json)。
- 实际 Windows 启动：[`runtime-reaudit-launcher-20261105.json`](../evaluations/runtime-reaudit-launcher-20261105.json)。
- 最终真实 Provider 图执行：[`runtime-reaudit-live-20261105.json`](../evaluations/runtime-reaudit-live-20261105.json)。

真实 Provider 使用临时文件中的随机 token，检查双文件读取、依赖合并和整体验证的真实回执与原始值。
该专项关闭自动 TaskReview/经验学习；既有任务闭环、修复、预算、来源失效、审批与记忆链路由全量回归覆盖，
不将专项真实 API 结果冒充所有功能都重新经过真实模型联调。

最终结果：**594 passed、1 skipped、0 failures、0 errors**；新增 **23** 个边界用例。
跳过项为 Windows 的可选 readline。扩展产品试运行 **32/32**、有种子扰动 **50 步**均通过。
最终真实 API 的 **5/5** 断言通过，使用 **15** 次模型调用；记录中的代码哈希与待提交文件一致。
修改 Python 文件完整 Ruff、全仓 E9/F821/F822/F823 严重错误检查、compileall 和 diff 检查均通过。
不将这些检查表述为全仓所有既有风格债务都已清除。

## 范围边界

本轮证明的是已列明场景通过、发现的运行边界异常得到处理，不证明任意输入和第三方扩展下绝对无错误。
权限不足、磁盘不可写、Provider 不可用或协议不兼容仍可能产生清楚的失败状态；不会为了避免报错而伪造成功。
验证环境是本机 Windows/Python；Linux/macOS 启动和全部可选远程 MCP 服务不在本机实测范围。
协作仍是最多三个只读子任务的串行执行，自动处理仍在轮次、恢复与显式 drain 发生。
