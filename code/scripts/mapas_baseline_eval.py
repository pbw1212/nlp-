#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MAPAS 语义评测 · 对照组实验（不依赖 MAPAS 仓库，纯 DeepSeek API）

用与 mapas_semantic_eval.py 完全相同的题目，跑两个对照方案：

  基线 1（no_memory）: 无记忆裸模型。
      模拟普通聊天机器人：铺垫对话照常进行，但每道题的最终提问在
      【全新对话】中发出，模型看不到任何历史。
      对应"没有记忆系统的 LLM"。

  基线 2（full_context）: 全上下文模型。
      模拟 LongContext 方案：铺垫对话与最终提问在同一条消息列表里，
      所有历史原样塞给模型，不做任何筛选/拦截。
      对应"把所有历史都塞进 prompt"的方案。

判分规则与主评测完全一致（关键词硬规则），保证三方可比。

放置位置： code/scripts/mapas_baseline_eval.py
运行方式：
    $env:DEEPSEEK_API_KEY="sk-你的key"
    python code/scripts/mapas_baseline_eval.py

输出： code/scripts/baseline_eval_report.json
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.request
import urllib.error
from typing import Any, Dict, List, Optional, Tuple

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# ---------------------------------------------------------------------------
# 与主评测共享同一套题目（从主评测脚本导入，保证题目一字不差）
# 若导入失败（脚本不在同一目录），请把两个脚本放在同一目录下。
# ---------------------------------------------------------------------------
sys.path.insert(0, SCRIPT_DIR)
try:
    from mapas_semantic_eval import QA_ITEMS, grade  # noqa: E402
except ImportError:
    print("错误：请把本脚本与 mapas_semantic_eval.py 放在同一目录（code/scripts/）下。")
    sys.exit(1)


def _load_api_key() -> str:
    for name in ("DEEPSEEK_API_KEY", "LLM_API_KEY", "AGENT_LLM_API_KEY"):
        key = os.environ.get(name, "").strip()
        if key:
            return key
    for path in (os.path.expanduser("~/.agent/config/.env.local"),):
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
# DeepSeek 纯聊天封装（非 JSON 模式，模拟普通对话）
# ---------------------------------------------------------------------------
class DeepSeekChat:
    API_URL = "https://api.deepseek.com/chat/completions"
    MODEL = "deepseek-chat"

    def __init__(self, api_key: str, max_retries: int = 3, timeout: int = 60) -> None:
        if not api_key:
            raise RuntimeError('未找到 API key，请先执行：$env:DEEPSEEK_API_KEY="sk-你的key"')
        self.api_key = api_key
        self.max_retries = max_retries
        self.timeout = timeout
        self.call_count = 0

    def chat(self, messages: List[Dict[str, str]]) -> str:
        body = json.dumps({
            "model": self.MODEL,
            "messages": messages,
            "temperature": 0.0,
            "stream": False,
        }).encode("utf-8")
        last_err: Optional[Exception] = None
        for attempt in range(self.max_retries):
            try:
                req = urllib.request.Request(
                    self.API_URL, data=body,
                    headers={"Content-Type": "application/json",
                             "Authorization": f"Bearer {self.api_key}"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    payload = json.loads(resp.read().decode("utf-8"))
                self.call_count += 1
                return payload["choices"][0]["message"]["content"]
            except (urllib.error.URLError, KeyError, json.JSONDecodeError) as err:
                last_err = err
                time.sleep(2 * (attempt + 1))
        raise RuntimeError(f"DeepSeek API 调用失败: {last_err}")


# ---------------------------------------------------------------------------
# 把主评测的 setup 动作翻译成"普通对话消息"
#   /remember xxx   -> 用户说"请记住：xxx"，助手回应"好的，我记住了。"
#   CHAT 文本       -> 用户消息（助手回复由模型生成）
#   RESTART/RESUME  -> 无记忆基线：本来就无状态；全上下文基线：继续累积
#   NEW_SESSION     -> 无记忆基线：无影响；全上下文基线：历史仍在（这正是它的问题）
#   NEW_TASK        -> 用户说"我们开个新任务：xxx"
# ---------------------------------------------------------------------------
KIND_NAMES = {
    "COMMAND": "COMMAND", "CHAT": "CHAT", "RESTART_APP": "RESTART_APP",
    "RESUME_SESSION": "RESUME_SESSION", "CREATE_SESSION": "CREATE_SESSION",
    "CREATE_TASK": "CREATE_TASK",
}


def run_baseline_no_memory(llm: DeepSeekChat, item: Dict[str, Any]) -> str:
    """无记忆基线：铺垫在独立对话里进行，最终提问在全新对话中发出。"""
    # 铺垫照常进行（模拟用户之前用过这个助手），但结果不保留
    for kind, text in item["setup"]:
        kind = str(kind)
        if kind.endswith("CHAT"):
            llm.chat([{"role": "user", "content": text}])
        elif kind.endswith("COMMAND") and text.startswith("/remember "):
            fact = text[len("/remember "):]
            llm.chat([{"role": "user", "content": f"请记住：{fact}"}])
        elif kind.endswith("CREATE_TASK"):
            llm.chat([{"role": "user", "content": f"我们开个新任务：{text}"}])
    # 最终提问：全新对话，没有任何历史
    return llm.chat([{"role": "user", "content": item["question"]}])


def run_baseline_full_context(llm: DeepSeekChat, item: Dict[str, Any]) -> str:
    """全上下文基线：所有历史塞在同一条消息列表里。"""
    messages: List[Dict[str, str]] = [{
        "role": "system",
        "content": "你是一个个人智能助手。请根据对话历史回答用户的问题。",
    }]
    for kind, text in item["setup"]:
        kind = str(kind)
        if kind.endswith("CHAT"):
            messages.append({"role": "user", "content": text})
            reply = llm.chat(messages)
            messages.append({"role": "assistant", "content": reply})
        elif kind.endswith("COMMAND") and text.startswith("/remember "):
            fact = text[len("/remember "):]
            messages.append({"role": "user", "content": f"请记住：{fact}"})
            messages.append({"role": "assistant", "content": "好的，我记住了。"})
        elif kind.endswith("CREATE_TASK"):
            messages.append({"role": "user", "content": f"我们开个新任务：{text}"})
            messages.append({"role": "assistant", "content": "好的。"})
        elif kind.endswith("CREATE_SESSION"):
            # 全上下文方案没有"会话"概念：历史继续累积（泄露风险的来源）
            messages.append({"role": "user", "content": "（开始新会话）"})
            messages.append({"role": "assistant", "content": "好的。"})
        # RESTART/RESUME 对全上下文无影响
    messages.append({"role": "user", "content": item["question"]})
    return llm.chat(messages)


BASELINES = {
    "no_memory": ("基线1 无记忆裸模型", run_baseline_no_memory),
    "full_context": ("基线2 全上下文模型", run_baseline_full_context),
}


def main() -> None:
    llm = DeepSeekChat(_load_api_key())
    all_results: Dict[str, Any] = {}

    for key, (label, fn) in BASELINES.items():
        print("=" * 72)
        print(f"{label}（同一套 {len(QA_ITEMS)} 题，同一判分规则）")
        print("=" * 72)
        records: List[Dict[str, Any]] = []
        for item in QA_ITEMS:
            try:
                answer = fn(llm, item)
                passed, reasons = grade(answer, item)
                err = ""
            except Exception as exc:
                answer, passed, reasons = "", False, ["运行异常"]
                err = f"{type(exc).__name__}: {exc}"
            status = "PASS" if passed else "FAIL"
            print(f"[{status}] {item['qid']} {item['category']} | {item['question']}")
            print(f"       回答: {answer[:100]}{'...' if len(answer) > 100 else ''}")
            records.append({
                "qid": item["qid"], "category": item["category"],
                "question": item["question"], "answer": answer,
                "passed": passed, "reasons": reasons, "error": err,
            })
        n_pass = sum(1 for r in records if r["passed"])
        by_cat: Dict[str, List[bool]] = {}
        for r in records:
            by_cat.setdefault(r["category"], []).append(r["passed"])
        print(f"\n{label} 总体: {n_pass}/{len(records)}（{n_pass / len(records) * 100:.1f}%）")
        for cat, flags in by_cat.items():
            print(f"  {cat:<14} {sum(flags)}/{len(flags)}")
        all_results[key] = {
            "label": label, "passed": n_pass, "total": len(records),
            "by_category": {c: {"passed": sum(f), "total": len(f)}
                            for c, f in by_cat.items()},
            "records": records,
        }
        print()

    out_path = os.path.join(SCRIPT_DIR, "baseline_eval_report.json")
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(all_results, fh, ensure_ascii=False, indent=2)
    print(f"对照组报告已写入: {out_path}（API 调用 {llm.call_count} 次）")
    print("把本报告与 semantic_eval_report.json 一起发回，即可生成三方对比图。")


if __name__ == "__main__":
    main()
