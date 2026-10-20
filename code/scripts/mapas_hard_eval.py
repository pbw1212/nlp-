#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MAPAS 独立语义质量评测 · 难题组（H 组，8 题）

目的：探测系统能力边界。题目故意设计得模糊、有干扰、需要多步推理，
MAPAS 做错是正常且有价值的——报告中的失败项即"能力边界清单"。

判分方式、运行环境与主评测（mapas_semantic_eval.py）完全一致。
放置位置： code/scripts/mapas_hard_eval.py
运行方式： $env:DEEPSEEK_API_KEY="sk-你的key"; python code/scripts/mapas_hard_eval.py
输出：     code/scripts/hard_eval_report.json
"""

from __future__ import annotations

import json
import os
import sys
import time
from typing import Any, Dict, List

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

# 复用主评测的全部基础设施（客户端、判分、场景构造、数据库保留）
from mapas_semantic_eval import (  # noqa: E402
    CHAT, REMEMBER, RESTART, RESUME, NEW_SESSION, NEW_TASK,
    DeepSeekTrialLLMClient, ProductTrialRunner,
    _load_api_key, build_scenario, dump_memories, grade,
    _CURRENT_QID, DB_OUT_DIR,
)

# ---------------------------------------------------------------------------
# H 组难题：干扰 / 多跳 / 细粒度纠正 / 模糊指代 / 负向断言 / 长时序
# ---------------------------------------------------------------------------
QA_HARD: List[Dict[str, Any]] = [
    {"qid": "H01", "category": "H1 相似记忆干扰",
     "setup": [(REMEMBER, "/remember 我喜欢喝拿铁"),
               (REMEMBER, "/remember 我早上一般喝美式"),
               (REMEMBER, "/remember 我喝咖啡不加糖")],
     "question": "我下午想喝咖啡的话，按我的喜好你会推荐什么？",
     # 要点：推荐应基于拿铁/美式/不加糖之一，且不得推荐加糖
     "any_of": [["拿铁", "美式"]], "not_contains": ["加糖", "全糖"]},
    {"qid": "H02", "category": "H1 相似记忆干扰",
     "setup": [(REMEMBER, "/remember 我周一上午有组会"),
               (REMEMBER, "/remember 我周三晚上打篮球"),
               (REMEMBER, "/remember 我周五下午有羽毛球课")],
     "question": "我周三晚上有什么安排？",
     "any_of": [["篮球"]], "not_contains": ["组会", "羽毛球"]},
    {"qid": "H03", "category": "H2 细粒度纠正",
     "setup": [(REMEMBER, "/remember 我的生日是 3 月 14 日"),
               (REMEMBER, "/remember 说错了，我的生日是 3 月 15 日")],
     "question": "我的生日是哪天？",
     "any_of": [["3 月 15", "3月15", "三月十五"]], "not_contains": ["3 月 14", "3月14"]},
    {"qid": "H04", "category": "H2 细粒度纠正",
     "setup": [(REMEMBER, "/remember 我住在北京海淀区"),
               (REMEMBER, "/remember 我搬到了北京朝阳区"),
               (REMEMBER, "/remember 我刚又搬了，现在住上海浦东")],
     "question": "我现在住在哪个城市？",
     "any_of": [["上海"]], "not_contains": ["北京"]},
    {"qid": "H05", "category": "H3 多跳推理",
     "setup": [(REMEMBER, "/remember 我导师姓陈"),
               (REMEMBER, "/remember 陈老师的实验室在信息楼 3 层")],
     "question": "我导师的实验室在哪里？",
     "any_of": [["信息楼"]], "not_contains": []},
    {"qid": "H06", "category": "H4 模糊指代",
     "setup": [(CHAT, "我昨天买了副新耳机，花了 899 块。")],
     "question": "上次说的那个东西多少钱来着？",
     "any_of": [["899"]], "not_contains": []},
    {"qid": "H07", "category": "H5 负向断言",
     "setup": [(REMEMBER, "/remember 我没有任何食物过敏")],
     "question": "帮我看看这份有花生酱的菜单我能吃吗？",
     # 正确行为：提及"没有过敏/可以吃"，不得编造过敏史
     "any_of": [["没有", "不过敏", "可以吃", "能吃", "无过敏"]],
     "not_contains": ["对花生过敏"]},
    {"qid": "H08", "category": "H6 长时序＋跨会话",
     "setup": [(REMEMBER, "/remember 我计划 5 月去日本旅行"),
               (RESTART, ""), (RESUME, ""),
               (REMEMBER, "/remember 日本旅行改到 7 月了"),
               (RESTART, ""), (RESUME, "")],
     "question": "我的日本旅行定在几月？",
     "any_of": [["7 月", "7月", "七月"]], "not_contains": ["5 月", "5月"]},
]


def main() -> None:
    client = DeepSeekTrialLLMClient(_load_api_key())
    runner = ProductTrialRunner(llm_client=client)

    print("=" * 72)
    print(f"MAPAS 难题组评测（{len(QA_HARD)} 题，故意模糊/有干扰，做错即能力边界）")
    print("=" * 72)

    records: List[Dict[str, Any]] = []
    t0 = time.time()
    for item in QA_HARD:
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
        except Exception as exc:
            reasons = ["运行异常"]
            err = f"{type(exc).__name__}: {exc}"

        status = "PASS" if passed else "FAIL"
        print(f"\n[{status}] {item['qid']} {item['category']}")
        print(f"  提问: {item['question']}")
        print(f"  回答: {answer[:150]}{'...' if len(answer) > 150 else ''}")
        if memories:
            print(f"  记忆库（{len(memories)} 条）:")
            for m in memories:
                print(f"    [{m['status']:<10}] {m['content'][:60]}")
        if reasons:
            print(f"  判分: {'; '.join(reasons)}")
        if err:
            print(f"  异常: {err}")
        records.append({
            "qid": item["qid"], "category": item["category"],
            "question": item["question"], "answer": answer,
            "memories": memories, "passed": passed,
            "reasons": reasons, "error": err, "manual_review": None,
        })

    elapsed = time.time() - t0
    n_pass = sum(1 for r in records if r["passed"])
    print("\n" + "=" * 72)
    print(f"难题组: {n_pass}/{len(records)} 通过（{n_pass / len(records) * 100:.1f}%），"
          f"耗时 {elapsed:.0f}s")
    fails = [r["qid"] for r in records if not r["passed"]]
    if fails:
        print(f"能力边界（未通过）: {', '.join(fails)}")
    print("=" * 72)

    out_path = os.path.join(SCRIPT_DIR, "hard_eval_report.json")
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump({
            "model": DeepSeekTrialLLMClient.MODEL,
            "total": len(records), "passed": n_pass,
            "accuracy": n_pass / len(records),
            "elapsed_seconds": round(elapsed, 1),
            "records": records,
        }, fh, ensure_ascii=False, indent=2)
    print(f"报告已写入: {out_path}")


if __name__ == "__main__":
    main()
