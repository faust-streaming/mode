"""AsyncIO Service-based programming."""

__version__ = "0.0.1"

import typing
from collections.abc import Mapping, Sequence

# Lazy loading, via the PEP 562 module __getattr__ defined at the bottom
# of this file.
from typing import Any

# -eof meta-


if typing.TYPE_CHECKING:  # pragma: no cover
    from .services import Service, task, timer
    from .signals import BaseSignal, Signal, SyncSignal
    from .supervisors import (
        CrashingSupervisor,
        ForfeitOneForAllSupervisor,
        ForfeitOneForOneSupervisor,
        OneForAllSupervisor,
        OneForOneSupervisor,
        SupervisorStrategy,
    )
    from .types.services import ServiceT
    from .types.signals import BaseSignalT, SignalT, SyncSignalT
    from .types.supervisors import SupervisorStrategyT
    from .utils.logging import flight_recorder, get_logger, setup_logging
    from .utils.objects import label, shortlabel
    from .utils.times import Seconds, want_seconds
    from .worker import Worker

__all__ = [
    "BaseSignal",
    "BaseSignalT",
    "CrashingSupervisor",
    "ForfeitOneForAllSupervisor",
    "ForfeitOneForOneSupervisor",
    "OneForAllSupervisor",
    "OneForOneSupervisor",
    "Seconds",
    "Service",
    "ServiceT",
    "Signal",
    "SignalT",
    "SupervisorStrategy",
    "SupervisorStrategyT",
    "SyncSignal",
    "SyncSignalT",
    "Worker",
    "flight_recorder",
    "get_logger",
    "label",
    "setup_logging",
    "shortlabel",
    "task",
    "timer",
    "want_seconds",
]


all_by_module: Mapping[str, Sequence[str]] = {
    "mode.services": ["Service", "task", "timer"],
    "mode.signals": ["BaseSignal", "Signal", "SyncSignal"],
    "mode.supervisors": [
        "ForfeitOneForAllSupervisor",
        "ForfeitOneForOneSupervisor",
        "OneForAllSupervisor",
        "OneForOneSupervisor",
        "SupervisorStrategy",
        "CrashingSupervisor",
    ],
    "mode.types.services": ["ServiceT"],
    "mode.types.signals": ["BaseSignalT", "SignalT", "SyncSignalT"],
    "mode.types.supervisors": ["SupervisorStrategyT"],
    "mode.utils.times": ["Seconds", "want_seconds"],
    "mode.utils.logging": ["flight_recorder", "get_logger", "setup_logging"],
    "mode.utils.objects": ["label", "shortlabel"],
    "mode.worker": ["Worker"],
}

object_origins = {}
for module, items in all_by_module.items():
    for item in items:
        object_origins[item] = module


# NOTE: This is a :pep:`562` module-level ``__getattr__``, and deliberately
# *not* the older trick of defining a ``ModuleType`` subclass and swapping it
# into ``sys.modules[__name__]`` at the end of this file.
#
# That swap was a race: it only happened once the module body had finished,
# so a thread calling ``import mode`` while another thread was still
# executing this file could be handed the original, pre-swap module object --
# which has no ``__getattr__`` on it -- and every lazily-exported name below
# raised ``AttributeError: module 'mode' has no attribute 'Service'``.  The
# replacement module also carried no ``__spec__``, which denied the import
# machinery the ``_initializing`` flag it uses to make the second thread wait.
# Rare under the GIL, common on free-threaded (:pep:`703`) builds.
#
# A module ``__getattr__`` needs no swap at all, so the race cannot happen.
def __getattr__(name: str) -> Any:
    try:
        origin = object_origins[name]
    except KeyError:
        raise AttributeError(
            f"module {__name__!r} has no attribute {name!r}"
        ) from None
    module = __import__(origin, None, None, [name])
    # Bind every name this module provides, not just the requested one, so
    # that later lookups are plain globals and never reach __getattr__ again.
    namespace = globals()
    for extra_name in all_by_module[origin]:
        namespace[extra_name] = getattr(module, extra_name)
    return namespace[name]


def __dir__() -> Sequence[str]:
    return [
        *__all__,
        "__file__",
        "__path__",
        "__doc__",
        "__all__",
        "__docformat__",
        "__name__",
        "VERSION",
        "version_info",
        "__package__",
        "__version__",
    ]
