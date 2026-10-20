# Controlled Applicability Evaluation v1

这是 Phase 2C 的 deterministic controlled evaluation，不是论文最终 benchmark、SOTA dataset 或真实用户研究。

文件边界：

- `memories.json`：Task 1 temporary SQLite 输入；包含 current content，个别 case 含 initial historical content。
- `profiles.json`：Task 2 candidate-side applicability annotations。
- `queries.json`：query、domain、scenario family、8-memory controlled candidate universe 与 positive recall targets。
- `contexts.json`：caller-supplied structured context；不包含 gold。
- `labels.json`：独立 gold labels、reason dimensions 与 supported/rich-reasoning subset。

Evaluator 必须先独立加载 runtime inputs，再加载 gold；production `src/memory/retrieval/` 不 import 本目录。每个 query universe 的全部 memory pair 都有 gold，避免根据实际 Top-K 临时补标签。所有 IDs、enum、references、coverage 和 gold/runtime separation 由 validator 检查。

当前限制：context/profile 均由人工 fixture 提供；没有自然语言解析、profile inference、semantic alias 或复杂 conditional rule。
