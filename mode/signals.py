"""Signals - implementation of the Observer pattern."""

import asyncio
from collections import defaultdict
from collections.abc import Iterable, Mapping, MutableSet
from functools import partial
from types import MethodType
from typing import Any, Callable, Optional, Union, cast, no_type_check
from weakref import ReferenceType, WeakMethod, ref

from .types.signals import (
    BaseSignalT,
    FilterReceiverMapping,
    SignalHandlerRefT,
    SignalHandlerT,
    SignalT,
    SyncSignalT,
    T,
)
from .utils.futures import maybe_async

__all__ = ["BaseSignal", "Signal", "SyncSignal"]


class BaseSignal(BaseSignalT[T]):
    """Base class for signal/observer pattern."""

    _receivers: MutableSet[SignalHandlerRefT]
    _filter_receivers: FilterReceiverMapping

    def __init__(
        self,
        *,
        name: Optional[str] = None,
        owner: Optional[type] = None,
        loop: Optional[asyncio.AbstractEventLoop] = None,
        default_sender: Any = None,
        receivers: Optional[MutableSet[SignalHandlerRefT]] = None,
        filter_receivers: Union[FilterReceiverMapping, None] = None,
    ) -> None:
        self.name = name or ""
        self.owner = owner
        self.loop = loop
        self.default_sender = default_sender
        self._receivers = receivers if receivers is not None else set()
        if filter_receivers is None:
            filter_receivers = defaultdict(set)
        self._filter_receivers = filter_receivers

    def asdict(self) -> Mapping[str, Any]:
        return {
            "name": self.name,
            "owner": self.owner,
            "loop": self.loop,
            "default_sender": self.default_sender,
        }

    def clone(self, **kwargs: Any) -> BaseSignalT:
        return self._clone(**kwargs)

    def with_default_sender(self, sender: Any = None) -> BaseSignalT:
        return self._with_default_sender(sender)

    def _clone(self, **kwargs: Any) -> BaseSignalT:
        return type(self)(**{**self.asdict(), **kwargs})

    def _with_default_sender(self, sender: Any = None) -> BaseSignalT:
        if sender is None:
            sender = self.default_sender
        return self.clone(
            default_sender=sender,
            receivers=self._receivers,
            filter_receivers=self._filter_receivers,
        )

    def __set_name__(self, owner: type, name: str) -> None:
        """If signal is an attribute of a class, we use __set_name__ to show the location of the signal in __repr__.

        Examples:

        ```python
        class X(Service):
            starting = Signal()

        >>> X.starting
        <Signal: X.string>
        ```
        """
        if not self.name:
            self.name = name
        self.owner = owner

    def unpack_sender_from_args(self, *args: Any) -> tuple[T, tuple[Any, ...]]:
        sender = self.default_sender
        if sender is None:
            if not args or len(args) == 0:
                raise TypeError("Signal.send requires at least one argument")

            if len(args) > 1:
                sender, *args = args  # type: ignore
            else:
                sender, args = args[0], ()
        return sender, args

    def connect(
        self, fun: Union[SignalHandlerT, None] = None, **kwargs: Any
    ) -> Callable:
        if fun is not None:
            return self._connect(fun, **kwargs)
        return partial(self._connect, **kwargs)

    def _connect(
        self, fun: SignalHandlerT, *, weak: bool = False, sender: Any = None
    ) -> SignalHandlerT:
        ref: SignalHandlerRefT
        # NOTE: A strong receiver is stored as the handler itself,
        # unwrapped.  Handlers already hash and compare the way
        # `disconnect` needs (functions by identity, bound methods by
        # ``(__func__, __self__)``), and keeping Python-level
        # __hash__/__eq__ out of the receiver set keeps `set.add` and
        # `set.discard` atomic -- a wrapper re-entering the interpreter
        # mid-operation reliably wedged PyPy; see docs/free-threading.md.
        ref = self._create_ref(fun) if weak else fun
        if self.default_sender is not None:
            sender = self.default_sender
        if sender is None:
            self._receivers.add(ref)
        else:
            self._filter_receivers[self._create_id(sender)].add(ref)
        return fun

    def disconnect(
        self, fun: SignalHandlerT, *, weak: bool = False, sender: Any = None
    ) -> None:
        ref: SignalHandlerRefT
        # Mirrors `_connect`: a strong receiver is the handler itself, so
        # the value built here compares equal to the stored entry.  (It
        # was once a fresh ``lambda: fun``, which never matched -- making
        # disconnect a silent no-op for strong receivers.)
        ref = self._create_ref(fun) if weak else fun
        if self.default_sender is not None:
            sender = self.default_sender
        if sender is None:
            self._receivers.discard(ref)
        else:
            try:
                # `discard`, not `remove`: disconnecting a receiver that
                # was never connected for this sender is not an error, and
                # `set.remove` signals it with KeyError -- which the
                # `except ValueError` below never caught.  That clause is
                # for `_create_id`, whose hash() of the sender is what can
                # raise here.
                self._filter_receivers[self._create_id(sender)].discard(ref)
            except ValueError:
                pass

    def iter_receivers(self, sender: object) -> Iterable[SignalHandlerT]:
        if self._receivers or self._filter_receivers:
            r = self._update_receivers(self._receivers)
            if sender is not None:
                sender_id = self._create_id(sender)
                r.update(
                    self._update_receivers(self._filter_receivers[sender_id])
                )
            yield from r

    def _update_receivers(
        self, r: MutableSet[SignalHandlerRefT]
    ) -> set[SignalHandlerT]:
        live_receivers, dead_refs = self._get_live_receivers(r)
        for href in dead_refs:
            r.discard(href)
        return live_receivers

    def _get_live_receivers(
        self, r: MutableSet[SignalHandlerRefT]
    ) -> tuple[set[SignalHandlerT], set[SignalHandlerRefT]]:
        live_receivers: set[SignalHandlerT] = set()
        dead_refs: set[SignalHandlerRefT] = set()
        # NOTE: Iterate a snapshot.  `r` is the live receiver set shared by
        # this signal and every clone of it, and `connect`/`disconnect`
        # mutate it from whatever thread or task calls them -- iterating it
        # directly raises "Set changed size during iteration".  The caller
        # also discards dead refs from `r` using what this returns, which
        # is itself a mutation during iteration.
        #
        # It must be `list(r)`, NOT `tuple(r)`: on free-threaded builds
        # `list()` (like `set()` and `set.copy()`) takes the source set's
        # per-object lock for the duration of the copy, while `tuple()`
        # falls back to the generic iterator protocol and does not -- so
        # `tuple(r)` raises the very error this snapshot exists to avoid.
        for href in list(r):
            alive, value = self._is_alive(href)
            if alive and value is not None:
                live_receivers.add(value)
            else:
                dead_refs.add(href)
        return live_receivers, dead_refs

    def _is_alive(
        self, ref: SignalHandlerRefT
    ) -> tuple[bool, Optional[SignalHandlerT]]:
        if isinstance(ref, ReferenceType):
            value = ref()
            return value is not None, value
        # Anything that is not a weak reference was connected with
        # ``weak=False``, which `_connect` stores as the handler itself:
        # alive by construction, and already the value to return.
        return True, cast(SignalHandlerT, ref)

    def _create_ref(self, fun: SignalHandlerT) -> SignalHandlerRefT:
        if hasattr(fun, "__func__") and hasattr(fun, "__self__"):
            return cast(SignalHandlerRefT, WeakMethod(cast(MethodType, fun)))
        else:
            return cast(SignalHandlerRefT, ref(fun))

    def _create_id(self, sender: Any) -> int:
        try:
            return hash((sender.__func__, sender.__self__))
        except AttributeError:
            return hash(sender)

    @property
    def ident(self) -> str:
        # XXX compat: deprecate remove in future versions
        return self.label

    @property
    def label(self) -> str:
        if self.owner:
            return f"{self.owner.__qualname__}.{self.name}"
        return self.name

    def __repr__(self) -> str:
        info = ""
        if self.default_sender:
            info = f" sender={self.default_sender!r}"
        return f"<{type(self).__name__}: {self.label}{info}>"


class Signal(BaseSignal[T], SignalT[T]):
    """Asynchronous signal (using ``async def`` functions)."""

    async def __call__(self, *args: Any, **kwargs: Any) -> None:
        await self.send(*args, **kwargs)

    async def send(self, *args: Any, **kwargs: Any) -> None:
        sender, args = self.unpack_sender_from_args(*args)
        for receiver in self.iter_receivers(sender):
            await maybe_async(receiver(sender, *args, signal=self, **kwargs))

    @no_type_check
    def clone(self, **kwargs: Any) -> SignalT:
        return cast(Signal, self._clone(**kwargs))

    @no_type_check
    def with_default_sender(self, sender: Any = None) -> SignalT:
        return cast(Signal, self._with_default_sender(sender))


class SyncSignal(BaseSignal[T], SyncSignalT[T]):
    """Signal that is synchronous (using regular ``def`` functions)."""

    def __call__(self, *args: Any, **kwargs: Any) -> None:
        self.send(*args, **kwargs)

    def send(self, *args: Any, **kwargs: Any) -> None:
        sender, args = self.unpack_sender_from_args(*args)
        for receiver in self.iter_receivers(sender):
            receiver(sender, *args, signal=self, **kwargs)

    @no_type_check
    def clone(self, **kwargs: Any) -> SyncSignalT:
        return cast(SyncSignal, self._clone(**kwargs))

    @no_type_check
    def with_default_sender(self, sender: Any = None) -> SyncSignalT:
        return cast(SyncSignal, self._with_default_sender(sender))
