# 产品使用入口

完整、面向用户的产品使用手册已经统一放在：

[`docs/cli-user-guide.md`](cli-user-guide.md)

从仓库根目录启动 CLI：

```bash
./code/start_agent.sh
```

Windows：

```powershell
.\code\start_agent.cmd
```

进入 CLI 后，在 `你 >` 提示符后直接输入自然语言；输入 `/help` 查看命令，输入 `/quit` 退出。

浏览器 Demo：

```bash
./code/start_demo.sh
```

Windows 使用 `.\code\start_demo.cmd`。Demo 与 CLI 共用 `PersonalAgentApp`，不是第二套 Agent。
如果服务运行在 China Ubuntu、浏览器在 Mac，请在 Mac 建立
`ssh -N -L 8000:127.0.0.1:8000 china@<china-ubuntu-host>`，再访问
`http://127.0.0.1:8000/`。

需要给其他前端接入时，可运行 JSONL host：

```bash
python code/scripts/personal_agent_app_server.py
```

它提供 initialize、thread/session、turn、事件重放、上下文/状态、工具目录和审批查询；
每行一个 JSON 请求，当前同步 host 不支持 `turn/interrupt`。

手册覆盖 API 配置、普通对话、会话与任务、长期记忆、联网工具、上下文压缩、高风险操作确认、数据保存位置和常见错误处理。本文不再重复开发者架构说明，避免用户在多个版本的命令列表之间产生混淆。
