from typing import Any
from unittest.mock import Mock
from weakref import ref

import pytest

from mode import label
from mode.signals import Signal, SignalT, SyncSignal, SyncSignalT


class X:
    on_started: SignalT = Signal()
    on_stopped: SignalT = Signal()

    def __init__(self):
        self.on_started = self.on_started.with_default_sender(self)
        self.on_stopped = self.on_stopped.with_default_sender(self)


class Y(X): ...


class SyncX:
    on_started: SyncSignalT = SyncSignal()
    on_stopped: SyncSignalT = SyncSignal()

    def __init__(self):
        self.on_started = self.on_started.with_default_sender(self)
        self.on_stopped = self.on_stopped.with_default_sender(self)


def test_clone():
    assert X.on_started.clone()


def test_with_default_sender():
    assert X.on_started.with_default_sender(42).default_sender == 42


def test_disconnect_value_error():
    X.on_started._create_id = Mock(side_effect=ValueError())
    X.default_sender = Mock()
    X.on_started.disconnect(Mock())


@pytest.mark.asyncio
async def test_signals():
    x, y = X(), Y()

    on_stopped_mock = Mock()
    on_started_mock = Mock()

    @y.on_stopped.connect
    async def my_on_stopped(self, value, **kwargs):
        on_stopped_mock(self, value)

    @y.on_started.connect
    async def my_on_started(self, value, **kwargs):
        on_started_mock(self, value)

    await y.on_started.send(value=1)
    on_started_mock.assert_called_once_with(y, 1)

    await y.on_stopped.send(value=2)
    on_stopped_mock.assert_called_once_with(y, 2)
    assert on_started_mock.call_count == 1
    await x.on_started.send(value=3)
    await x.on_stopped(value=4)

    assert x.on_started.ident
    assert label(x.on_started)
    assert repr(x.on_started)

    assert on_started_mock.call_count == 1
    assert on_stopped_mock.call_count == 1


def test_sync_signals():
    x = SyncX()
    x2 = SyncX()

    on_stopped_mock = Mock()
    on_started_mock = Mock()

    @x.on_stopped.connect
    def my_on_stopped(self, code: int, reason: str, **kwargs: Any) -> None:
        assert kwargs["signal"] == x.on_stopped
        on_stopped_mock(self, code, reason)

    @x.on_started.connect
    def my_on_started(self, **kwargs: Any) -> None:
        assert kwargs["signal"] == x.on_started
        on_started_mock(self)

    x.on_started.send()
    on_started_mock.assert_called_once_with(x)

    x.on_stopped.send(303, "sorry not sorry")
    on_stopped_mock.assert_called_once_with(x, 303, "sorry not sorry")
    assert on_started_mock.call_count == 1

    assert x.on_started.ident
    assert label(x.on_started)
    assert label(X.on_started)
    assert repr(x.on_started)
    assert repr(X.on_started)

    prev, x.on_started.owner = x.on_started.owner, None
    assert label(x.on_started)
    x.on_started.owner = prev

    x.on_started()
    assert on_started_mock.call_count == 2

    x2.on_started.send()
    x2.on_started.send()

    assert on_started_mock.call_count == 2

    new_sender = Mock()
    sig2 = x2.on_started.clone(default_sender=new_sender)
    assert sig2.default_sender == new_sender

    sig3 = sig2.with_default_sender(None)
    assert sig3.default_sender == sig2.default_sender

    new_sender2 = Mock()
    sig4 = sig3.with_default_sender(new_sender2)
    assert sig4.default_sender == new_sender2

    sig4.name = ""
    sig4.__set_name__(sig3, "foo")
    assert sig4.name == "foo"
    assert sig4.owner == sig3
    sig4.__set_name__(sig2, "bar")
    assert sig4.name == "foo"
    assert sig4.owner == sig2

    sig4.default_sender = None
    with pytest.raises(TypeError):
        sig4.unpack_sender_from_args()
    assert sig4.unpack_sender_from_args(1) == (1, ())
    assert sig4.unpack_sender_from_args(1, 2) == (1, [2])

    partial_yes = sig4.connect(None)
    mockfun = Mock()
    partial_yes(mockfun)
    sig4.disconnect(mockfun)

    sig2.connect(mockfun, weak=True)
    sig2.disconnect(mockfun, weak=True)


def test_signal_name():
    # Signal should have .name attribute set when created
    # as a field in a class:

    class X:
        sig = Signal()
        sig2 = SyncSignal()

    assert X.sig.name == "sig"
    assert X.sig.owner is X
    assert X.sig2.name == "sig2"
    assert X.sig2.owner is X


class test_BaseSignal:
    @pytest.fixture
    def sig(self):
        return Signal()

    def test_with_default_sender(self, sig):
        sender = Mock()
        sig2 = super(type(sig), sig).with_default_sender(sender)
        assert sig2.default_sender is sender

        sig3 = super(type(sig2), sig2).clone()
        assert sig3.asdict() == sig2.asdict()

    def test_disconnect_discards_the_handler_itself(self, sig):
        # A strong receiver is stored unwrapped, so the value handed to
        # `discard` is the handler.  It used to be a freshly built
        # ``lambda: fun``, which could never equal the one `connect`
        # stored, so the discard matched nothing.
        sig._receivers = Mock()
        r = Mock()
        sig.disconnect(r, sender=None)
        sig._receivers.discard.assert_called_once_with(r)

    def test_disconnect_raises(self, sig):
        sig._create_id = Mock(side_effect=ValueError())
        sig.disconnect(Mock(), sender=Mock())

    def test_iter_receivers(self, sig):
        receivers, alive_refs, _dead_refs = self.create_refs(sig)
        sig._receivers = receivers
        sig._live_receivers = set()
        sig._update_receivers = Mock(return_value=alive_refs)
        assert list(sig.iter_receivers(None)) == alive_refs

    def test_iter_receivers_no_receivers(self, sig):
        sig._receivers = set()
        sig._filter_receivers = set()
        assert list(sig.iter_receivers(None)) == []

    def test__get_live_receivers(self, sig):
        receivers, alive_refs, _dead_refs = self.create_refs(sig)
        sig._get_live_receivers(receivers)
        sig._update_receivers(receivers)
        assert receivers == set(alive_refs)

    def create_refs(self, sig):
        sig._is_alive = Mock()

        def is_alive(x):
            return x.alive, x

        sig._is_alive.side_effect = is_alive

        alive_refs = [Mock(alive=True), Mock(alive=True)]
        dead_refs = [Mock(alive=False)]

        receivers = set(alive_refs + dead_refs)

        return receivers, alive_refs, dead_refs

    def test__is_alive(self, sig):
        class Object:
            value = None

        x = Object()
        x.value = 10

        async def handler(*args, **kwargs): ...

        # Not a weakref -- a strong receiver, stored as the handler
        # itself, so it is returned as-is rather than called.
        assert sig._is_alive(handler) == (True, handler)
        assert sig._is_alive(ref(x)) == (True, x)

    def test_create_ref_methods(self, sig):
        class X:
            def foo(self, **kwargs):
                return 42

        assert sig._create_ref(X.foo)
        assert sig._create_ref(X().foo)


@pytest.fixture
def handler():
    async def handler(*args: Any, **kwargs: Any) -> None: ...

    return handler


class test_disconnect_removes_the_receiver:
    """`disconnect` has to undo `connect`, strong references included.

    Strong receivers used to be stored as ``lambda: fun``, and
    `disconnect` built a *second* lambda to look up.  Two lambdas never
    compare equal, so the `discard` matched nothing and the handler stayed
    connected -- and stayed subscribed to every subsequent send.
    """

    def test_strong_receiver(self, handler):
        sig = Signal()
        sig.connect(handler)
        assert len(sig._receivers) == 1

        sig.disconnect(handler)
        assert not sig._receivers

    def test_weak_receiver(self, handler):
        sig = Signal()
        sig.connect(handler, weak=True)
        assert len(sig._receivers) == 1

        sig.disconnect(handler, weak=True)
        assert not sig._receivers

    def test_strong_bound_method(self):
        class Owner:
            async def handler(self, *args: Any, **kwargs: Any) -> None: ...

        owner = Owner()
        sig = Signal()
        # `owner.handler` is a fresh bound method object on every attribute
        # access, so this only works if equality is what decides, not
        # identity.
        sig.connect(owner.handler)
        sig.disconnect(owner.handler)
        assert not sig._receivers

    def test_connect_is_still_idempotent(self, handler):
        sig = Signal()
        sig.connect(handler)
        sig.connect(handler)
        assert len(sig._receivers) == 1

    def test_only_the_named_receiver_is_removed(self, handler):
        async def other(*args: Any, **kwargs: Any) -> None: ...

        sig = Signal()
        sig.connect(handler)
        sig.connect(other)

        sig.disconnect(handler)
        assert set(sig._receivers) == {other}

    def test_disconnected_receiver_stops_being_iterated(self, handler):
        sender = object()
        sig = Signal()
        sig.connect(handler)
        assert list(sig.iter_receivers(sender)) == [handler]

        sig.disconnect(handler)
        assert list(sig.iter_receivers(sender)) == []

    def test_sender_specific_receiver(self, handler):
        sender = object()
        sig = Signal()
        sig.connect(handler, sender=sender)
        assert sig._filter_receivers[sig._create_id(sender)]

        sig.disconnect(handler, sender=sender)
        assert not sig._filter_receivers[sig._create_id(sender)]

    def test_sender_specific_disconnect_of_unknown_receiver(self, handler):
        # `set.remove` raised KeyError here, which the `except ValueError`
        # around it never caught.
        sig = Signal()
        sig.connect(handler, sender=object())
        sig.disconnect(handler, sender=object())

    def test_disconnect_of_never_connected_receiver(self, handler):
        sig = Signal()
        sig.disconnect(handler)
        sig.disconnect(handler, sender=object())

    def test_default_sender_disconnect(self, handler):
        x = X()
        x.on_started.connect(handler)
        assert x.on_started._filter_receivers[x.on_started._create_id(x)]

        x.on_started.disconnect(handler)
        assert not x.on_started._filter_receivers[x.on_started._create_id(x)]


class test_strong_receivers_are_stored_unwrapped:
    """A ``weak=False`` receiver is kept in the set as the handler itself.

    Nothing wraps it.  That is what lets `disconnect` find it -- functions
    hash and compare by identity, bound methods by
    ``(__func__, __self__)`` -- and it keeps every `set` operation on the
    receiver set free of Python-level ``__hash__``/``__eq__``, which would
    otherwise re-enter the interpreter mid-operation and let another
    thread mutate the set underneath it.
    """

    def test_the_stored_receiver_is_the_handler_itself(self, handler):
        # `_is_alive` distinguishes weak from strong by asking whether the
        # entry is a `weakref`, so a strong entry must be the handler and
        # not something that returns it when called.
        sig = Signal()
        sig.connect(handler)
        (stored,) = sig._receivers
        assert stored is handler
        assert sig._is_alive(stored) == (True, handler)

    def test_weak_and_strong_receivers_coexist(self):
        async def strong(*args: Any, **kwargs: Any) -> None: ...

        async def weak(*args: Any, **kwargs: Any) -> None: ...

        sig = Signal()
        sig.connect(strong)
        sig.connect(weak, weak=True)
        assert set(sig.iter_receivers(object())) == {strong, weak}

    def test_hashing_is_not_implemented_in_python(self, handler):
        # The point of storing the handler bare: `set.add`/`set.discard`
        # must not call back into Python to hash or compare an entry.
        sig = Signal()
        sig.connect(handler)
        (stored,) = sig._receivers
        assert type(stored).__hash__ is object.__hash__
        assert type(stored).__eq__ is object.__eq__

    def test_unhashable_handler_is_rejected_at_connect(self):
        # A handler that cannot be hashed cannot go in the receiver set.
        # It never worked: `lambda: fun` let `connect` succeed, and then
        # the first `send` blew up in `_get_live_receivers`, which
        # collects the dereferenced handlers into a set of their own.
        # Failing at registration points at the handler instead.
        class Unhashable:
            __hash__ = None  # type: ignore[assignment]

            async def __call__(self, *args: Any, **kwargs: Any) -> None: ...

        sig = Signal()
        with pytest.raises(TypeError):
            sig.connect(Unhashable())
        assert not sig._receivers
