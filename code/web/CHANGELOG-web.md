## v0.47 — 2026-11-06 · README 第 14 章改版：快捷版在前、详细版在后

**改了什么**

- 第 14 章开头新增 **「快捷版（1–8 步，照抄即可）」**：
  1 装后端依赖 → 2 装前端依赖 → 3 API Key（可跳过）→ 4 `start.cmd` 一键启动桥接+前端
  → 5 打开 localhost:5173 → 6 口令（免校验无需操作）→ 7 填 API Key → 8 新建会话开始对话；
- 快捷版之后接 **「一、详细版：逐步说明」**，保留原来的逐步解释（两种桥接启动方式、
  预期现象、常见坑），作为深入阅读；
- 清掉详细版里重复的「快捷方式」小广告块（信息已并入快捷版），避免两处说法不一致；
- 附录 B 数据路径纠正为实际布局（`long_term_memory/memory.db`、`task_state/task_state.db`、
  `execution/` 下 4 个库与 trace）；附录 A 的 README 描述改为「项目说明 + 本使用指南」。

**为什么**

用户提出：快捷启动方式较多，应该先给最短路径（1–8 步），再补充旧的分步说明。

**验收**

- 第 14 章结构：`## 14` → `### 快捷版`（`#### 1–8`）→ `### 一、详细版`（`#### 0–8`）→ `二`…`六` → `附录 A–J`；
- 文档内无重复的快捷启动说明；相对链接可达。

---

## v0.46 — 2026-11-06 · 一键启动脚本（桥接 + 前端 + 浏览器）

**背景**

原来要开两个终端：先启动桥接、再启动前端、再手输网址。组员容易漏步骤，或忘记桥接导致「后端未连接」。

**改了什么（纯新增）**

- `code/web/start.cmd`：Windows 一键启动。内部用 `-ExecutionPolicy Bypass` 调起 `scripts/start.ps1`：
  1. 环境检测（Python / pnpm 或 npm / 8787、5173 端口占用）；
  2. 启动桥接（`--no-token`，single 模式；`multi` 参数切换协作模式）；
  3. 首次自动安装前端依赖，再启动 dev server（各自独立窗口，日志可见）；
  4. 等待两个服务就绪后自动打开 http://localhost:5173；
- 幂等：端口已有服务时跳过启动，不会重复拉起或覆盖；
- `start.cmd check` 只做环境检查；`start.cmd multi` 用多 Agent 协作模式；`start.cmd nobrowser` 不自动开浏览器；
- README §2 与第 14 部分补充一键启动说明，目录结构加 `start.cmd` / `scripts/start.ps1`。

**验收**

- 实测 `start.cmd check`：正确输出 Python、pnpm、数据目录、端口占用并退出；
- 实测启动流程：桥接就绪 → 前端就绪 → 打开浏览器（本机 5173/8787 均正常）；
- 中文输出正常（UTF-8 BOM + chcp 65001），`.cmd` 为 ASCII/CRLF 确保 cmd 解析正确。

---

## v0.45 — 2026-11-06 · 修复测试组反馈的 10 个问题

测试组在浏览器里逐个复现后提出的问题，逐条定位并修复（全部实测，未新增模型调用）。

**高优先级**

1. **免口令模式允许跨源写入（CSRF）** —— 桥接只加 CORS 响应头，没有校验请求来源；
   恶意本地网页可用「简单请求」提交 JSON（浏览器读不到响应，但服务端已执行写操作）。
   修复：非 GET/OPTIONS 请求校验 `Origin` 白名单，跨源直接 403；带 body 的 POST 强制
   `application/json`（JSON 必须预检，预检已被 Origin 拦住）。实测：恶意 Origin → 403，text/plain → 415。
2. **网页无法删除 API Key** —— CORS 预检 `Access-Control-Allow-Methods` 漏了 `DELETE`，
   浏览器直接拦掉请求（页面误报"连不上桥接"）。修复：预检允许 `GET, POST, DELETE, OPTIONS`；
   实测 `DELETE /api/api-key` 的预检通过。

**中优先级**

3. **切换回放后仍显示实时数据** —— `changeSource` 只换了数据源对象，页面状态没重置。
   修复：切换数据源时递增 `epoch`，当前页整页重挂载、从新数据源重新取数。
4. **断线/重连状态不同步** —— 顶栏只在启动/切模式时探测。修复：后台每 15 秒探活；
   桥接停机时顶栏及时变「后端未连接」；恢复连接时递增 `epoch` 让页面重新取数，
   清掉断线期间残留的旧报错（新口令验证后无需再手点刷新）。
5. **窄屏（390px）导航与顶栏不可用** —— 原来 ≤900px 直接把侧栏隐藏、顶栏不换行。
   修复：≤900px 顶栏自动换行并隐藏品牌文字/协议 chip；侧栏改为横向滚动导航条；
   对话页会话栏改为顶部横条，正文不再被挤掉。
6. **回放数据中文损坏** —— 早期录制链路把中文写成了 `?`（仓库里的 JSON 已损坏）。
   修复：用 updated 桥接整批重录 fixtures（`?src=fixture` 里所有中文正常）；
   无法恢复的 1 条损坏记忆 / 1 条损坏任务 / 5 条损坏 trace 按"只删不改"剔除，
   并在 `fixtures/README.md` 记录原因与重录方法。

**低优先级**

7. **过程耗时容易误读** —— 过程摘要把各阶段与工具耗时相加（阶段之间重叠），
   与轮次头的整轮耗时不一致。修复：标注为「累计 X」，tooltip 说明"耗时之和、不等于墙钟耗时"。
8. **协作图接口 404** —— 前端契约声明 `GET /api/collaboration/runs/:runId/graph`，桥接没实现。
   修复：新增该接口（plan 节点 spec / artifacts / handoffs / model_calls / budget），
   实测历史 run：2 节点、2 产物、11 次调用、spec 齐全。
9. **审批 decision 传数组返回 500** —— `raw_decision not in {...}` 对 list 抛 TypeError。
   修复：先校验 `isinstance(raw_decision, str)`；实测传数组 → 400。
10. **网页没有中断恢复入口** —— 桥接能列出/恢复未完成轮次，但界面没有按钮。
    修复：对话页在存在未完成轮次时显示提示条 + 每个轮次一个「继续执行」按钮，
    点击调用 `POST /api/threads/:id/turns/:turnId/resume` 并复用既有作业轮询。

**验证**

- 桥接：`py_compile` 通过；真实桥接上实测 403/415/400/200 与 graph 接口；
- 前端：`tsc` 0、`vite build` 0；
- 回放：fixtures 全量检查无 `?`/U+FFFD，关键 fixture key 齐全。

---

## v0.44 — 2026-11-06 · 一键启动脚本（规避 Windows PowerShell 执行策略）

**背景**

组员在 Windows PowerShell 里首次运行前端时会连续踩两个坑：

1. `pnpm` 未安装 → `pnpm : 无法将"pnpm"项识别为…`；
2. 装好后 `pnpm`/`npm` 又报 `无法加载文件 …pnpm.ps1，因为在此系统上禁止运行脚本`
   —— PowerShell 默认执行策略是 `Restricted`，会拦截所有 `.ps1` 包装脚本。

这是**机器策略**问题，项目侧不可能改用户系统设置，只能提供不受策略限制的入口。

**改了什么（纯新增）**

- `code/web/dev.cmd`：Windows 一键启动。内部用 `powershell -NoProfile -ExecutionPolicy Bypass`
  调起 `scripts/dev.ps1`（只对这一次调用生效，不改系统策略）；自动检测 pnpm/npm，首次自动装依赖；
- `code/web/build.cmd`：一键 typecheck + 生产构建，同上机制；
- `code/web/scripts/dev.ps1` / `scripts/build.ps1`：实际逻辑（UTF-8 BOM，保证 PowerShell 5.1 中文正常）；
- `code/web/dev.sh`：macOS / Linux 一键启动；
- README 更新：快速开始、第 14 部分第 2/5 步、常见问题表都补了 `.\dev.cmd` 与
  「`pnpm.cmd` / `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`」三种解法；目录结构补 `scripts/`。

**为什么这样做（而不是改 package.json）**

`.ps1` 拦截发生在 PowerShell 调命令这一步，与包管理器无关（npm 同样会被拦），
所以 `corepack`、`packageManager` 字段都救不了；`.cmd` 不受执行策略限制，是最小成本的正解。

**验收（本机实测）**

- `dev.cmd --check` → `[OK] 找到 pnpm（将使用 pnpm 安装依赖与启动）`，exit 0；
- `build.cmd` 全流程 → `tsc --noEmit` 通过 + `vite build` 成功（CSS 38.87 kB / JS 468.49 kB），exit 0；
- 未修改任何系统策略；中文输出正常（BOM + chcp 65001）。

---

## v0.43 — 2026-11-06 · 撤回过度精简，恢复完整步骤版

**改了什么**

- 撤回 v0.42 的过度精简：恢复 `docs/user-guide.md` 的完整逐步说明（246 行），
  依赖安装、桥接启动、前端启动、浏览器入口、首次配置、对话流程等**具体命令全部保留**；
- 恢复第 4 步的两组命令（免校验 / 需要校验）及第 7 步按方式分支的口令说明；
- 恢复七个页面表、常见问题（13 条）、离线演示、附录 A–J。

**仅做的小幅优化（不删内容）**

- 顶部新增「命令速查」代码块：5 行命令概览，方便老手直接抄，详细步骤仍在下方；
- 桥接检查点补一条：macOS/Linux 把 `python` 换成 `python3`；
- 附录目录树里 README 的描述更新为"项目说明（架构 / 功能 / 桥接接口 / 限制）"（README 已重写）。

**为什么**

用户反馈：精简要求是删掉"不必要的重复"，不是把要执行的命令一带而过；组员需要照着一步步做。

**验收**

- 关键命令齐全：`pip install -e .`、`pnpm install`、`web_bridge.py`、`pnpm dev`、`localhost:5173`、`pnpm build/preview`
- 文档内相对链接可达；`tsc` 0、`vite build` 0

---

## v0.42 — 2026-11-05 · 使用指南精简（238 → 115 行）

> 本版已撤销（2026-11-06）：精简过度，删掉了依赖安装、桥接启动等**必须的具体命令**，
> 已由 v0.43 恢复完整的逐步说明。保留本记录仅作历史。

**改了什么**

- **启动只给一条通用命令**：`python code\web\bridge\web_bridge.py --port 8787 --no-token`，
  一行说明"去掉 `--no-token` 则启动时打印口令"；不再展开两组命令与模式参数选择
- **模式说明收敛成一句**：顶栏「模式」切换即可，下一轮生效（不再解释启动参数对默认模式的影响）
- 全文按"5 步跑起来 → 界面 → 七页 → 常用操作 → 常见问题 → 附录"重排，去掉重复解释与表格冗余
- 附录压缩为 6 小节：数据位置、模式区别、常用环境变量、安全须知、构建/重置、文档索引

**为什么**

用户要求：使用指南要"简化够用"，不要为了完整性把简单事情写复杂。

**验收**

- 115 行；启动命令与实际参数一致；文档内 4 个相对链接可达
- 覆盖：环境、启动、首次配置、七页、切换模式/主题、导出导入、离线演示、常见问题、数据位置

---

## v0.41 — 2026-11-05 · 使用指南第 4 步改为两组命令

**改了什么**

`docs/user-guide.md` 的「4. 启动后端桥接」原来是"一组命令 + 一句'想免校验就加 --no-token'"的写法，
补充说明容易被忽略。现在直接并列两组命令：

- **方式一（推荐）**：`--no-token`，免校验；启动后顶栏显示「口令：本机免校验」，第 7 步无需填口令；
- **方式二**：默认启动，终端打印 `token=…`；第 7 步粘贴；
- 之后追加"两种方式共同的检查点"（启动成功的标志、终端别关、如何切 multi_fixed）。

同时把第 7 步「口令」的说明改为按方式一/方式二分支，避免读者回头对照。

> 2026-11-06 补记：该段落被本地编辑器以旧版本覆盖过一次，已重新应用（当前文件为两组命令版本）。

**验收**

- 两组命令与桥接实际参数一致（`--no-token`、`--execution-mode single|multi_fixed`、`--port 8787`）
- 文档内其他引用（常见问题、界面速览）无冲突

---

## v0.40 — 2026-11-05 · 前端 README 重写（客观完整版）

**改了什么**

`README.md` 从 1,858 行的"交付记录流水账"重写为 218 行的**项目说明**，按读者关心的顺序组织：

1. 当前状态（页面/数据源/桥接/静态检查/测试/阻塞项 + 代码规模）
2. 快速开始（最小命令 + 指向 `docs/user-guide.md`）
3. 架构（三层图、为什么需要桥接、前端的硬性边界）
4. 目录结构（各目录职责）
5. 功能（七页能力表 + 8 项关键交互）
6. 数据源层（65 个方法、两个实现、探针与错误模型）
7. 桥接层（29 组路由按域分组 + 行为约定）
8. 配置与运行（dev/build/preview、数据目录、凭据、模式、端口）
9. 设计系统（token、四主题、组件策略）
10. 验证与已知限制（5 条限制逐条给原因与现状）
11. 文档索引（9 个文档）
12. 开发历史里程碑（P0–P6，细节指向 CHANGELOG）
13. 开发约定（增量、不造假、无语义规则、可撤回、自检）

**顺带清理**

- 删除空目录 `src/pages/`（占位页已在 v0.28 删除）
- README 中的目录结构改为真实存在的 `index.html`（原写 `public/` 但该目录不存在）

**为什么**

原 README 是逐轮追加的交付记录（1,858 行），信息完整但缺乏主次，不适合作为"进入这个目录先读什么"的说明。
历史细节仍完整保留在 `CHANGELOG-web.md`。

**验收**

- README 内 9 个相对链接全部可达
- 声明的规模与实际一致（48 个源文件 / 约 11,900 行 / 桥接 2,637 行 / 31 个 fixture / 29 组路由 / 65 个数据源方法）
- `tsc` 0、`vite build` 0

---

## v0.39 — 2026-11-05 · 联调缺陷文档整理

**改了什么**

- `docs/bug-report-verifier-receipts.md` 重构为按状态分组的缺陷记录：
  状态总览表 + 「已修复(§1–§9) / 遗留(§10–§11) / 已排除(§12)」+ 取证方法 + 修复提交对照
- 删除报告人抬头、"给后端负责人"等沟通性措辞；保留全部可复查证据（run id、调用账本、DB 字段）
- `docs/backend-issues.md`（旧清单）改为归档索引，指向新文件
- README 相关表述中性化；`ModeHint` 注释里的旧章节号 `§十一` → `§2`

**为什么**

用户要求：把缺陷文档整理成有条理的现象/修复/遗留记录，去掉无用语句。

**验收**

- 文档结构：1 张总览表 + 12 个编号条目 + 2 个附录
- 全仓检索：README 中已无"后端负责人"措辞，代码注释无失效章节号
- `tsc` 0、`vite build` 0

---

## v0.38 — 2026-11-05 · 新增组员版《前端使用指南》

**改了什么**

- 新增 `docs/user-guide.md`：主流程（5 分钟跑起来）在前，细节与排障在附录在后
- 覆盖：环境依赖、安装/启动命令、浏览器入口（必须 localhost）、口令与 API Key 配置、
  七页功能速查、第一次对话流程、常见问题、离线回放演示、数据位置、模式差异、导出导入、主题、构建预览、安全须知

**为什么**

用户要把代码交给组员，需要一份可以直接照做的运行与使用说明。

**验收**

- 文档内引用的文件全部存在（frontend-contract / test-plan / bug-report / 上级 README）
- 命令与端口和实际代码一致（5173 dev、4173 preview、8787 桥接、CORS 白名单 localhost）

---

## v0.37 — 2026-11-05 · API Key 弹窗文案与布局调整

**改了什么**

- 「只写不读」徽章移到输入框旁（与标签同行），带 tooltip 说明
- 输入框说明补充"仅支持 DeepSeek API Key（sk-…）"，与后端固定 Provider 的事实对齐
- 修复 JSX 中残留的 markdown 星号（`ApiKeyDialog` / `ModeSwitcher` 各一处），改为 `<b>` 加粗

**为什么**

用户要求：把"只写不读"放在输入框旁边，并在合适位置说明仅支持 DeepSeek API Key。

**验收**

- `tsc` 0、`vite build` 0
- 全仓 TSX 中已无用户可见的 `**` 残留（只剩注释里的）

---

## v0.36 — 2026-11-05 · 适配后端 53484dc（六条缺陷修复）

**后端修复对接**

- 会话：创建即落库（`persist=True`）、官方改名/删除（软删除），不再直写数据库
- 审批：后端按会话隔离，桥接支持 `?threadId=`，前端按当前会话查询，未知会话 404
- 协作详情：改用 `get_run_details()` 公开投影；`usage` 拆分「Provider 实报 / 预留估算」，每次调用带 `usage_source`
- 检索证据：trace `schema_version=2`，前端新增「证据充分性」卡与「候选与判定（逐条）」表（排名/分数/分解/四态/冲突/是否选用），旧记录明确标注无逐条字段
- `bridge/session_admin.py` → `session_transfer.py`（只剩导出/导入）
- 新增 5 条英文错误中文化；错误文案与状态码统一

**验证（未调用模型）**

- 隔离实例全链路：创建/改名/导出/导入/审批/删除/协作详情；usage 拆分实测 22,429 + 16,830
- 真机 smoke（single）：全部通过；single 下协作接口仍 400（符合设计）
- `tsc` 0、`vite build` 0（CSS 34.4 kB / JS 469 kB）

---

## v0.35 — 2026-11-05 · 默认主题改为「随北京时间变化」

**改了什么**

- `lib/theme.ts`：新增 `THEME_STORAGE_KEY` / `readStoredThemeMode()`（默认 `auto`）/ `applyInitialTheme()`
- `AppState`：改用共享读取函数与存储键，删除本地 `THEME_KEY`
- `main.tsx`：在 `createRoot` 前调用 `applyInitialTheme()`，避免首屏闪一下默认深色
- 新用户默认 auto；老用户已保存的选择保持不变

**为什么**

用户要求：默认值设为自动，新用户一进来就是自动模式。

**验收**

- `tsc` 0、`vite build` 0（CSS 34.2 kB / JS 463 kB）
- 无 localStorage 时解析为 auto，并按当前北京时间落到对应主题

**怎么撤**

`readStoredThemeMode()` 的兜底返回值改回 `'dark'`，并移除 `main.tsx` 的 `applyInitialTheme()` 调用。

---

## v0.34 — 2026-11-05 · 浅色主题去蓝（白 + 浅灰）

**改了什么**

- 浅色主题的基础层全部中性化：`--bg #f7f7f8`、`--bg-elev-2 #f1f1f2`、`--bg-sunken #ebebed`、
  `--border #e2e2e4`、`--border-strong #c6c6ca`、文字色去掉蓝调
- `--bg-elev` 保持纯白；强调色（青绿）与语义色不动

**为什么**

用户反馈浅色主题背景偏淡蓝，"蓝应该属于晨光"。

**验收**

- `tsc` 0、`vite build` 0
- 浅色主题无蓝色调背景，晨光主题保持蓝白渐变，两者定位区分清楚

**怎么撤**

还原 `tokens.css` 中 `html[data-theme='light']` 的上述变量值即可。

---

## v0.33 — 2026-11-05 · 主题菜单不透明 + 去编号 + 切换渐变

**改了什么**

- tokens 新增不透明 `--menu-surface`（四主题各一个值）；`.theme-menu` 改用它，移出磨砂玻璃列表
- `ThemeSwitcher` 删除 1–5 序号（连同 `.theme-menu__index` 样式）
- `AppState`：主题切换优先走 `document.startViewTransition()` 整页交叉淡化（460ms），
  回退为 `html.theme-transition` + 全元素 CSS 过渡 480ms；`prefers-reduced-motion` 直接切换；
  自动模式与手动切换共用同一路径
- 新增 `::view-transition-old/new(root)` 动画时长与缓动

**为什么**

用户要求：主题下拉不要半透明；去掉编号；手动/自动切换主题都要渐变而不是突变。

**验收**

- `tsc` 0、`vite build` 0（CSS 34.2 kB / JS 462 kB）
- 产物 CSS 含 `--menu-surface` / `view-transition` / `theme-transition`；`theme-menu__index` 0 残留

**怎么撤**

还原 `.theme-menu` 背景为 `var(--bg-elev)`、恢复编号 span、去掉 AppState 过渡逻辑与两段过渡 CSS。

---

## v0.32 — 2026-11-05 · 晨光/夕阳降饱和，统一磨砂面板

**改了什么**

- tokens：晨光/夕阳的 `--app-bg` 首尾色降到近白（`#e6f0fc` / `#fcefe0`），ambient 光晕透明度 0.12–0.22
- `--bg` / `--bg-elev` / `--bg-elev-2` / `--bg-sunken` 全部改为**带色调的半透明**，
  去掉"纯白面板 + 彩色背景"的割裂感
- 强调色降饱和：晨光 `#3b7dd8`、夕阳 `#c9802f`；语义色同步低饱和重调
- 顶栏加 `className="topbar"`；晨光/夕阳下对 `.topbar / .card / .thread-item / .modal /
  .theme-menu / .thread-menu / .banner / .dag__node` 统一 `backdrop-filter` 磨砂
- 更新预览：`docs/theme-preview.png`

**为什么**

用户反馈：主题颜色太直白、渐变太抢眼；部分方框仍纯白导致"有的地方有颜色有的地方没有"。
目标是"只感受到氛围变化"，不是换成大色块主题。

**验收**

- `tsc` 0、`vite build` 0（CSS 33.4 kB / JS 462 kB）
- 源码无硬编码白色背景（全部走 token）；四主题共用同一套磨砂面板

**怎么撤**

还原 `tokens.css` 的 dawn/sunset 两段与 `components.css` 的磨砂规则即可。

---

## v0.31 — 2026-11-05 · 四主题（含晨光/夕阳渐变氛围）+ 北京时间自动切换

**改了什么**

- 新增 `lib/theme.ts`：四种主题 + `auto` 模式、选项顺序、北京时间时段解析
- `styles/tokens.css`：新增「晨光」（蓝白冷调）与「夕阳」（橙黄暖调）两套完整变量，
  含 `--app-bg`（渐变底）与 `--ambient`（光晕层）；深/浅色保持纯色零回归
- `styles/index.css`：渐变底 + 氛围光层 + `#root` 层叠
- 顶栏主题按钮改为下拉 `ThemeSwitcher`：顺序 浅色 → 深色 → 晨光 → 夕阳 → 随北京时间变化；
  auto 项显示"现在 → 当前命中主题"
- `AppState`：`themeMode`/`theme`/`setTheme`，`<html data-theme>` 与 `data-theme-mode`，
  auto 每 30 秒按北京时间重算
- `Icon` 新增 `sunrise` / `sunset` / `clock`

**为什么**

用户要求：新增夕阳（橙黄）与晨光（蓝白）两个带渐变氛围的主题，四个主题 + 自动模式做成下拉，
自动模式按北京时间切换（4–9 晨光 / 9–16 浅色 / 16–18 夕阳 / 18–4 深色）。

**验收**

- 时段映射实测正确；`tsc` 0、`vite build` 0（CSS 32.6 kB / JS 462 kB）
- 产物 CSS 含 `data-theme=dawn` / `data-theme=sunset` / `--app-bg` / `--ambient` / `.theme-menu`
- 预览：`docs/theme-preview.png`

**怎么撤**

删除 `lib/theme.ts`、`ThemeSwitcher.tsx`，恢复 TopBar 原按钮与 `toggleTheme`，
tokens/index.css 删除 dawn/sunset 块与 `--app-bg`/`--ambient` 使用。

---

## v0.30 — 2026-11-05 · 会话列表头部排版修复

**改了什么**

- `ThreadList` 头部改为两行：第一行「会话 + 数量徽章 + 图标按钮」，第二行灰色提示
- 「导入 / 新建」文字按钮改为 24px 图标按钮（`download` / `plus`），新增 `.icon-btn` 样式
- `Icon` 增加 `download`、`plus` 线稿图标
- 文本 `nowrap` + 提示行 `ellipsis`，窄栏不再挤成竖排

**为什么**

用户反馈会话栏头部文字被挤成竖排。

**验收**

- `tsc` 0、`vite build` 0（CSS 31.2 kB / JS 458 kB）
- 宽度核算：216px 栏内可用 196px，第一行固定元素约 112px，无溢出

**怎么撤**

还原 `ThreadList` 头部结构与 `.thread-head*` / `.icon-btn` 样式即可。

---

## v0.29 — 2026-11-05 · 品牌标识重做（v2）

**改了什么**

- `components/Brand.tsx` 重写：圆角渐变徽标底 + M 主笔/残影 + 渐隐地平线 + 宽字距字标与副标
- `styles/components.css`：重写 `.brand*` 样式（悬停上浮、M 描边重绘动画），删除旧 `.brand__node` 与关键帧
- 修掉 `useId()` 含 `:` 导致 SVG `url(#…)` 可能失效的问题（清洗 id）
- 新增预览资产：`docs/brand-preview.png` / `docs/brand-preview.svg`

**为什么**

组长反馈左上角 logo + 标题太丑、缺设计感。

**验收**

- `tsc` 0、`vite build` 0（CSS 30.6 kB / JS 458 kB）
- 产物 CSS 含 `.brand__word / .brand__tick / .brand__glyph / .brand__horizon / brand-draw`；旧 `.brand__node` 0 残留

**怎么撤**

还原 `Brand.tsx` 与 `.brand*` 样式段即可（只影响顶栏标识）。

---

## v0.28 — 2026-11-05 · P0 收尾：会话导出/导入 + 结构整理

**改了什么**

### 功能
- 新增**会话导出**（会话「⋯」菜单）与**会话导入**（列表头部「导入」按钮），格式 `mapas.session/v1`
- 桥接新增 `GET /api/threads/:id/export`、`POST /api/threads/import`
- `bridge/session_admin.py` 增加 `export_session()` / `import_session()`（受控读写 `conversation.db`，
  单事务、生成新 session_id、绝不覆盖已有会话；上限 5000 条消息）
- 新增 `lib/download.ts`（Blob 下载 + 文件名清洗）

### 结构整理
- 拆分 `CollaborationPage.tsx`：665 → 445 行；抽出 `dagView.tsx`(202) + `tone.ts`(27)
- 删除 `pages/PlaceholderPage.tsx`（7 屏全部真实实现，占位页已无调用）
- 删除 `lib/errorText.ts` 里未使用的 `errorTranslationOnly()`
- 修正 `App.tsx` 过期注释（不再写"协作拓扑为占位"）

### 文档
- README §2.26：导出/导入用法与边界、代码结构整理、**数据位置说明**（git push 不带数据）、进度更新

**验收（不消耗模型调用）**

- 导出→导入往返：12 条消息、首条内容/角色一致、新会话出现在列表
- 非法格式 400、导出不存在会话 404
- `tsc` 0、`vite build` 0（CSS 30.4 kB / JS 458 kB / gzip 133.9 kB）

**怎么撤**

删除导出/导入按钮与两个数据源方法、桥接两个路由与 `session_admin` 两个函数，
恢复 `PlaceholderPage` 与单文件 `CollaborationPage` 即可。

---

## v0.27 — 2026-11-05 · 检索证据页显示记忆正文（不再显示编号）

**改了什么**

- `RetrievalPage`：并行读取 trace 与记忆列表，用 `selected_memory_ids` 只读 join 出正文；
  「本次用到的记忆」卡片显示正文 + 类型/粒度 + 版本号，点击跳到记忆库
- 新增 `toMemoryLite()` 适配真实扁平结构（`memory_id / type / content / granularity`），
  不再假设 `current_revision`
- 左列标题改为"查询扩写"（人话），轮次 ID 缩成小字；「本次检索」技术字段收进「更多信息」
- 顶部说明与空态文案改写为产品语言

**为什么**

用户反馈：证据页只显示 `mem_2aeff57f5750` 这类编号，"不说人话"。根因是检索 trace 只落盘了
记忆 ID，前端此前直接把 ID 当内容展示。

**验收**

- 18 条 trace / 3 条记忆；2 个选中 ID 全部命中并还原正文
- `tsc` 0、`vite build` 0（CSS 30.4 kB / JS 459 kB）

**怎么撤**

还原 `RetrievalPage` 的「选中的证据」卡片与数据加载；删除 `toMemoryLite` 适配。

---

## v0.26 — 2026-11-05 · 协作拓扑页实现（P4）

**改了什么**

### 后端（只读）
- 新增 `GET /api/collaboration/runs/:runId`：run / plan / nodes / role_tasks / artifacts /
  handoffs / model_calls（按角色聚合）/ result_json 快照
- `GET /api/collaboration/runs` 支持 `?all=1&limit=N`：跨会话最近运行
- 新增 `CollaborationRunNotFound` → 404（与参数错误区分）

### 前端
- 新增 `src/features/collaboration/CollaborationPage.tsx`：
  运行选择 → 概览 → **六列 DAG**（节点点击看阶段细节、依赖虚线）→ 失败原因 → 调用分布 →
  证据与交接（折叠）→ 最终回答（折叠）
- `pages.ts` collaboration 就绪度改为 `ready`；`App.tsx` 注册页面
- `types/http/fixture` 新增 `listCollaborationRuns` / `getCollaborationRun`
- CSS 新增 `.dag*`（节点状态色条、依赖虚线、列头）

**为什么**

用户要求实现此前占位的「协作」页；该页是"多 Agent 责任可追踪"的展示面，
必须基于真实 coordination.db 数据，不补造节点。

**验收**

- 真实数据：8 条运行；成功运行 10 调用/39,259 单位；失败运行 3 子任务依赖链正确、
  `COLLABORATION_VERIFICATION_BLOCKED` 正确展示
- 404 / single 模式 400 符合预期
- `tsc` 0、`vite build` 0（CSS 30.1 kB / JS 456 kB）

**怎么撤**

删除 `CollaborationPage.tsx` 与 App/pages 注册；桥接删除两个路由与 `collaboration_runs` /
`collaboration_run_detail` 方法；前端去掉两个数据源方法；CSS 删 `.dag*` 段。

---

## v0.25 — 2026-11-05 · 「口令」语义澄清（免校验/需要填写/已验证）

**改了什么**

- 桥接新增免鉴权探针 `GET /api/auth` → `{tokenRequired, protocolVersion}`
- `probeDataSource` 先探鉴权模式：`--no-token` 显示"本机免校验"，要求口令但本机未存 → `AUTH_REQUIRED`
- `TokenButton` 重写：四态显示（本机免校验 / 已验证 / 需要填写 / 不可用），
  弹层说明"口令 = 本机桥接校验码，不是模型 token，也不是 API Key"
- `errors.ts` 增加 `AUTH_REQUIRED`；`errorText` 增加对应中文解释
- 对话页在 `AUTH_REQUIRED` 时给"去顶栏填口令"的提示，而不是让用户复制启动命令

**为什么**

用户指出：显示「Token: 未设置」有歧义——如果是 `--no-token` 启动，本就无需填写；
另外 token 在本项目里指"词元"，用它指代校验码容易误解，需要更贴切的名字 → 界面统一称「访问口令」。

**验收**

- 无 token 模式：`/api/auth` → `tokenRequired=false`，`/api/meta` 200
- 带 token 模式：`/api/auth` → `tokenRequired=true`；无口令 `/api/meta` 401；带口令 200
- `tsc` 0、`vite build` 0（CSS 28.9 kB / JS 442 kB）

**怎么撤**

删桥接 `/api/auth` 路由与 `_authorized(path)` 的豁免；前端去掉 `checkAuth/hasCredential`
与 `authRequired` 字段，恢复 TokenButton 旧文案。

---

## v0.24 — 2026-11-05 · 顶栏 API Key 按钮三色状态

**改了什么**

- 桥接：`api_key_status()` 增加探活缓存（默认 30s，`?refresh=1` 强制刷新），返回值带
  `probeCached` / `probeAgeSeconds`；更新/删除后清缓存
- `AppState`：新增 `apiKeyStatus` + `refreshApiKeyStatus()`，桥接连上后自动拉取
- `ApiKeyButton`：按状态着色（可用绿 / 不可用红 / 未填入或未校验黄），带状态点与 title 说明
- `ApiKeyDialog`：打开时强制刷新探活；更新/删除后同步全局状态与按钮颜色
- CSS：`.chip--ok` / `.chip--bad` / `.chip--warn`

**为什么**

用户要求：右上角 API Key 按钮要按状态上色（可用绿、不可用红、未填入黄）。

**验收**

- 实测探活：首次 249ms → 缓存 12ms → 强制刷新 97ms；`valid=true`
- `tsc` 0、`vite build` 0（CSS 28.9 kB / JS 440 kB）

**怎么撤**

删 `.chip--ok/-bad/-warn` 三色样式与 `ApiKeyButton` 着色逻辑，AppState 去掉状态与刷新方法，
桥接去掉探活缓存。

---

## v0.23 — 2026-11-05 · 未配置/删除 Key 的降级与引导模板

**改了什么**

### 桥接
- 新增 `ApiKeyMissing` 异常 + 统一文案 `API_KEY_MISSING_MESSAGE`
- `WebBridge` 支持 `app=None` 的**降级启动**：`app` 改为属性，缺失时抛 `ApiKeyMissing`；
  `meta()` 增加 `needsApiKey` 字段；`switch_mode` 在缺 Key 时明确拒绝
- HTTP 异常映射：`ApiKeyMissing` → **503** + `{error: 模板话术, code: API_KEY_MISSING}`
- `delete_api_key`：**立即关闭 Agent 并置空**（删除立刻生效）
- `reload_app()` 支持从降级态恢复
- `main()`：无 Key 时降级启动（仅其它初始化错误才退出），横幅打印 `needsApiKey: true`

### 前端
- `MetaInfo` / `BridgeProbe` 增加 `needsApiKey`；`AppState` 增加全局 API Key 弹窗开关
- `ChatPage`：`needsApiKey` 时渲染引导面板（模板话术 + 去填写 + 重新检测），不发无效请求
- `ModeSwitcher`：缺 Key 时禁用并说明原因
- API Key 弹窗更新/删除后自动 `refreshBridge()`，顶栏状态与对话页立刻同步

**嗯为什么**

用户问：删除 Key 后再对话会显示什么？并希望改成"您还未设置 DeepSeek API Key，请点击右上角 API Key 填入密钥"这样的模板提示。

**验收**

- 隔离实例：无 Key 启动存活 → 503 模板 → 填 Key 恢复 → 删除立刻 503
- `tsc` 0、`vite build` 0（CSS 28.6 kB / JS 439.1 kB）

**怎么撤**

删 `ApiKeyMissing` 与 503 映射，恢复 `__init__(app)` 必填；`main()` 恢复"构造失败即退出"；
前端去掉 `needsApiKey` 面板与相关字段。

---

## v0.22 — 2026-11-05 · API Key 只写管理（顶栏按钮 + 弹窗）

**改了什么**

### 桥接（新增端点，+约 190 行）
- `GET /api/api-key`：配置状态 + `updatedAt` + 用 `GET https://api.deepseek.com/models` 探活（不消耗 token）
- `POST /api/api-key`：校验（sk- 前缀 / 长度 / 无空白）→ 原子写入配置 → `reload_app()` 重建 Agent 生效
- `DELETE /api/api-key`：删除 Key 行 + **清理注释行残留的旧 Key 副本**
- 新增 `WebBridge.reload_app()`：同数据目录同模式重建 app
- 新增 `_scrub_comment_line()`：写入时把注释里的 `sk-…` 替换为 `sk-<removed>`

### 前端
- 新增 `components/ApiKeyDialog.tsx`（按钮 + 弹窗）：可用性徽章、填入时间、新 Key 输入框、
  更新/删除（二次确认）、只写不读说明
- 顶栏主题按钮**右侧**挂载「API Key」按钮
- `types` / `http`（新增 DELETE 支持）/ `fixture`（回放只读拒绝）同步新增三个方法
- `Icon` 增加 `key` 图标；CSS 新增 `.modal*` 弹窗样式与动画

### 安全清理
- 发现真实配置第 1 行注释里粘着明文 Key，已抹除（现仅剩有效 Key 行，UTF-8 无 BOM）

**嗯为什么**

用户要求：顶栏加"更新 API Key"，弹窗显示可用性/填入时间、可覆盖更新、可删除，且页面无法回显 Key。

**验收**

- 隔离实例全流程通过：状态/更新/删除/非法 Key/无 Key 片段泄漏
- `tsc` 0、`vite build` 0（CSS 28.2 kB / JS 437.6 kB / gzip 128.1 kB）
- 真实实例 `GET /api/api-key`：`configured=True valid=True`

**怎么撤**

删除 `ApiKeyDialog.tsx` 与 TopBar 引用；桥接删除 `api-key` 路由、三个方法与 `reload_app`；
`types/http/fixture` 去掉三个方法；CSS 删 `.modal*` 段。

---

## v0.21 — 2026-11-05 · 火苗动效加强 + 时钟 + 报错中译 + 协作横幅按次提醒

**改了什么**

- `FlameLoader`：主焰 8 段不规则关键帧、顶焰独立快拍、底部余光、三颗火星、外发光脉动
- 新增 `components/layout/Clock.tsx` + `.clock*` 样式：左下角北京时间（秒）+ 日期 + UTC+8 标签，
  固定 `Asia/Shanghai`，整秒对齐
- 新增 `lib/errorText.ts`：错误代码/英文消息/前缀三类映射；`Banner` 自动翻译字符串 children；
  对话页错误横幅、时间线警告与失败原因、顶栏与检查器提示都接入
- `AppState`：新增 `collabHintDismissed` / `dismissCollabHint`；`switchMode` 成功后重置
- `ModeHint`：去掉 localStorage 永久关闭；每次切到 multi_fixed 重新提醒，文案强调 token 消耗

**为什么**

用户反馈：火苗动效很假；需要在左下角看北京时间；红色/黄色报错看不懂；协作模式的
高 token 消耗提醒应该每次切换都出现（可关闭但只到下次切换）。

**验收**

- `tsc` 0、`vite build` 0（CSS 24.5 kB / JS 431 kB）
- 产物 CSS 含 `.clock__time` / `flame-flicker` / `flame-spark` / `flame-glow`
- 全仓 squirrel 命中 0

**怎么撤**

删除 `Clock.tsx` / `errorText.ts` 及对应 import；还原 `FlameLoader` 的动画与火星；
`AppState` 去掉两个字段；`ModeHint` 恢复 localStorage 方案。

---

## v0.20 — 2026-11-05 · 加载动效改用 OpenMoji 线稿 🔥（token 燃烧中）

**改了什么**

- 新增 `src/components/FlameLoader.tsx`：内联 OpenMoji black 线稿 🔥（U+1F525，CC BY-SA 4.0），
  按语义切分三组（底座 / 主焰 / 顶端火苗）分别加关键帧
- 尺寸从 30–32px 缩小到 **17px**（比汉字略大）
- 文案从「正在深度求索…」改成 **「token 燃烧中…」**
- 删除 `SquirrelLoader.tsx` 与 `.squirrel*` 全部样式（全仓无残留）
- 颜色改用 `--sem-blocked` 橙色表达燃烧

**为什么**

用户反馈：自绘松鼠太丑、动画太大、文案不该借用第三方产品的说法。

**验收**

- `tsc` 0、`vite build` 0（CSS 22.7 kB / JS 427 kB）
- 产物 CSS 含 `.flame` / `flame-sway` / `flame-tip` / `.dots`
- 全仓 `squirrel` 命中 0

**许可说明**

线稿素材来自 OpenMoji（CC BY-SA 4.0），署名与改动说明写在组件文件头与本记录中；
分发时需保持相同许可。

**怎么撤**

删除 `FlameLoader.tsx`，恢复 `SquirrelLoader.tsx` 与对应 CSS/引用即可。

---

## v0.19 — 2026-11-05 · 发送即显示 + 松鼠动效 + 默认滚底

**改了什么**

- `useChatSession`：新增乐观渲染 `pendingMessage`（发送瞬间可见用户消息；后端落库后自动让位）；
  作业结束/切会话时清空
- `TurnTimeline`：末尾渲染「待回显消息 + 生成中」；新增 `suppressGenerating` 避免两只松鼠
- `SquirrelLoader`：美术与动效重做（双爪交替、尾巴两段、眨眼、星光、四粒碎屑），
  新增 `GeneratingIndicator`（松鼠 + 「正在深度求索…」+ 跳动省略号）
- `ChatPage`：打开/切换会话默认滚到最新；仅在底部时跟随新内容；新增「↓ 最新」悬浮按钮
- `styles/components.css`：`.generating` / `.dots` / `.to-bottom` / 松鼠关键帧补齐

**为什么**

用户要求：发出的消息要立即可见；加载动画要放在"回答将要出现的位置"并更生动（参考 DSH 小虎鲸）；
会话默认显示最新几条而不是最早几条（像微信）。

**验收**

- `tsc` 0、`vite build` 0（CSS 23.8 kB / JS 428.7 kB）
- 产物 CSS 含 `.squirrel` / `sq-blink` / `.dots` / `.to-bottom`

**怎么撤**

还原 `useChatSession`（去掉 pendingMessage）、`TurnTimeline` 末尾块与 suppressGenerating、
`ChatPage` 滚动逻辑、`SquirrelLoader` 与对应 CSS 段。

---

## v0.18 — 2026-11-05 · 轮次编号修正 + 松鼠加载动画

**改了什么**

- `eventModel.ts`：无 `turn_id` 的会话级事件不再制造 `'no-turn'` 空块；过滤空块
- `TurnTimeline.tsx`：轮次编号改为「按用户发言次数」（含用户消息才算新一轮，续跑沿用上一号）；
  新增生成中占位行（松鼠 + 「正在翻找记忆、执行工具…」）
- 新增 `src/components/SquirrelLoader.tsx`：纯 SVG + CSS 的松鼠翻筐子动画（尾巴/前爪/脑袋/筐/碎屑），
  跟随主题色、`prefers-reduced-motion` 静止
- `ChatPage.tsx`：顶栏「执行中」指示器换成 18px 松鼠（滚动时也能看到动效）
- `styles/components.css`：新增 `.squirrel*` 与 5 组关键帧

**为什么**

用户反馈：新建会话会莫名出现一轮「#1 UNKNOWN」，希望编号符合"第几句来回就是第几轮"；
并希望生成时有一个"松鼠翻筐子"的加载动画（参考主流 Agent 体验）。

**验收**

- `tsc` 0、`vite build` 0（CSS 22.4 kB / JS 426 kB / gzip 124 kB）
- 新建会话事件流确认只有 `thread.started`（`turn_id=null`）→ 不再渲染空轮次
- dev server 已提供新模块

**怎么撤**

还原 `eventModel` 的 turnId 处理与空块过滤、`TurnTimeline` 的编号与占位行、
删除 `SquirrelLoader.tsx` 与 CSS `.squirrel*` 段、`ChatPage` 顶栏恢复 `dot`。

---

## v0.17 — 2026-11-05 · 会话栏撑满 + 会话菜单不被裁剪

**改了什么**

- `styles/components.css`：`.app-grid` 增加 `grid-template-rows: minmax(0, 1fr)`；
  `.app-main--flush` 增加 `height: 100%; min-height: 0`；新增 `.thread-menu.thread-menu--fixed`
- `ChatPage.tsx`：根网格加 `gridTemplateRows: 'minmax(0, 1fr)'`，中栏 `height: 100%`
- `ThreadList.tsx`：外层 `height: 100% + overflow: hidden`；列表区 `flex: 1; minHeight: 0; alignContent: start`；
  菜单改为固定定位（锚点由按钮 rect 计算、下方不足时上弹、滚动/缩放自动关闭）

**为什么**

用户反馈：会话栏高度随会话数量变化、没有从顶到底铺满；会话「⋯」菜单被列表容器裁剪，要看完整选项得滚轮。

**验收**

- `tsc` 0、`vite build` 0（CSS 21.1 kB）
- 产物 CSS 含 `grid-template-rows:minmax(0,1fr)`、`.thread-menu.thread-menu--fixed`

**怎么撤**

还原上述三处 CSS 与两个组件的对应改动即可（`v0.16` 的会话菜单功能不受影响）。

---

## v0.16 — 2026-11-05 · 四项修复：幽灵会话 / 会话菜单 / 警告降噪 / 界面可读性（本轮）

**改了什么**

### 桥接
- **会话创建即落库**：`create_thread` 调用后端 `_ensure_persisted_session()`，根治幽灵会话；
  删除整套 pending 注入逻辑（`-40` 行特殊分支）
- 新增 `POST /api/threads/:id/rename`、`POST /api/threads/:id/delete`
- 新增 `bridge/session_admin.py`：受控写 `conversation.db`（单事务、只碰 3 张表、不级联删事件日志），
  文件头注明"后端提供正式接口后应替换"
- 新增警告降噪：`BENIGN_WARNINGS` + `clean_event()` / `clean_serialized_result()`

### 前端
- `ThreadList` 重写：每行加「⋯」菜单（重命名 / 删除会话 / 导出会话-暂未实现），
  内联编辑 + 删除二次确认，操作失败显示真实原因
- `useChatSession` 新增 `renameThread` / `deleteThread`；删除当前会话时清空选中态
- `types` / `http` / `fixture` 同步新增 rename/delete（回放模式明确报只读）
- 新增 `components/ui/feedback.tsx`（`Banner` / `Hint` / `MoreInfo`）；5 个业务页面删除重复实现改为委托
- `Card` 新增 `collapsed` 属性；任务/上下文/证据/治理页各折叠 2 个次要卡片
- 5 个非对话页面标题下增加一句灰色人话解释；记忆页把 ID/置信度/来源数折进「更多信息」
- `styles/components.css` 新增 `.banner` / `.hint` / `.moreinfo` / `.card__fold` / `.thread-menu`

**为什么**

用户反馈：① 幽灵会话仍未修复；② 会话需要重命名/删除/导出入口；③ 正常对话出现
`This turn has no Task 3 binding.` 噪音警告；④ 非对话页面信息晦涩、需要解释与折叠。

**验收**

- 真机：新建即 ACTIVE；建 A→建 B→切回 A 返回 200；重命名/删除生效；删除不存在 404；空标题 400
- 警告：宿主那条已从 result 与 event 移除，真实警告保留
- `tsc` 0、`vite build` 0（CSS 20.9 kB / JS 423 kB / gzip 123 kB）
- Git：仅 `?? code/web/`，无 M/D

**怎么撤**

- 桥接：恢复 `create_thread` 为懒创建（去掉 `_ensure_persisted_session`），删 rename/delete 路由与 `session_admin.py`，
  删 `BENIGN_WARNINGS` 与两个 clean 方法
- 前端：还原 `ThreadList`、`useChatSession`、`types`、`http`、`fixture`；删 `feedback.tsx`；
  `Card` 去掉 `collapsed`；还原 5 个页面的 Hint/折叠改动

---

## v0.15 — 2026-11-05 · 模式热切换 + 幽灵会话修复（本轮）

**改了什么**

### 桥接（`web_bridge.py` 当前 1,861 行）
- `POST /api/mode` / `GET /api/mode`：模式热切换（同数据目录重建 app，token 不变、数据不丢）
  - 有作业 QUEUED/RUNNING → **409** + 中文原因
  - 新 app 构造失败 → **回滚**旧模式，桥接不整体挂掉
  - 切换后清空进程内 pending 会话登记
- `resume_thread`：未落库但仍是当前活动会话 → 返回 **200 + PENDING**（修掉 404）

### 前端
- 新增 `src/components/layout/ModeSwitcher.tsx`：顶栏「模式：single / 协作」下拉
- `AppState`：新增 `switchMode()` 与 `epoch`；切换成功后 epoch+1
- `App.tsx`：`PageHost key={view-epoch}` → 模式切换后整页重挂载，避免旧模式状态（含幽灵会话）残留
- `useChatSession`：PENDING 会话不调 resume；`loadThreads` 剪枝不存在的活动会话；切换失败时刷新列表
- `ModeHint`：从"复制命令"改为**一键切换到 single**
- `GovernancePage`：按 `capabilities` 预判，single 下不再发必然 400 的请求（改为解释卡 + 一键切协作）

**为什么**

用户要求：模式要能在**每轮对话之间切换**以适配一任务内的不同需求；并反馈新建会话会出现
"未找到会话…404"、切走后变成切不回来的幽灵会话。

**验收**

- 真机：`GET/POST /api/mode` 正常；single→multi→single 均 200；切换后 token 不变；
  有作业时切换返回 409；惰性会话 resume 返回 200+PENDING；旧惰性会话从列表消失
- `tsc` 0 / `vite build` 0（JS 420 kB，gzip 122 kB）
- Git：仅 `?? code/web/`，无 M/D

**怎么撤**

- 桥接：删除 `/api/mode` 路由与 `has_active_jobs` / `switch_mode` 方法，恢复 `resume_thread` 旧写法
- 前端：删 `ModeSwitcher.tsx`；还原 `AppState`（去掉 switchMode/epoch）、`App.tsx`、`useChatSession`、
  `ModeHint`、`GovernancePage` 的对应改动

---

## v0.14 — 2026-11-05 · 界面优化三项（本轮）

**改了什么**

1. **产品标识重做**：新增 `src/components/Brand.tsx`（三层记忆标记 + 字标 + 中文副标），
   替换 `TopBar` 里原来的"圆角方块 + M + 学术副标题"；悬停上浮、点击回对话页
2. **对话时间线重做**（`src/features/chat/TurnTimeline.tsx` 全量重写）
   - 执行中：紧凑等宽日志逐行显示（`.logline`）
   - 已完成：过程自动折叠成一行摘要（`.processbar`，可点开/收起）
   - 待审批 / 错误 / 警告**永不折叠**
   - 新增「产出」行：从 create_file / apply_patch 的真实参数提取文件路径
   - 新增「协作」徽章：由 `turn.completed.trace_metadata.collaboration_run_id` 结构化判断
3. **协作模式提示条**：新增 `src/features/chat/ModeHint.tsx`，`multi_fixed` 下提示"简单请求也会被拆子任务"，
   附切到 `single` 的命令（可复制、可关闭并本机记住）
4. `eventModel.ts`：`TurnBlock` 增加 `collaborationRunId` 与 `outputs`；在 `turn.completed` 解析协作标识
5. `styles/components.css`：新增 `.brand*` / `.logline*` / `.processbar*` 样式
6. 文档：缺陷报告新增「问题 5：多 Agent 把子任务自述当最终回答」（含证据与建议）；README 新增 §2.12

**为什么**

用户反馈：① 左上角标识"AI 味重"；② 日常问候被回成系统自述；③ 对话中间日志占版面太大。
①③ 是前端可改的体验问题，已重做；② 经查是后端响应组装问题（`user_response` 为空、
`final_response` 是子任务完成描述），前端只能加提示与徽章，不能篡改数据。

**验收**

- `tsc` exit 0；`vite build` exit 0（CSS 16.8 kB / JS 415 kB，gzip 121 kB）
- 构建产物 CSS 中存在 `.brand__word` / `.brand__tier` / `.logline` / `.processbar` / `.processbar__chev`
- 真机（dev server HMR）可用：标识、折叠日志、协作提示条均已生效

**怎么撤**

- 删除 `src/components/Brand.tsx`、`src/features/chat/ModeHint.tsx`，还原 `TopBar.tsx`、`TurnTimeline.tsx`
- `eventModel.ts` 去掉 `collaborationRunId` / `outputs` 两个字段与其赋值
- `components.css` 删除 `.brand*` / `.logline*` / `.processbar*` 段

---

# CHANGELOG — code/web（前端层）

> 只记录**本层**的改动。每次改动必须能回答三件事：改了什么、为什么、怎么撤。
> 全部改动都在 `code/web/` 内（bridge 也已批准放在 `code/web/bridge/`），
> 因此总回滚始终是：`git -C D:\projects\nlp- clean -fd code/web`

## v0.13 — 2026-11-05 · 手动验收流程表（本轮）

**改了什么**

- 新增 `docs/test-plan.md`：分 5 阶段的手动验收流程（每步含操作 / 预期结果 / 花费 / 失败排查）
  1. 零成本验收（15 项，覆盖 token、主题、7 屏只读接口、模式感知、跨页跳转、Console 干净）
  2. 低成本对话验收（含工具卡 + **审批确认全链路** + 文件真实生成）
  3. 记忆 / 任务 / 上下文验收（写入、版本、来源、回收站、建任务、压缩、真实用量）
  4. 协作与治理验收（`multi_fixed` 模式切换、**预期失败**的错误如实呈现、协作图节点 7 态、经验 learn 如实失败）
  5. 离线与容错验收（断连报错、重试恢复、`?src=fixture` 回放、回放只读拒绝）
- 附"通过标准汇总"与"先分清前端还是后端的定位表"（含绕开界面的 curl 命令）
- README 第 6 章新增指向该流程表的说明

**为什么**

用户需要一份能自己照着做、且**不浪费 token** 的验收流程；
另外必须写清"对话在 single 测、协作在 multi_fixed 测"，否则 multi_fixed 下普通闲聊也会撞上
已知后端缺陷（`COLLABORATION_VERIFICATION_BLOCKED`），容易被误判为前端 bug。

**预算**

全流程约 21 次模型调用、≈0.035 元（空闲时段）、≈24 分钟。

**怎么撤**

- 删除 `docs/test-plan.md`，还原 README 第 6 章那一行

---

## v0.12 — 2026-11-05 · 后端缺陷报告（本轮）

**改了什么**

- 新增 `docs/bug-report-verifier-receipts.md`（186 行）：可直接转发给后端负责人的独立缺陷报告
  - §0 简短版（可贴群/私聊）
  - §三 复现步骤（桥接命令 + 两条 HTTP 请求）
  - §四 两次运行的**完整调用账本**（角色/操作/状态/units/token；12 次与 7 次）
  - §五 `coordination.db` 里的交付链证据（task_attempts / artifacts / handoffs）
  - §六 初步定位线索（3 条假设 + 对应代码位置）
  - §七 修复后的验证方式（离线回归 + 手工验收）
  - §九 三个次要问题（检索明细未落盘 / approvals 过滤 / 模型回 JSON）
- README §2.11 遗留问题处新增指向该报告的链接

**为什么**

用户需要把"协作验证被阻断"这条阻塞性缺陷反馈给后端负责人。
之前的 `backend-issues.md` 偏前端视角，这份报告自包含、可独立阅读、带可执行复现与可核对的 DB 证据。

**关键证据（报告核心）**

- run `ma_3a6caa8bfa012db281f02894`：12 次调用 / 47,685 units；`read_readme` attempt+artifact 均 VERIFIED，
  `summarize_40` handoff/artifact 齐全，但 `result.executed_tool_call_ids` 只剩 1 个、`source_reads=[]`
- run `ma_fa87f481e772eada8c4de8ba`：7 次调用 / 44,947 units；`executed_tool_call_ids=[]`、`source_reads=[]`
- 两次 Executor 失败调用占 19,966 / 28,035 units（单轮预算 42% / 62%），且 `usage_json` 为空

**怎么撤**

- 删除 `docs/bug-report-verifier-receipts.md`，还原 README §2.11 那一行链接

---

## v0.11 — 2026-11-04 · P5/P6 实现完成（本轮）

**改了什么**

- 新增 `bridge/record_fixtures.py`（169 行，纯标准库 urllib）：把 bridge 的真实响应**原样**录成
  `src/datasource/fixtures/*.json`，按 `fixture.ts` 期望的读取形状保存（列表存数组、对象存对象）
- 录制产出：**31 个 fixture / 93.9 KB**（会话、消息、70 条事件、记忆及版本/来源、任务与闭环、
  5 条检索 trace、工具目录、工作区、存储、审批、修复、经验、协作图）
- 新增 `docs/backend-issues.md`：遗留问题清单（4 条，含复现命令、实测 token/调用数、错误码、建议动作）
- 重写 `src/datasource/fixtures/README.md`：录制流程、文件命名对照表、两点注意（bundle 体积 / 测试数据）
- README 新增 §2.11（P5/P6 交付、全量接通校验、遗留问题、交付规模）、更新目录树与状态行

**为什么**

P5 的目标是"离线也能演示 + 演示不依赖明文 token"；P6 的实现部分是交付文档与遗留问题交接。
按用户指示**不做**截图/录屏/PPT 素材。

**验收**

- `tsc` exit 0；`vite build` exit 0（44 模块，CSS 14.5 kB / JS 410 kB，gzip ~124 kB）
- fixture 形状校验脚本逐文件通过（数组/对象类型 + 必需键全部匹配 `fixture.ts`）
- 桥接 20 个 GET 端点 200；`/api/traces` 5 条真实记录；错误路径 404
- 离线入口：`http://localhost:5173/?src=fixture`（回放模式写操作抛 `OFFLINE_FIXTURE_READ_ONLY`）

**已知代价**

- fixtures 经 `import.meta.glob(eager)` 打进 bundle → JS 由 334 KB 增至 410 KB（gzip ~124 KB）；如需精简可改懒加载
- fixtures 含测试数据（一条"会议时间偏好"记忆），已确认无隐私内容

**怎么撤**

- 删 `bridge/record_fixtures.py`、`src/datasource/fixtures/*.json`（保留其 README）、`docs/backend-issues.md`
- 还原 README 状态行与 §2.11

---

## v0.10 — 2026-11-04 · 证据降级视图 + P5 token 鉴权（本轮）

**改了什么**

### 后端（`bridge/web_bridge.py` 1,734 → 1,791 行）

- `GET /api/traces?limit=N` —— 读取真实落盘的 `execution/retrieval-traces.jsonl`（倒序，只读文件）
- `GET /api/turns/:turnId/trace` —— 单条 trace；未知 → 404 + 中文
- 两者都不走 worker（纯文件只读，避免长轮次阻塞浏览）

### 前端

- 新增 `src/features/retrieval/RetrievalPage.tsx`：**降级证据视图**
  - 能显示：召回阶段、查询扩写、每条查询的候选数、适用性评估计数、选中的记忆 ID
  - 显示不了：逐条排名/分数/四态/冲突/充分性 → 统一标「—（后端未持久化）」，并在页面写明根因与补救
  - 选中记忆可点击 → 跳转记忆库并预选
- 新增 `src/components/layout/TokenButton.tsx`（P5）：顶栏输入桥接 token，存 localStorage，自动重连；
  不使用 `VITE_BRIDGE_TOKEN`（会被编译进 bundle）
- 修改：`types/index.ts`（`listTurnTraces`）、`datasource/http.ts`、`datasource/fixture.ts`、
  `state/AppState.tsx`（`pendingMemoryFocus`）、`features/memory/MemoryPage.tsx`（消费焦点）、
  `app/App.tsx`（检索路由）、`app/pages.ts`（检索就绪度 → ready）、`layout/TopBar.tsx`（接入 Token 按钮）

**为什么**

- 证据屏的逐条字段后端未落盘，按"功能不造假"降级为真字段视图，并把缺失项**显式标注**，等后端补字段即可升级。
- P5 的第一步是把桥接 token 鉴权接通；正式演示不应跑在 `--no-token` 上。

**验收**

- `tsc` exit 0；`vite build` exit 0（44 模块，JS 334 kB / gzip ~101 kB）
- `/api/traces` → 5 条真实记录；`/api/turns/:id/trace` → 200；未知 turn → 404
- 全量核验：23 个关键文件存在；20 个 GET 端点全 200；3 条错误路径均 404；
  Git 仅 `?? code/web/`；无 `__pycache__`、无探针文件、无残留进程

**怎么撤**

- 前端：删 `src/features/retrieval/`、`src/components/layout/TokenButton.tsx`，
  还原 `App.tsx`/`pages.ts`/`AppState.tsx` 的对应改动
- 后端：删 web_bridge.py 里 traces 路由与 `retrieval_traces` / `retrieval_trace` 两方法

---

## v0.9 — 2026-11-04 · P4 治理中心（本轮）

**改了什么**

### 后端（`bridge/web_bridge.py` 1,475 → 1,734 行）

- 修复：`GET /api/repairs`、`GET /api/collaboration/runs`、`GET /api/repairs/:repairId`（不存在 404）、
  `POST /api/repairs/:runId/:eventId/run`、`POST /api/repairs/revoke`、`POST /api/repairs/drain`
- 经验：`GET /api/experiences`、`POST /api/experiences/{learn,review,revoke,feedback}`
- `single` 模式下协作类端点统一返回 400 + 中文提示（不返回空数组、不抛 500）

### 前端

- 新增 `src/features/governance/GovernancePage.tsx`：5 个分区（审批 / 修复 / 经验 / 工具目录 / 环境）
- 修改 `src/app/App.tsx`：治理页接入；`src/app/pages.ts`：治理就绪度 → `ready`

**为什么**

P4 的定位是"把失败处理做成可见界面"：修复的影响范围与局部重跑、经验的 7 态与撤销、
副作用操作的人工确认。这些能力后端已实现，缺的是入口与展示。

**验收**

- `pnpm typecheck` exit 0；`pnpm build` exit 0（42 模块，JS 322 kB）
- 真机：`repairs / collaboration/runs / experiences` 均 200；`/api/repairs/nope_123` → 404；
  tools=12（3 个 SIDE_EFFECT）；workspace/storage 正常；既有端点回归通过
- ⚠️ 治理页当前多为空态：修复与经验需要**成功通过核验的协作运行**，
  而 `multi_fixed` 因后端证据传递问题被 Verifier 阻断（见 v0.8）。后端修复后无需改前端即可出数据

**形状对齐（本轮补充修正）**

- `learn` 的 **ABSTAIN 分支**（`{"decision":"ABSTAIN","reason":…}`）原来会被当成"已完成"，已改为如实显示「Provider 弃权：原因」，不与"写入成功"混淆。
- `GET /api/repairs/:repairId` 返回的是 repository 原始 `plan_json`（无 `affected` 字段），
  前端按真实字段渲染；`S/Desc(S)/A` 只在后端提供时显示。
- `drain` 作业 `result` 是数组而非 `AgentResult`，前端不解析 result，只看作业状态。

**怎么撤**

- 前端：删除 `src/features/governance/`，`App.tsx` 去掉 governance 分支，`pages.ts` 改回 `waiting-bridge`
- 后端：删除 web_bridge.py 中 repairs/experiences/collaboration 路由与对应 WebBridge 方法

---

## v0.8 — 2026-11-04 · P3 真实数据验证 + 两处前端修正（本轮）

**改了什么**

- `src/features/chat/eventModel.ts` 两处修正：
  1. 工具事件按 `tool_call_id` 建立**全局索引**去重（真实流里 `tool.call.completed` 可能先于 `tool.started`）；
  2. 审批事件按 `pending_action_id` 建立**跨 turn 索引**（确认发生在新的 confirmation turn 里，需原地更新原卡片）。
- `README.md` 新增 §2.8：真实数据验证结果、V1 结论、4 个实测问题、2 处修正

**为什么**

真机跑通后发现两处界面级缺陷（重复工具卡、已审批卡片仍可点），以及一个必须记录的后端事实
（检索证据明细未落盘），这些只有跑真实数据才会暴露。

**实测花费**

30 次模型调用 / 64,012 输入 / 7,085 输出 tokens ≈ **0.09 元**（空闲时段）

**验收**

- `pnpm typecheck` exit 0；`pnpm build` exit 0（41 模块，CSS 14.5 kB / JS 302 kB）
- 真机验证通过：对话阶段卡/工具卡/错误如实、**审批确认后文件真被创建**、记忆写入与版本/来源字段、
  任务创建（0 调用）、上下文窗口与用量、协作 12 次调用的角色明细
- ⚠️ V1 结论：检索证据明细（rank/score/四态/冲突/充分性）**没有落盘**，证据包面板只能做降级版
- ⚠️ `multi_fixed` 两轮均被 Verifier 以 `COLLABORATION_VERIFICATION_BLOCKED` 阻断（后端证据传递问题，需反馈组长）

**怎么撤**

- 代码：`git checkout` 不适用（未入库）；撤销两处修正即回到"单轮查找"逻辑。
- 文档：删除 README §2.8 与本节即可。

---

## v0.7 — 2026-11-04 · P3 三个可视化面板（本轮）

**改了什么**

### 后端（`bridge/web_bridge.py` 1,186 → 1,475 行）

- 长期记忆 9 个端点：`memories`(list)、`memories/recycle-bin`、`memories/profile`、`memories/:id`、
  `memories/:id/history`、`memories/:id/provenance`，以及 `POST memories`（显式写入）、`forget`、`restore`
- 任务 8 个端点：`GET/POST tasks`、`tasks/:id/switch`、`tasks/status`、`tasks/current/{pause,resume,complete,cancel}`
- 闭环 2 个端点：`GET task-lifecycle`（顶层对象）、`POST task-lifecycle/drain`
- 上下文 1 个端点：`POST context/compact`（202 + 异步作业）
- 固定路径（`recycle-bin` / `profile` / `tasks/status` / `tasks/current/*`）均先于 `:id` 匹配

### 前端（3 屏 + 2 个工具模块）

- `src/features/memory/MemoryPage.tsx` —— 记忆库：版本时间轴、来源溯源、回收站、忘记/恢复、显式写入
- `src/features/tasks/TaskPage.tsx` —— 任务台账：五组条目、检查点、任务级闭环、任务操作
- `src/features/context/ContextPage.tsx` —— 上下文仪表盘：预算条、投影占用、压缩检查点、手动压缩
- `src/lib/useAsync.ts` —— 读一次+刷新的统一 hook（卸载安全、竞态安全、错误不吞）
- `src/lib/format.ts` —— 时间/时长/比例/整数格式化
- 修改：`src/app/App.tsx`（三屏路由）、`src/app/pages.ts`（三屏标为 `ready`）、`README.md`（新增 §2.7）

**为什么**

- 记忆/任务/上下文三屏的字段在 `PersonalAgentApp` 中**已确定存在且不依赖模型调用**，可以做到"真数据 + 可真验证"。
- 证据包面板被 **V1**（检索 trace 字段是否持久化）阻塞；DAG 面板需要协作运行记录 + 新端点。
  字段未确认就画图 = 做出字段对不上的界面，所以先做能真验证的三屏。

**验收**

- `pnpm typecheck` exit 0；`pnpm build` exit 0（40 模块，CSS 14.5 kB / JS 300 kB，gzip 93 kB）
- 独立集成实测（隔离数据目录 + 占位密钥 + 独立端口）：
  - memories / recycle-bin / profile → 200，空集合形状正确
  - `POST /api/tasks` → `taskId`，列表可见 `ACTIVE`
  - `GET /api/tasks/status` → **真实数据**（`goals:["P3 integration smoke"]`、`checkpoint:null`、`workspace` 齐全）
  - `GET /api/task-lifecycle` → `{enabled:true,tasks:[],jobs:[]}` 顶层对象
  - `POST /api/context/compact` → 202 + jobId，作业 **SUCCEEDED**
  - `GET /api/context` → `window=1,000,000`、`usable=771,808`
  - `POST /api/memories`（占位密钥）→ **502 + 真实 401 原因**，未伪装成功
  - chat 端点回归通过；未知路径 404

**未做**：检索证据包（V1 阻塞）、协作 DAG（待 P4 + 新端点）

**修正（P0 契约与实测不符）**

第一版记忆库按 P0 契约假设了 `MemoryItem + current_revision` 嵌套结构，实测后端返回扁平/分组字典，按真实结构重写：

- `list_memories()` 只有 `{memory_id,type,content,granularity}` 4 个字段；
- `get_memory_detail()` 是扁平字段（`revision_id/retention/confidence/semantic_validity/provenance_count`）；
- `get_memory_history()` 只有 `{revision_id,content,created_at}`，**没有**版本号与变更类型；
- `get_memory_provenance()` 按版本分组返回 `{revision_id,revision_no,change_kind,refs[]}`。

新增 `src/features/memory/memoryModel.ts` 做视图模型与适配（history × provenance 按 `revision_id` 关联），
界面不再引用不存在的字段；缺失版本号时显示"无版本号"而不猜测。README §2.7 已记录该差异。
`docs/frontend-contract.md` 的 §2.5 仍是 P0 假设字段名，等确认后再改（它是冻结基线）。

**怎么撤**

- 三屏：`Remove-Item -Recurse code/web/src/features/memory,code/web/src/features/tasks,code/web/src/features/context`，
  并把 `src/app/App.tsx` 改回全部占位、`pages.ts` 的 `ready` 改回 `waiting-bridge`
- 后端：删除 `web_bridge.py` 中新增的记忆/任务/闭环/compact 路由与 `WebBridge` 对应方法
- 整体：`git clean -fd code/web`

---

## v0.6 — 2026-11-04 · P2 对话工作台 + bridge（本轮）

**改了什么**

### 新增后端（操作区内，未碰 `code/scripts/`）

- `bridge/web_bridge.py`（1,186 行，纯标准库）
  - 串行 worker 执行所有 `PersonalAgentApp` 调用（该类非线程安全）
  - 异步作业：`POST turns` / `POST approvals/:id/decision` 立即返回 `jobId`，`GET /api/jobs/:id` 轮询
  - 端点：`meta`、`threads`(list/create/resume/messages/unfinished)、`events`、`events/stream`(SSE)、`status`、`approvals`、`tools`、`context`、`mcp`、`workspace`、`storage`
  - 安全：仅 `127.0.0.1`、一次性 token、CORS 白名单 5173/4173
  - 诚实性：启动失败 / Provider 失败 / 未知路径一律非 2xx + 中文原因

### 新增前端

- `src/features/chat/eventModel.ts` —— 事件流 → 时间线纯函数归约（未知事件保留显示）
- `src/features/chat/useChatSession.ts` —— 会话状态机：作业轮询 + 增量读事件 + 审批决策
- `src/features/chat/ChatPage.tsx` —— 页面装配（会话列表 / 时间线 / 输入框 / 连接错误提示）
- `src/features/chat/ThreadList.tsx` —— 会话列表
- `src/features/chat/TurnTimeline.tsx` —— 消息气泡 + 工具卡 + **审批卡（确认/拒绝）** + 阶段卡 + 压缩卡
- `src/features/chat/Composer.tsx` —— 输入框（Enter 发送 / Shift+Enter 换行 / 执行中禁用并说明原因）

### 修改

- `src/app/App.tsx`：对话页接真实组件，其余六屏仍占位；`AppShell` 支持 `flush` 全高布局
- `src/app/pages.ts`：新增 `ready` 就绪度，对话页标记为已实现
- `src/components/layout/SideNav.tsx` 与 `Inspector.tsx`：就绪度三态（已实现 / 等桥接 / 待验证 V1）配色
- `src/styles/components.css`：新增 `.thread-item`、`.event-row`、`.app-main--flush`
- `README.md`：新增 §2.6（P2 交付与集成验证）、更新目录树/运行方式/决策状态

**为什么**

- P2 的出口是“真跑通一轮对话”，而 JSONL 宿主缺审批决策等入口，所以 bridge 必须与 P2 同时交付。
- 时间线必须由**真实事件流**归约而来，不能前端编步骤，否则违背“功能不造假”。
- 轮询同时拉消息与事件：让“用户提问/助手回答”尽早出现、阶段与工具卡逐项出现。

**验收**

- `pnpm typecheck` exit 0；`pnpm build` exit 0（35 模块，CSS 13.9 kB / JS 273 kB，gzip 86 kB）
- bridge 集成实测（占位密钥、隔离数据目录、独立端口）：
  - token 鉴权：无/错 token → 401，正确 → 200
  - `meta` / `threads` / `messages` / `events` / `status` / `approvals` / `tools` / `context` / `mcp` / `workspace` / `storage` 形状与 `http.ts` 一致
  - 发起一轮 → 立即得 `jobId` → 作业 `FAILED`（`PROVIDER_REQUEST_ERROR … 401`）→ **失败如实上报**
  - 事件序列 `thread.started → turn.started → item.completed → turn.completed → turn.usage`
  - SSE `curl -N` 可收到流式事件
- ⚠️ **未达成**：真跑通一轮**成功**对话 —— 本机无 `DEEPSEEK_API_KEY`，需用户配置一次

**怎么撤**

- 后端：`Remove-Item -Recurse code/web/bridge`
- 前端：`Remove-Item -Recurse code/web/src/features/chat`，并把 `src/app/App.tsx` 改回全部占位
- 整体：`git clean -fd code/web`

---

## v0.5 — 2026-11-04 · P1 骨架与设计系统（本轮）

**改了什么**

新增可构建的前端骨架（18 个源文件 + 5 个配置文件），**未接任何真实数据**：

| 类别 | 文件 | 作用 |
|---|---|---|
| 构建 | `package.json`、`vite.config.ts`、`tsconfig.json`、`index.html`、`.gitignore` | 独立构建（Vite 8 + React 19 + TS 5.9 + Tailwind 4）；嵌套 `.gitignore` 避免污染根仓库 |
| 设计系统 | `src/styles/{tokens,components,index}.css` | 7 套状态枚举 → 唯一语义色令牌；深/浅主题；`.badge[data-tone]` 等语义组件 |
| 数据源 | `src/datasource/{index,http,fixture,errors}.ts`、`fixtures/README.md` | `HttpSource`（真实）/ `FixtureSource`（录制回放，只读）+ 类型化错误：缺数据必报错、不造假 |
| 状态 | `src/state/AppState.tsx` | 当前页 / 主题 / 数据源切换 / 启动即探测桥接 |
| 布局 | `src/components/layout/{AppShell,TopBar,SideNav,Inspector}.tsx`、`src/components/Icon.tsx`、`src/components/ui/primitives.tsx` | 三栏骨架 + 顶栏全局状态 + 检查器（含语义图例） |
| 页面 | `src/app/App.tsx`、`src/app/pages.ts`、`src/pages/PlaceholderPage.tsx` | 页面注册表（作用/将展示/后端依赖/就绪度）+ 七屏共用占位页 |
| 文档 | `README.md`、`docs/bridge-plan.md`、本文件 | P1 交付说明与决策状态更新 |

**为什么**

- 先把"壳"和"数据管道"分开：数据源抽象定死之后，P2 起每屏只写展示逻辑，不会各写一套取数代码。
- 设计系统先落地：MAPAS 有 7 套状态枚举，若不做语义令牌，后面必然出现同一状态多种颜色。
- 空态必须解释原因：后端没连上 / 缺录制 / 依赖待验证，三种情况分别给出引导，而不是空白页。

**验收**

- `pnpm typecheck` exit 0（strict）
- `pnpm build` exit 0：29 模块，CSS 12.5 kB、JS 252 kB（gzip 81 kB）
- `pnpm preview` → HTTP 200，含 `#root` 挂载点
- `git status --short` → 仅 `?? code/web/`，无 `M`/`D`

**怎么撤**

- 整体：`git clean -fd code/web`
- 单个文件：按上表路径直接删除即可（无跨目录依赖）

---

## v0.4 — 2026-11-04

**改了什么**

- `README.md` **再次重写**：新增**第 0 章「前序任务完成度」**（0.1–0.8，含三个断裂点、分层与数据所有权、
  八项已交付能力逐条说明、完成度总表、工程纪律、明确未做清单）与**第 2 章「规划导读」**
  （三栏骨架线框图、七个界面一览、一条演示剧本、建设顺序与出口验收）；其余章节重排为第 1、3–9 章。
- 新增 `docs/bridge-plan.md`：`web_bridge.py` 说明与审批材料
  （为什么需要、做什么、不做什么、写入范围、代价风险、不批准时的退路）。
- `docs/frontend-contract.md` §8 改成"条目 + 影响分析"，实时状态统一指向 README §8。

**为什么**

- 用户要求：README 要能被组员读懂，要交代前序完成度，要站在组员立场叙述（讲思想、框架、细节），
  并让规划的界面形态一看就懂。
- `web_bridge.py` 属于"允许区域外的写入"，需要独立的审批材料，不能只在对话里口头解释。

**怎么撤**

- README 回到 v0.3 版（本层总览版）：`git checkout` 不适用（未入库），需按本节记录手工还原关键 9 节结构。
- 删除审批材料：`Remove-Item code/web/docs/bridge-plan.md`

---

## v0.3 — 2026-11-04

**改了什么**

- 重写 `code/web/README.md`：从"P0 暂存包说明"升级为**本层总览**
  （定位与边界、目录、P0 结论、开发约定、运行方式、协作与撤回、待拍板、阶段、环境备注）。
- 新增本文件 `CHANGELOG-web.md`。

**为什么**

- 原 README 是"因仓库不可写而暂存"的临时说明，落地后已经过期；
  本层需要一个长期的、能回答"这层是什么 / 怎么协作 / 改到哪了"的入口文件。
- 用户要求"随时完善本层 README"，并需要一份可追溯的改动日志以便撤回错误操作。

**怎么撤**

- `git checkout -- code/web/README.md` 不适用（该目录尚未入库，无历史版本）。
- 旧版（v0.1 暂存包说明）只是首次落地用的过渡文档，已被上一节 v0.1 条目描述覆盖，**未保留副本**；
  如需恢复成"暂存包说明"的形态，属于回退需求，应直接 `Remove-Item -Recurse code/web` 从零重来。
- 删除本文件：`Remove-Item code/web/CHANGELOG-web.md`

**影响范围**：仅文档，无代码、无依赖、不影响 Python 侧（`setup.cfg` 只打包 `src.memory*`，
`pytest` 只收集 `code/tests`）。

---

## v0.2 — 2026-11-04

**改了什么**

- `docs/frontend-contract.md` 第 6 行：把"落位（等你授权后）：…"改为"落位：…（已落地，未提交 Git）"。

**为什么**：文件已实际落地，原文案过期会产生误导。

**怎么撤**：把该行改回 `> 落位（等你授权后）：…`。

---

## v0.1 — 2026-11-04

**改了什么**（P0 交付，首次落地）

- `code/web/README.md`（初版：暂存包说明与落地命令）
- `code/web/docs/frontend-contract.md`：P0 主契约
  —— 7 域 68 项功能点 ↔ 后端方法映射（含真实度分级）、三层数据源架构、
  Bridge HTTP 规格、TS 类型清单、目录骨架、V1–V6 待验证清单、里程碑、待决事项
- `code/web/src/types/index.ts`：全部枚举（记忆 / 检索四态 / 任务 / 协作 / 经验 7 态 / 审批 / 21 种事件）
  + 领域对象 + `Bridge` HTTP 契约 + `DataSource` 接口

**为什么**

- 前序组员已完成 Python 侧能力（Task 1/2/3 + 多 Agent + 修复 + 经验 + 任务闭环），
  前端需要先冻结"每个界面元素从哪拿数据"，再动手搭，避免做出字段对不上的界面。
- 用户明确要求"功能不造假"，因此必须先证明数据可得性，再决定技术方案。

**怎么撤**

- `Remove-Item -Recurse -Force code/web`
- 或 `git -C D:\projects\nlp- clean -fd code/web`

**当时的验收记录**

- `git -C D:\projects\nlp- status --short` → 仅 `?? code/web/`，无任何 `M`/`D`
- 三份文件与暂存源 SHA256 全部一致
- `setup.cfg` / `pyproject.toml` 确认不干扰 Python 侧
