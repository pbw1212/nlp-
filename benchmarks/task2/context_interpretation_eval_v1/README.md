# Natural-language Interpretation Engineering Evaluation v1

独立评估两个 semantic interpretation stages：30 条 current-context 自然语言样本、30 条 raw memory scope 样本和 10 个 end-to-end pair。覆盖中文、英文与中英混合；包括 explicit、UNKNOWN、AMBIGUOUS、negation、general、unspecified、REQUIRES/EXCLUDES 与 unsupported rich condition。

`runtime.json` 只包含模型输入和 pair linkage；`gold.json` 由人工逐条编写，不由 interpreter、prompt 或 ApplicabilityEngine 自动生成。这仍是 engineering pilot，不是论文 benchmark。
