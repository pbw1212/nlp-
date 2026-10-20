import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Mapping, Optional

from src.memory.core.paths import default_agent_data_dir


DEEPSEEK_PROVIDER_NAME = "deepseek"
DEEPSEEK_API_KEY_ENV = "DEEPSEEK_API_KEY"
DEEPSEEK_API_BASE = "https://api.deepseek.com"
DEEPSEEK_MODEL_NAME = "deepseek-v4-flash"


class ProviderMode(str, Enum):
    """模型能力的显式执行语义。"""
    REQUIRED = "REQUIRED"


def _env_bool(value: str, *, name: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} 必须是 true/false")


def _global_config_env_path() -> Path:
    return Path(default_agent_data_dir()) / "config" / ".env.local"


def _load_local_env(path: Optional[Path] = None) -> dict[str, str]:
    """读取简单的 local-only dotenv；不修改 process environment，也不记录值。"""
    path = path or Path(".env.local")
    if not path.is_file():
        return {}
    values: dict[str, str] = {}
    for line_no, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(f".env.local 第 {line_no} 行缺少 '='")
        key, value = (item.strip() for item in line.split("=", 1))
        if not key or not key.replace("_", "").isalnum():
            raise ValueError(f".env.local 第 {line_no} 行变量名非法")
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key] = value
    return values


@dataclass
class MemoryConfig:
    # 物理存储配置
    sqlite_db_path: str = "memory_store.db"
    vector_dim: int = 384
    
    # 遗忘功能默认关闭；Provider 通过生命周期提议调整记忆权重。
    # 只有显式打开此开关，才允许时间衰减和自动回收。
    enable_forgetting: bool = False
    enable_model_rerank: bool = False
    # 兼容保留的遗忘参数；开关关闭时不参与普通产品流程。
    s0_initial_strength: float = 1.0        # 初始最大可用强度 S_0
    decay_gamma: float = 0.2                 # 间隔重复加权系数 gamma
    decay_threshold: float = 0.15            # 强到低于此阈值移入回收站
    time_unit_hours: float = 168.0           # 时间衰减基准半衰期单位（7天 / 168小时）
    
    # 30 天回收站配置
    recycle_bin_retention_days: int = 30     # 回收站保留天数
    
    # Provider 配置：真实 API 必须可用；失败时显式报错且不降级。
    provider_mode: ProviderMode = ProviderMode.REQUIRED
    llm_provider_name: str = DEEPSEEK_PROVIDER_NAME
    llm_api_key_env: str = DEEPSEEK_API_KEY_ENV
    llm_api_key: Optional[str] = None
    llm_api_base: str = DEEPSEEK_API_BASE
    llm_model_name: str = DEEPSEEK_MODEL_NAME
    # Complex multi-turn reasoning and structured memory writes commonly need
    # more than one short socket window. Keep the deadline finite, but avoid
    # turning a normal long prompt into an immediate false failure.
    llm_request_timeout_seconds: float = 60.0
    llm_max_retries: int = 3
    llm_retry_delay_seconds: float = 1.0
    llm_request_interval_seconds: float = 0.0
    llm_reasoning_effort: Optional[str] = None
    llm_max_completion_tokens: Optional[int] = None
    llm_context_window_tokens: Optional[int] = None
    context_reserve_tokens: int = 8192
    context_compaction_ratio: float = 0.78
    context_keep_recent_tokens: int = 20000
    provenance_excerpt_max_chars: int = 240

    def __repr__(self) -> str:
        """Redact llm_api_key to prevent credential exposure in logs or print statements."""
        key_status = "<set>" if self.llm_api_key else "<unset>"
        return (
            f"MemoryConfig("
            f"sqlite_db_path={self.sqlite_db_path!r}, "
            f"provider_mode={self.provider_mode!r}, "
            f"llm_provider_name={self.llm_provider_name!r}, "
            f"llm_model_name={self.llm_model_name!r}, "
            f"llm_api_base={self.llm_api_base!r}, "
            f"llm_api_key_env={self.llm_api_key_env!r}, "
            f"llm_api_key={key_status}, "
            f"llm_request_timeout_seconds={self.llm_request_timeout_seconds!r}, "
            f"llm_max_retries={self.llm_max_retries!r}, "
            f"llm_reasoning_effort={self.llm_reasoning_effort!r}, "
            f"llm_max_completion_tokens={self.llm_max_completion_tokens!r}"
            f")"
        )

    def __post_init__(self) -> None:
        if isinstance(self.provider_mode, str):
            try:
                self.provider_mode = ProviderMode(self.provider_mode.upper())
            except ValueError as exc:
                raise ValueError("MEMORY_PROVIDER_MODE 必须是 REQUIRED") from exc
        if not self.sqlite_db_path:
            raise ValueError("sqlite_db_path 不能为空")
        if self.vector_dim <= 0:
            raise ValueError("vector_dim 必须大于 0")
        if not 0.0 < self.s0_initial_strength <= 1.0:
            raise ValueError("s0_initial_strength 必须位于 (0, 1]")
        if self.decay_gamma < 0:
            raise ValueError("decay_gamma 不能小于 0")
        if not 0.0 <= self.decay_threshold <= 1.0:
            raise ValueError("decay_threshold 必须位于 [0, 1]")
        if self.time_unit_hours <= 0:
            raise ValueError("time_unit_hours 必须大于 0")
        if self.recycle_bin_retention_days < 0:
            raise ValueError("recycle_bin_retention_days 不能小于 0")
        if self.llm_request_timeout_seconds <= 0:
            raise ValueError("llm_request_timeout_seconds 必须大于 0")
        if self.llm_max_retries <= 0:
            raise ValueError("llm_max_retries 必须大于 0")
        if self.llm_retry_delay_seconds < 0:
            raise ValueError("llm_retry_delay_seconds 不能小于 0")
        if self.llm_request_interval_seconds < 0:
            raise ValueError("llm_request_interval_seconds 不能小于 0")
        if self.llm_reasoning_effort not in {None, "low", "high", "max"}:
            raise ValueError("LLM_REASONING_EFFORT 必须是 low/high/max")
        if self.llm_max_completion_tokens is not None and self.llm_max_completion_tokens <= 0:
            raise ValueError("LLM_MAX_COMPLETION_TOKENS 必须大于 0")
        if self.llm_context_window_tokens is not None and self.llm_context_window_tokens <= 0:
            raise ValueError("LLM_CONTEXT_WINDOW_TOKENS 必须大于 0")
        if self.context_reserve_tokens <= 0:
            raise ValueError("CONTEXT_RESERVE_TOKENS 必须大于 0")
        if not 0.5 <= self.context_compaction_ratio < 1.0:
            raise ValueError("CONTEXT_COMPACTION_RATIO 必须位于 [0.5, 1)")
        if self.context_keep_recent_tokens <= 0:
            raise ValueError("CONTEXT_KEEP_RECENT_TOKENS 必须大于 0")
        if self.provenance_excerpt_max_chars <= 0:
            raise ValueError("provenance_excerpt_max_chars 必须大于 0")
        if self.llm_provider_name != DEEPSEEK_PROVIDER_NAME:
            raise ValueError("本产品固定使用 DeepSeek Provider")
        if self.llm_model_name != DEEPSEEK_MODEL_NAME:
            raise ValueError("本产品固定使用 deepseek-v4-flash 模型")
        if self.llm_api_base.rstrip("/") != DEEPSEEK_API_BASE:
            raise ValueError("本产品固定使用 https://api.deepseek.com")
        if self.llm_api_key_env != DEEPSEEK_API_KEY_ENV:
            raise ValueError("本产品固定使用 DEEPSEEK_API_KEY 凭据变量")

    @classmethod
    def from_env(
        cls,
        default_db_path: str = "memory_store.db",
        environ: Optional[Mapping[str, str]] = None,
        config_path: Optional[str] = None,
    ) -> "MemoryConfig":
        """从指定 Agent 配置与 process env 构建配置；不修改环境或文件。"""
        if environ is None:
            load_local = os.environ.get("MEMORY_LOAD_LOCAL_ENV", "1").strip().lower()
            if load_local in {"0", "false", "no"}:
                local_env = {}
            else:
                # The application passes its Agent home config explicitly.
                # Direct callers use the global Codex-like Agent home. A
                # project-local dotenv is never an implicit production source.
                local_env = _load_local_env(
                    Path(config_path).expanduser() if config_path else _global_config_env_path()
                )
            env = {**local_env, **os.environ}
        else:
            env = dict(environ)
        # Provider selection is intentionally not configurable: accepting a
        # legacy provider/key selector here would make a stale local env file
        # silently route production traffic to a different API.
        api_key_env = DEEPSEEK_API_KEY_ENV
        api_key = env.get(DEEPSEEK_API_KEY_ENV)
        return cls(
            sqlite_db_path=env.get("MEMORY_DB_PATH", default_db_path),
            enable_forgetting=_env_bool(
                env.get("MEMORY_ENABLE_FORGETTING", env.get("MEMORY_ENABLE_TIME_DECAY", "false")),
                name="MEMORY_ENABLE_FORGETTING",
            ),
            enable_model_rerank=_env_bool(
                env.get("MEMORY_ENABLE_MODEL_RERANK", "false"),
                name="MEMORY_ENABLE_MODEL_RERANK",
            ),
            provider_mode=ProviderMode(
                env.get("MEMORY_PROVIDER_MODE", ProviderMode.REQUIRED.value)
            ),
            llm_provider_name=DEEPSEEK_PROVIDER_NAME,
            llm_api_key_env=api_key_env,
            llm_api_key=api_key,
            llm_api_base=DEEPSEEK_API_BASE,
            llm_model_name=DEEPSEEK_MODEL_NAME,
            llm_request_timeout_seconds=float(env.get("LLM_REQUEST_TIMEOUT_SECONDS", "60")),
            llm_max_retries=int(env.get("LLM_MAX_RETRIES", "3")),
            llm_retry_delay_seconds=float(env.get("LLM_RETRY_DELAY_SECONDS", "1")),
            llm_request_interval_seconds=float(
                env.get("LLM_REQUEST_INTERVAL_SECONDS", "0")
            ),
            llm_reasoning_effort=env.get("LLM_REASONING_EFFORT") or None,
            llm_max_completion_tokens=(
                int(env["DEEPSEEK_MAX_TOKENS"])
                if env.get("DEEPSEEK_MAX_TOKENS") else None
            ),
            llm_context_window_tokens=(
                int(env["LLM_CONTEXT_WINDOW_TOKENS"])
                if env.get("LLM_CONTEXT_WINDOW_TOKENS") else None
            ),
            context_reserve_tokens=int(env.get("CONTEXT_RESERVE_TOKENS", "8192")),
            context_compaction_ratio=float(env.get("CONTEXT_COMPACTION_RATIO", "0.78")),
            context_keep_recent_tokens=int(env.get("CONTEXT_KEEP_RECENT_TOKENS", "20000")),
            provenance_excerpt_max_chars=int(env.get("PROVENANCE_EXCERPT_MAX_CHARS", "240")),
        )
