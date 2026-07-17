"""Context manager utilities."""

from contextlib import AbstractAsyncContextManager
from types import TracebackType
from typing import Any, Optional

__all__ = ["asyncnullcontext"]


class asyncnullcontext(AbstractAsyncContextManager):
    """Context for async-with statement doing nothing."""

    enter_result: Any

    def __init__(self, enter_result: Any = None) -> None:
        self.enter_result = enter_result

    async def __aenter__(self) -> Any:
        return self.enter_result

    async def __aexit__(
        self,
        exc_type: Optional[type[BaseException]] = None,
        exc_val: Optional[BaseException] = None,
        exc_tb: Optional[TracebackType] = None,
    ) -> None: ...
