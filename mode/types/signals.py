"""Type classes for `mode.signals`."""

import abc
import asyncio
import typing
from collections.abc import Awaitable, MutableMapping, MutableSet
from typing import Any, Callable, Generic, Optional, TypeVar, Union

from mypy_extensions import KwArg, NamedArg, VarArg

__all__ = [
    "BaseSignalT",
    "FilterReceiverMapping",
    "SignalHandlerRefT",
    "SignalHandlerT",
    "SignalT",
    "SyncSignalT",
    "T",
    "T_contra",
]

T = TypeVar("T")
T_contra = TypeVar("T_contra", contravariant=True)

SignalHandlerT = Union[
    Callable[
        [T, VarArg(), NamedArg("BaseSignalT", name="signal"), KwArg()], None
    ],
    Callable[
        [T, VarArg(), NamedArg("BaseSignalT", name="signal"), KwArg()],
        Awaitable[None],
    ],
]

SignalHandlerRefT = Union[Callable[[], SignalHandlerT], SignalHandlerT]
FilterReceiverMapping = MutableMapping[Any, MutableSet[SignalHandlerRefT]]


class BaseSignalT(Generic[T]):
    """Base type for all signals."""

    name: str
    owner: Optional[type]

    @abc.abstractmethod
    def __init__(
        self,
        *,
        name: Optional[str] = None,
        owner: Optional[type] = None,
        loop: Optional[asyncio.AbstractEventLoop] = None,
        default_sender: Any = None,
        receivers: Optional[MutableSet[SignalHandlerRefT]] = None,
        filter_receivers: Optional[FilterReceiverMapping] = None,
    ) -> None: ...

    @abc.abstractmethod
    def clone(self, **kwargs: Any) -> "BaseSignalT": ...

    @abc.abstractmethod
    def with_default_sender(self, sender: Any = None) -> "BaseSignalT": ...

    @abc.abstractmethod
    def connect(self, fun: SignalHandlerT, **kwargs: Any) -> Callable: ...

    @abc.abstractmethod
    def disconnect(
        self, fun: SignalHandlerT, *, sender: Any = None, weak: bool = True
    ) -> None: ...


class SignalT(BaseSignalT[T]):
    """Base class for all async signals (using ``async def``)."""

    @abc.abstractmethod
    async def __call__(
        self, sender: T_contra, *args: Any, **kwargs: Any
    ) -> None: ...

    @abc.abstractmethod
    async def send(
        self, sender: T_contra, *args: Any, **kwargs: Any
    ) -> None: ...

    @typing.no_type_check
    @abc.abstractmethod
    def clone(self, **kwargs: Any) -> "SignalT": ...

    @typing.no_type_check
    @abc.abstractmethod
    def with_default_sender(self, sender: Any = None) -> "SignalT": ...


class SyncSignalT(BaseSignalT[T]):
    """Base class for all synchronous signals (using regular ``def``)."""

    @abc.abstractmethod
    def __call__(
        self, sender: T_contra, *args: Any, **kwargs: Any
    ) -> None: ...

    @abc.abstractmethod
    def send(self, sender: T_contra, *args: Any, **kwargs: Any) -> None: ...

    @typing.no_type_check
    @abc.abstractmethod
    def clone(self, **kwargs: Any) -> "SyncSignalT": ...

    @typing.no_type_check
    @abc.abstractmethod
    def with_default_sender(self, sender: Any = None) -> "SyncSignalT": ...
