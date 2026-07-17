"""Type classes for `mode.supervisors`."""

import abc
import typing
from collections.abc import Awaitable
from typing import Any, Callable, Optional

from mode.utils.times import Seconds

if typing.TYPE_CHECKING:
    from .services import ServiceT
else:

    class ServiceT: ...


__all__ = ["SupervisorStrategyT"]

ReplacementT = Callable[[ServiceT, int], Awaitable[ServiceT]]


class SupervisorStrategyT(ServiceT):
    """Base type for all supervisor strategies."""

    max_restarts: float
    over: float
    raises: type[BaseException]

    @abc.abstractmethod
    def __init__(
        self,
        *services: ServiceT,
        max_restarts: Seconds = 100.0,
        over: Seconds = 1.0,
        raises: Optional[type[BaseException]] = None,
        replacement: Optional[ReplacementT] = None,
        **kwargs: Any,
    ) -> None:
        self.replacement: Optional[ReplacementT] = replacement

    @abc.abstractmethod
    def wakeup(self) -> None: ...

    @abc.abstractmethod
    def add(self, *services: ServiceT) -> None: ...

    @abc.abstractmethod
    def discard(self, *services: ServiceT) -> None: ...

    @abc.abstractmethod
    def service_operational(self, service: ServiceT) -> bool: ...

    @abc.abstractmethod
    async def restart_service(self, service: ServiceT) -> None: ...
