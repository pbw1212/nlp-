#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MAPAS 独立语义质量评测（真实模型版 v2）

与仓库内 product_trial 的 DeterministicTrialLLMClient（mock）无关：
本脚本把【真实 DeepSeek API】注入同一个 ProductTrialRunner，
让系统走完整的 写入 → 存储 → 检索 → 适用性判断 → 回答 链路，
再用【确定性关键词规则】对最终回答自动判分（不使用 LLM 当裁判）。

v2 变化：
  - 每道题的记忆数据库【保留】到 semantic_eval_dbs/<题号>/ 下，
    可用任意 SQLite 工具（如 DB Browser for SQLite）打开核查；
  - 每道题跑完导出【记忆清单】（内容 + 状态 + 类型）进报告，
    回答层与记忆层双重证据；
  - 报告预留 manual_review 字段，供人工抽查后回填一致率。

放置位置： code/scripts/mapas_semantic_eval.py
运行方式： 在仓库根目录执行
    PowerShell:
        $env:DEEPSEEK_API_KEY="sk-你的key"
        python code/scripts/mapas_semantic_eval.py
    （若 ~/.agent/config/.env.local 已配置 DEEPSEEK_API_KEY 则无需设置）

输出：
  1. 控制台逐项打印 PASS/FAIL、系统回答、记忆清单
  2. code/scripts/semantic_eval_report.json   完整报告
  3. code/scripts/semantic_eval_dbs/<题号>/   每题的记忆数据库副本
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
import time
import tempfile
import urllib.request
import urllib.error
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Windows 临时目录清理容错（agent_events.db 句柄释放滞后，与产品逻辑无关）
# ---------------------------------------------------------------------------
_OrigTemporaryDirectory = tempfile.TemporaryDirectory

# 评测期间 runner 内部的临时目录改到 semantic_eval_dbs/ 下，且不自动删除，
# 这样每道题的记忆数据库（SQLite）会完整保留，可用 DB Browser 等工具打开核查。
SCRIPT_DIR_PRE = os.path.dirname(os.path.abspath(__file__))
DB_OUT_DIR = os.path.join(SCRIPT_DIR_PRE, "semantic_eval_dbs")
os.makedirs(DB_OUT_DIR, exist_ok=True)
_CURRENT_QID = {"value": "run"}


class _PreservedTemporaryDirectory(_OrigTemporaryDirectory):
    def __init__(self, *args, **kwargs):
        kwargs["dir"] = DB_OUT_DIR
        kwargs["prefix"] = f"{_CURRENT_QID['value']}_"
        kwargs["ignore_cleanup_errors"] = True
        super().__init__(*args, **kwargs)

    def cleanup(self):  # 不删除，保留证据
        pass

    def __exit__(self, *exc_info):
        return False


tempfile.TemporaryDirectory = _PreservedTemporaryDirectory

# ---------------------------------------------------------------------------
# 路径引导：脚本位于 code/scripts/ 下，把 code/ 加入 sys.path
# ---------------------------------------------------------------------------
CODE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if CODE_ROOT not in sys.path:
    sys.path.insert(0, CODE_ROOT)

from src.memory.llm.llm_client import LLMClientBase  # noqa: E402
from src.memory.product_trial.models import (  # noqa: E402
    TrialActionKind,
    TrialScenario,
    TrialStep,
)
from src.memory.product_trial.runner import ProductTrialRunner  # noqa: E402

SCRIPT_DIR = SCRIPT_DIR_PRE


# ---------------------------------------------------------------------------
# API key 读取：环境变量 > 用户配置 > 仓库根 .env.local
# ---------------------------------------------------------------------------
def _load_api_key() -> str:
    for name in ("DEEPSEEK_API_KEY", "LLM_API_KEY", "AGENT_LLM_API_KEY"):
        key = os.environ.get(name, "").strip()
        if key:
            return key
    candidates = [
        os.path.expanduser("~/.agent/config/.env.local"),
        os.path.join(os.path.dirname(CODE_ROOT), ".env.local"),
    ]
    for path in candidates:
        if not os.path.exists(path):
            continue
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                for name in ("DEEPSEEK_API_KEY", "LLM_API_KEY", "AGENT_LLM_API_KEY"):
                    if line.startswith(name + "="):
                        return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


# ---------------------------------------------------------------------------
# 真实 DeepSeek LLM 客户端（实现仓库 LLMClientBase 接口，替换 mock）
# ---------------------------------------------------------------------------
class DeepSeekTrialLLMClient(LLMClientBase):
    """真实模型客户端：DeepSeek Chat，强制 JSON 输出，带重试。"""

    #API_URL = "https://api.deepseek.com/chat/completions"
    #MODEL = "deepseek-chat"
    API_URL = "https://open.bigmodel.cn/api/paas/v4/chat/completions"
    MODEL = "glm-4-flash"   # 或 glm-4-air / glm-4-plus
    
    def __init__(self, api_key: str, max_retries: int = 3, timeout: int = 60) -> None:
        if not api_key:
            raise RuntimeError(
                "未找到 API key。请执行：\n"
                '  $env:DEEPSEEK_API_KEY="sk-你的key"\n'
                "或确认 ~/.agent/config/.env.local 中已配置 DEEPSEEK_API_KEY。"
            )
        self.api_key = api_key
        self.max_retries = max_retries
        self.timeout = timeout
        self.call_count = 0

    def generate_json(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        response_schema: Dict[str, Any] | None = None,
        schema_name: str = "structured_output",
    ) -> Dict[str, Any]:
        messages = [
            {"role": "system", "content": system_prompt + "\n请只输出 JSON。"},
            {"role": "user", "content": user_prompt},
        ]
        return self._call(messages)

    def generate_chat_json(
        self, messages: List[Dict[str, str]], enable_web_search: bool = False
    ) -> Dict[str, Any]:
        return self._call(list(messages))

    def _call(self, messages: List[Dict[str, str]]) -> Dict[str, Any]:
        body = json.dumps({
            "model": self.MODEL,
            "messages": messages,
            "response_format": {"type": "json_object"},
            "temperature": 0.0,
            "stream": False,
        }).encode("utf-8")
        last_err: Optional[Exception] = None
        for attempt in range(self.max_retries):
            try:
                req = urllib.request.Request(
                    self.API_URL,
                    data=body,
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {self.api_key}",
                    },
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    payload = json.loads(resp.read().decode("utf-8"))
                self.call_count += 1
                content = payload["choices"][0]["message"]["content"]
                return json.loads(content)
            except (urllib.error.URLError, KeyError, json.JSONDecodeError) as err:
                last_err = err
                time.sleep(2 * (attempt + 1))
        raise RuntimeError(f"DeepSeek API 调用失败（重试 {self.max_retries} 次）: {last_err}")


# ---------------------------------------------------------------------------
# 评测题目定义
#   setup: 铺垫动作序列；question: 最后提问
#   any_of: 每个子列表至少命中一个关键词（组间 AND）
#   not_contains: 回答中不得出现的内容（旧值 / 串台内容）
# ---------------------------------------------------------------------------
CHAT = TrialActionKind.CHAT
REMEMBER = TrialActionKind.COMMAND          # 配合 "/remember xxx" 文本
RESTART = TrialActionKind.RESTART_APP
RESUME = TrialActionKind.RESUME_SESSION
NEW_SESSION = TrialActionKind.CREATE_SESSION
NEW_TASK = TrialActionKind.CREATE_TASK

QA_ITEMS: List[Dict[str, Any]] = [
    # ---- A. 显式写入 → 回忆 -------------------------------------------------
    {"qid": "A01", "category": "A 显式记忆回忆",
     "setup": [(REMEMBER, "/remember 我对花生过敏")],
     "question": "我有什么食物过敏吗？",
     "any_of": [["花生"]], "not_contains": []},
    {"qid": "A02", "category": "A 显式记忆回忆",
     "setup": [(REMEMBER, "/remember 我的生日是 3 月 14 日")],
     "question": "我的生日是哪天？",
     "any_of": [["3 月 14", "3月14", "三月十四"]], "not_contains": []},
    {"qid": "A03", "category": "A 显式记忆回忆",
     "setup": [(REMEMBER, "/remember 我每周三晚上打篮球")],
     "question": "我每周几打篮球？",
     "any_of": [["周三", "星期三"]], "not_contains": []},
    {"qid": "A04", "category": "A 显式记忆回忆",
     "setup": [(REMEMBER, "/remember 我的猫叫年糕，是一只橘猫")],
     "question": "我的猫叫什么名字？",
     "any_of": [["年糕"]], "not_contains": []},

    # ---- B. 对话中陈述 → 自动记忆 → 回忆 ------------------------------------
    {"qid": "B01", "category": "B 对话自动记忆",
     "setup": [(CHAT, "顺便说一下，我下个月要搬去杭州工作了。")],
     "question": "我下个月要搬去哪个城市？",
     "any_of": [["杭州"]], "not_contains": []},
    {"qid": "B02", "category": "B 对话自动记忆",
     "setup": [(CHAT, "我最近开始学小提琴了，每天练半小时。")],
     "question": "我最近在学习什么乐器？",
     "any_of": [["小提琴"]], "not_contains": []},
    {"qid": "B03", "category": "B 对话自动记忆",
     "setup": [(CHAT, "记住一下，我喝咖啡不加糖。")],
     "question": "我喝咖啡有什么习惯？",
     "any_of": [["不加糖", "不放糖", "无糖"]], "not_contains": []},

    # ---- C. 记忆更新与纠正 ---------------------------------------------------
    {"qid": "C01", "category": "C 记忆更新纠正",
     "setup": [(REMEMBER, "/remember 我喜欢吃香菜"),
               (REMEMBER, "/remember 我现在不吃香菜了")],
     "question": "我现在吃香菜吗？",
     "any_of": [["不吃", "不喜欢", "不再"]], "not_contains": []},
    {"qid": "C02", "category": "C 记忆更新纠正",
     "setup": [(REMEMBER, "/remember 我的手机号是 13800001111"),
               (REMEMBER, "/remember 我的手机号换成了 13900002222")],
     "question": "我的手机号是多少？",
     "any_of": [["13900002222"]], "not_contains": ["13800001111"]},
    {"qid": "C03", "category": "C 记忆更新纠正",
     "setup": [(CHAT, "我以前经常熬夜到凌晨两点。"),
               (CHAT, "不过从上个月开始我改成十一点半睡觉了。")],
     "question": "我现在一般几点睡觉？",
     "any_of": [["十一点半", "11:30", "11 点半", "23:30"]], "not_contains": ["凌晨两点"]},

    # ---- D. 跨会话持久化 -----------------------------------------------------
    {"qid": "D01", "category": "D 跨会话持久",
     "setup": [(REMEMBER, "/remember 我的项目名称叫北极星"),
               (RESTART, ""), (RESUME, "")],
     "question": "我的项目名称叫什么？",
     "any_of": [["北极星"]], "not_contains": []},
    {"qid": "D02", "category": "D 跨会话持久",
     "setup": [(REMEMBER, "/remember 我导师姓陈，是计算机学院的"),
               (RESTART, ""), (RESUME, "")],
     "question": "我导师姓什么？",
     "any_of": [["陈"]], "not_contains": []},

    # ---- E. 会话隔离 ---------------------------------------------------------
    {"qid": "E01", "category": "E 会话隔离",
     "setup": [(CHAT, "这场对话里我的临时代号是蓝鲸。"),
               (NEW_SESSION, "全新会话")],
     "question": "我的临时代号是什么？",
     "any_of": [["没有", "未", "不知道", "尚未"]],
     "not_contains": ["蓝鲸"]},
    {"qid": "E02", "category": "E 会话隔离",
     "setup": [(CHAT, "先记个临时的事：今晚的取件码是 8842。"),
               (NEW_SESSION, "另一场对话")],
     "question": "我的取件码是多少？",
     "any_of": [["没有", "未", "不知道", "尚未"]],
     "not_contains": ["8842"]},

    # ---- F. 适用性拦截 -------------------------------------------------------
    {"qid": "F01", "category": "F 适用性拦截",
     "setup": [(REMEMBER, "/remember 我喜欢独自去上海旅行")],
     "question": "推荐几个适合我和朋友一起徒步的地方？",
     "any_of": [], "not_contains": ["独自"]},
    {"qid": "F02", "category": "F 适用性拦截",
     "setup": [(REMEMBER, "/remember 我不吃辣")],
     "question": "帮我看看这份甜品菜单有什么推荐？",
     "any_of": [], "not_contains": ["不辣", "不要辣", "免辣"]},
    {"qid": "F03", "category": "F 适用性拦截",
     "setup": [(REMEMBER, "/remember 我用 Windows 台式机办公")],
     "question": "我的 MacBook 连不上打印机怎么办？",
     "any_of": [], "not_contains": ["Windows 台式机"]},

    # ---- G. 多任务上下文 -----------------------------------------------------
    {"qid": "G01", "category": "G 多任务上下文",
     "setup": [(NEW_TASK, "写论文"), (REMEMBER, "/remember 论文截止日期是 6 月 30 日"),
               (NEW_TASK, "健身计划"), (REMEMBER, "/remember 我每周健身四次")],
     "question": "我的论文什么时候截止？",
     "any_of": [["6 月 30", "6月30", "六月三十"]], "not_contains": []},
    {"qid": "G02", "category": "G 多任务上下文",
     "setup": [(NEW_TASK, "写论文"), (REMEMBER, "/remember 论文用 LaTeX 排版"),
               (NEW_TASK, "健身计划")],
     "question": "我每周健身几次？",
     "any_of": [["不知道", "没有", "未", "尚未", "没提到"]],
     "not_contains": ["LaTeX"]},
]


# ---------------------------------------------------------------------------
# 判分与记忆清单
# ---------------------------------------------------------------------------
def _normalize(text: str) -> str:
    text = re.sub(r"[\s，。、！？,.!?'\"“”‘’：:；;（）()\-—]", "", text)
    return text.lower()


def grade(answer: str, item: Dict[str, Any]) -> Tuple[bool, List[str]]:
    norm = _normalize(answer)
    reasons: List[str] = []
    ok = True
    for group in item["any_of"]:
        if not any(_normalize(kw) in norm for kw in group):
            ok = False
            reasons.append(f"缺少要点 {group}")
    for bad in item["not_contains"]:
        if _normalize(bad) in norm:
            ok = False
            reasons.append(f"出现禁止内容 '{bad}'")
    return ok, reasons


def build_scenario(item: Dict[str, Any]) -> TrialScenario:
    steps = [TrialStep(action_kind=kind, input_text=text) for kind, text in item["setup"]]
    steps.append(TrialStep(action_kind=CHAT, input_text=item["question"]))
    return TrialScenario(
        scenario_id=item["qid"],
        category=item["category"],
        description=item["question"],
        steps=tuple(steps),
        invariants=(),
    )


def dump_memories(result) -> List[Dict[str, str]]:
    """从最后一个步骤的观测中提取记忆清单（内容 + 状态 + 类型）。"""
    for sr in reversed(result.step_results):
        mems = getattr(sr.observation, "memories", None)
        if mems is not None:
            return [
                {
                    "memory_id": getattr(m, "memory_id", ""),
                    "type": getattr(m, "type", ""),
                    "status": getattr(m, "status", ""),
                    "content": getattr(m, "content", ""),
                }
                for m in mems
            ]
    return []


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def main() -> None:
    api_key = _load_api_key()
    client = DeepSeekTrialLLMClient(api_key)
    runner = ProductTrialRunner(llm_client=client)

    print("=" * 72)
    print("MAPAS 独立语义质量评测（真实 DeepSeek 模型 + 确定性判分）")
    print(f"共 {len(QA_ITEMS)} 题，覆盖 {len(set(i['category'] for i in QA_ITEMS))} 个维度")
    print(f"每题记忆数据库将保留至: {DB_OUT_DIR}")
    print("=" * 72)

    records: List[Dict[str, Any]] = []
    t0 = time.time()
    for item in QA_ITEMS:
        _CURRENT_QID["value"] = item["qid"]
        scenario = build_scenario(item)
        answer, memories, err = "", [], ""
        passed, reasons = False, []
        try:
            result = runner.run_scenario(scenario)
            for sr in reversed(result.step_results):
                if sr.action_kind == CHAT.value and sr.observation.visible_response:
                    answer = sr.observation.visible_response
                    break
            memories = dump_memories(result)
            passed, reasons = grade(answer, item)
        except Exception as exc:  # 单题失败不中断整体
            reasons = ["运行异常"]
            err = f"{type(exc).__name__}: {exc}"

        status = "PASS" if passed else "FAIL"
        print(f"\n[{status}] {item['qid']} {item['category']}")
        print(f"  提问: {item['question']}")
        print(f"  回答: {answer[:120]}{'...' if len(answer) > 120 else ''}")
        if memories:
            print(f"  记忆库（{len(memories)} 条）:")
            for m in memories:
                print(f"    [{m['status']:<10}] ({m['type']}) {m['content'][:60]}")
        if reasons:
            print(f"  判分: {'; '.join(reasons)}")
        if err:
            print(f"  异常: {err}")

        records.append({
            "qid": item["qid"], "category": item["category"],
            "question": item["question"], "answer": answer,
            "memories": memories,
            "passed": passed, "reasons": reasons, "error": err,
            "manual_review": None,   # 人工抽查后回填 True/False
        })

    # ---- 汇总 --------------------------------------------------------------
    elapsed = time.time() - t0
    total = len(records)
    n_pass = sum(1 for r in records if r["passed"])
    print("\n" + "=" * 72)
    print(f"总体: {n_pass}/{total} 通过（{n_pass / total * 100:.1f}%），"
          f"耗时 {elapsed:.0f}s，API 调用 {client.call_count} 次")
    by_cat: Dict[str, List[bool]] = {}
    for r in records:
        by_cat.setdefault(r["category"], []).append(r["passed"])
    print("\n分维度准确率:")
    for cat, flags in by_cat.items():
        p = sum(flags)
        print(f"  {cat:<14} {p}/{len(flags)}  ({p / len(flags) * 100:.0f}%)")
    print("=" * 72)

    report = {
        "model": DeepSeekTrialLLMClient.MODEL,
        "total": total, "passed": n_pass,
        "accuracy": n_pass / total,
        "by_category": {c: {"passed": sum(f), "total": len(f)} for c, f in by_cat.items()},
        "elapsed_seconds": round(elapsed, 1),
        "api_calls": client.call_count,
        "records": records,
    }
    out_path = os.path.join(SCRIPT_DIR, "semantic_eval_report.json")
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)
    print(f"\n完整报告已写入: {out_path}")
    print("提示：报告 records 中每题含 memories 记忆清单；"
          "人工抽查后可回填 manual_review 字段统计一致率。")


if __name__ == "__main__":
    main()
