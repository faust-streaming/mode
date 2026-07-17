"""AsyncIO Service-based programming."""

__version__ = "0.0.1"

import sys
import typing
from collections.abc import Mapping, Sequence

# Lazy loading.
# - See werkzeug/__init__.py for the rationale behind this.
from types import ModuleType
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


class _module(ModuleType):
    """Customized Python module."""

    def __getattr__(self, name: str) -> Any:
        if name in object_origins:
            module = __import__(object_origins[name], None, None, [name])
            for extra_name in all_by_module[module.__name__]:
                setattr(self, extra_name, getattr(module, extra_name))
            return getattr(module, name)
        return ModuleType.__getattribute__(self, name)

    def __dir__(self) -> Sequence[str]:
        result = list(new_module.__all__)
        result.extend(
            (
                "__file__",
                "__path__",
                "__doc__",
                "__all__",
                "__docformat__",
                "__name__",
                "__path__",
                "VERSION",
                "version_info",
                "__package__",
            )
        )
        return result


# keep a reference to this module so that it's not garbage collected
old_module = sys.modules[__name__]

new_module = sys.modules[__name__] = _module(__name__)
new_module.__dict__.update(
    {
        "__file__": __file__,
        "__path__": __path__,  # type: ignore
        "__doc__": __doc__,
        "__all__": tuple(object_origins),
        "__version__": __version__,
        "__package__": __package__,
    }
)
