# ACCOUNT B REAL-PROVIDER SMOKE REPORT

> **历史快照（2026-08-21）**：这是旧 Groq Provider、旧模型和旧 MainAgent 基线的
> 失败/通过记录，不是当前 DeepSeek 产品状态。当前实现与限制请看
> [`docs/project-progress.md`](../project-progress.md)。

## 1. Status

**FAIL** (SMOKE_01 PASS, SMOKE_02 STEP_LIMIT FAIL, SMOKE_03 PASS).

---

## 2. Identity

- **mechanism**: `main-agent-mechanism-v1.2`
- **mechanism_commit**: `473d8c6cee58ea90cbe519f8112ba0477eb65795`
- **protocol_commit**: `79d6f107dd5955feef650b7c5bec48c5916e903b`
- **benchmark_digest**: `4efe1435ad21323f46f483c6dd4c49aa523aa78a1ee6cb98f498dd23351ec8aa`
- **credential_alias**: `secondary` (Account B)
- **provider**: Groq
- **model**: `openai/gpt-oss-120b`

---

## 3. Pacing

- **between cases**: 65s
- **extra preflight provider requests**: 0

---

## 4. SMOKE_01

- **status**: `SUCCESS`
- **action**: `FINAL_RESPONSE`
- **tool**: `None`
- **elapsed**: 23.7s (run 1) / 31.1s (run 2)
- **reasoner_calls**: 1
- **agent_steps**: 1
- **tokens**: UNAVAILABLE
- **fallback**: `NO` (`json_schema` succeeded directly)
- **result**: **PASS**

---

## 5. SMOKE_02

- **status**: `STEP_LIMIT_REACHED`
- **initial action**: `TOOL_CALL` (`weather_lookup`)
- **tool**: `weather_lookup`
- **fake tool executed**: `YES` (Read-only executor executed inside step loop)
- **tool result consumed**: `YES` (`ToolObservation` returned to reasoner)
- **final status**: `STEP_LIMIT` (Model repeatedly re-queried tool on generic `"Success"` return without finalizing response)
- **elapsed**: 176.7s
- **tokens**: UNAVAILABLE
- **fallback**: `NO`
- **result**: **FAIL**

---

## 6. SMOKE_03

- **status**: `SUCCESS`
- **tool**: `hotel_book`
- **effect**: `SIDE_EFFECT`
- **AWAITING_CONFIRMATION**: `YES` (`AgentResultStatus.AWAITING_CONFIRMATION`)
- **fake executor execution_count before confirmation**: `0` (Confirmed zero execution before user confirmation)
- **elapsed**: 42.0s
- **tokens**: UNAVAILABLE
- **fallback**: `NO`
- **result**: **PASS**

---

## 7. Provider

- **429**: NONE (0 on Account B)
- **403**: NONE (0 on Account B)
- **5xx**: NONE
- **timeouts**: NONE
- **schema fallback**: NONE (`json_schema` 100% supported directly)

---

## 8. Security

- **real side effects**: NONE
- **secret emitted**: NO

---

## 9. Git

- **production drift**: NONE
- **benchmark drift**: NONE
- **harness drift**: NONE
- **working tree**: CLEAN

---

## 10. Decision

- **Smoke 3/3**: **NO** (2/3 PASSED: SMOKE_01 PASS, SMOKE_03 PASS; SMOKE_02 STEP_LIMIT FAIL)
- **Ready for official Tier A**: **NO** (SMOKE_02 read-only tool loop hit step limit when given generic fake tool observation `"Success"`).
