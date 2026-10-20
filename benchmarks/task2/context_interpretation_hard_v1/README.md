# Context Interpretation Hardening Set v1

这是独立于 frozen `context_interpretation_eval_v1` 的工程对抗集。它以人工自然语言和独立 gold 覆盖：诱导式缺失信息、歧义、否定、纠正、中英混合、query intent 与 applicability fact 分离、field alias、`EXPLICIT_GENERAL` 与 `UNSPECIFIED`。

`runtime.json` 是 interpreter 输入，`gold.json` 仅供 evaluator；production code 不读取本目录。该集合不是论文 benchmark，也不用于修改 frozen-v1 gold。
