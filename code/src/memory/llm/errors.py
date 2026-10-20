from typing import Optional


class ProviderError(RuntimeError):
    """Provider 层所有可预期失败的基类；消息不得包含凭据。"""

    def __init__(
        self,
        message: str,
        *,
        provider: str,
        operation: str,
        status_code: Optional[int] = None,
    ) -> None:
        super().__init__(message)
        self.provider = provider
        self.operation = operation
        self.status_code = status_code

    def as_dict(self) -> dict:
        return {
            "error_type": type(self).__name__,
            "provider": self.provider,
            "operation": self.operation,
            "status_code": self.status_code,
            "message": str(self),
        }

    def to_safe_projection(self) -> dict:
        """Returns safe, structured external projection without raw exception text."""
        if isinstance(self, ProviderConfigurationError):
            code = "PROVIDER_CONFIGURATION_ERROR"
            safe_msg = f"Provider configuration failed for {self.provider} ({self.operation})."
            retryable = False
        elif isinstance(self, ProviderResponseError):
            code = "PROVIDER_RESPONSE_ERROR"
            safe_msg = f"Provider returned invalid response for {self.provider} ({self.operation})."
            retryable = False
        elif isinstance(self, ProviderRequestError):
            code = "PROVIDER_REQUEST_ERROR"
            status_str = f" [status {self.status_code}]" if self.status_code else ""
            safe_msg = f"Provider request failed for {self.provider} ({self.operation}){status_str}."

            # Explicit retryability contract:
            # - Non-transient client errors (400, 401, 403, 404): retryable = False
            # - Transient rate limits or server errors (408, 429, 500, 502, 503, 504): retryable = True
            # - Network/timeout errors (status_code is None): retryable = True
            if self.status_code is None:
                retryable = True
            elif self.status_code in {408, 429, 500, 502, 503, 504}:
                retryable = True
            else:
                retryable = False
        else:
            code = "PROVIDER_ERROR"
            safe_msg = f"Provider error during {self.operation}."
            retryable = False

        return {
            "code": code,
            "category": "PROVIDER_ERROR",
            "provider": self.provider,
            "operation": self.operation,
            "status_code": self.status_code,
            "safe_message": safe_msg,
            "retryable": retryable,
        }


class ProviderConfigurationError(ProviderError):
    """REQUIRED 模式缺少必需配置。"""


class ProviderRequestError(ProviderError):
    """请求无法完成或 provider 返回 HTTP 错误。"""

    def __init__(self, message: str, **kwargs):
        super().__init__(message, **kwargs)
        self.error_type: Optional[str] = None
        self.error_code: Optional[str] = None
        self.request_id: Optional[str] = None
        self.retry_after: Optional[str] = None

    def as_dict(self) -> dict:
        data = super().as_dict()
        data.update({
            "provider_error_type": self.error_type,
            "provider_error_code": self.error_code,
            "request_id": self.request_id,
            "retry_after": self.retry_after,
        })
        return data

    def to_safe_projection(self) -> dict:
        data = super().to_safe_projection()
        data.update(
            {
                "provider_error_type": self.error_type,
                "provider_error_code": self.error_code,
                "request_id": self.request_id,
                "retry_after": self.retry_after,
            }
        )
        return data


class ContextWindowOverflowError(ProviderRequestError):
    """Provider rejected a request because its input exceeded the context window."""

    def to_safe_projection(self) -> dict:
        data = super().to_safe_projection()
        data.update(
            {
                "code": "CONTEXT_WINDOW_OVERFLOW",
                "safe_message": (
                    f"Provider context window was exceeded for {self.provider} "
                    f"({self.operation}); the conversation should be compacted and retried."
                ),
                "retryable": False,
            }
        )
        return data


class ProviderResponseError(ProviderError):
    """Provider 响应不符合约定的结构。"""
