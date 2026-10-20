# Controlled Retrieval Engineering Evaluation v1

这是用于 Task 2 retrieval 回归的人工工程数据集，不是论文 benchmark。数据含 40 条跨 8 个领域的 memory 与 32 个 query，其中同时包含 lexical-exact、semantic-paraphrase 和 mixed 子集。

Gold 由人工编写的 `positive_memory_ids` 与逐 query `hard_negatives` 给出，不由 embedding model 生成。该数据集只评估 candidate recall，不包含 applicability label，也不评估最终回答质量。

默认离线 runner 可使用测试专用 deterministic provider 验证 Dense/Hybrid 接口和评估逻辑；其分数不得作为 semantic performance。可发布的工程对比必须显式使用并记录真实 provider/model。
