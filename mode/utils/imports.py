"""Importing utilities."""

import importlib
import os
import sys
import typing
import warnings
from collections.abc import (
    Generator,
    Iterable,
    Iterator,
    Mapping,
    MutableMapping,
)
from contextlib import contextmanager, suppress
from types import ModuleType
from typing import (
    Any,
    Callable,
    Generic,
    NamedTuple,
    Optional,
    TypeVar,
    Union,
    cast,
)

try:
    from importlib.metadata import entry_points  # Python >= 3.10
except ImportError:
    from importlib_metadata import entry_points  # type: ignore # Python < 3.10


from .collections import FastUserDict
from .objects import cached_property
from .text import didyoumean

if typing.TYPE_CHECKING:
    from yarl import URL
else:
    try:
        from yarl import URL
    except ImportError:  # pragma: no cover

        class URL:
            def __init__(self, url: str) -> None:
                assert "://" in url
                self.scheme = url.split("://")[0]


# - these are taken from kombu.utils.imports

__all__ = [
    "FactoryMapping",
    "SymbolArg",
    "cwd_in_path",
    "import_from_cwd",
    "load_extension_class_names",
    "load_extension_classes",
    "smart_import",
    "symbol_by_name",
]

_T = TypeVar("_T")
SymbolArg = Union[_T, str]


class FactoryMapping(FastUserDict, Generic[_T]):
    """Class plugin system.

    This is an utility to maintain a mapping from name to fully
    qualified Python attribute path, and also supporting the use
    of these in URLs.

    ```sh
    >>> # Specifying the type enables mypy to know that
    >>> # this factory returns Driver subclasses.
    >>> drivers: FactoryMapping[Type[Driver]]
    >>> drivers = FactoryMapping({
    ...    'rabbitmq': 'my.drivers.rabbitmq:Driver',
    ...    'kafka': 'my.drivers.kafka:Driver',
    ...    'redis': 'my.drivers.redis:Driver',
    ... })

    >>> drivers.by_url('rabbitmq://localhost:9090')
    <class 'my.drivers.rabbitmq.Driver'>

    >>> drivers.by_name('redis')
    <class 'my.drivers.redis.Driver'>
    ```
    """

    aliases: MutableMapping[str, str]
    namespaces: set
    _finalized: bool = False

    def __init__(self, *args: Mapping, **kwargs: str) -> None:
        self.aliases = dict(*args, **kwargs)
        self.namespaces = set()

    def iterate(self) -> Iterator[_T]:
        self._maybe_finalize()
        for name in self.aliases:
            yield self.by_name(name)

    def by_url(self, url: Union[str, URL]) -> _T:
        """Get class associated with URL (scheme is used as alias key)."""
        # we remove anything after ; so urlparse can recognize the url.
        return self.by_name(URL(url).scheme)

    def by_name(self, name: SymbolArg[_T]) -> _T:
        self._maybe_finalize()
        try:
            return symbol_by_name(name, aliases=self.aliases)
        except ModuleNotFoundError as exc:
            name_ = cast(str, name)
            if "." in name_:
                raise
            alt = didyoumean(
                self.aliases,
                name_,
                fmt_none=f"Available choices: {', '.join(self.aliases)}",
            )
            raise ModuleNotFoundError(
                f"{name!r} is not a valid name. {alt}"
            ) from exc

    def get_alias(self, name: str) -> str:
        self._maybe_finalize()
        return self.aliases[name]

    def include_setuptools_namespace(self, namespace: str) -> None:
        self.namespaces.add(namespace)

    def _maybe_finalize(self) -> None:
        if not self._finalized:
            self._finalized = True
            self._finalize()

    def _finalize(self) -> None:
        for namespace in self.namespaces:
            self.aliases.update(dict(load_extension_class_names(namespace)))

    @cached_property
    def data(self) -> MutableMapping:  # type: ignore
        return self.aliases


def _ensure_identifier(path: str, full: str) -> None:
    for part in path.split("."):
        if not part.isidentifier():
            raise ValueError(
                f"Component {part!r} of {full!r} is not a valid identifier"
            )


class ParsedSymbol(NamedTuple):
    """Tuple returned by `parse_symbol`."""

    module_name: Optional[str]
    attribute_name: Optional[str]


def parse_symbol(
    s: str,
    *,
    package: Optional[str] = None,
    strict_separator: str = ":",
    relative_separator: str = ".",
) -> ParsedSymbol:
    """Parse `symbol_by_name` argument into components.

    Returns:
        ParsedSymbol: Tuple of ``(module_name, attribute_name)``

    Raises:
        ValueError: if relative import (arg starts with '.') and
            no ``package`` argument is specified.

    Examples:

    ```sh
    >>> parse_symbol('mode.services')
    ParsedSymbol(module_name='mode.services', attribute_name=None)

    >>> parse_symbol('.services', package='mode')
    ParsedSymbol(module_name='.services', attribute_name=None)

    >>> parse_symbol('mode.services.Service')
    ParsedSymbol(module_name='mode.services', attribute_name='Service')

    >>> parse_symbol('mode.services:Service')
    ParsedSymbol(module_name='mode.services', attribute_name='Service')
    ```
    """
    module_name: Optional[str]
    attribute_name: Optional[str]
    partition_by = (
        strict_separator if strict_separator in s else relative_separator
    )

    module_name, used_separator, attribute_name = s.rpartition(partition_by)
    if not module_name:
        # Module name is missing must be either ".foo" or ":foo",
        # and is a relative import.
        if used_separator == ":":
            # ":foo" is illegal and will result in ValueError below.
            raise ValueError(f'Missing module name with ":" separator: {s!r}')
        elif used_separator == ".":
            # ".foo" is legal but requires a ``package`` argument.
            if not package:
                raise ValueError(
                    f"Relative import {s!r} but package=None (required)"
                )
            module_name, attribute_name = s, None
        else:
            attribute_name, module_name = (
                None,
                package if package else attribute_name,
            )

    if attribute_name:
        _ensure_identifier(attribute_name, full=s)
    if module_name:  # pragma: no cover
        _ensure_identifier(module_name.strip(relative_separator), full=s)

    return ParsedSymbol(module_name, attribute_name)


def symbol_by_name(
    name: SymbolArg[_T],
    aliases: Optional[Mapping[str, str]] = None,
    imp: Any = None,
    package: Optional[str] = None,
    sep: str = ".",
    default: _T = None,
    **kwargs: Any,
) -> _T:
    """Get symbol by qualified name.

    The name should be the full dot-separated path to the class:

        modulename.ClassName

    Example:

    ```python
    mazecache.backends.redis.RedisBackend
                            ^- class name
    ```

    or using ':' to separate module and symbol:

    ```python
    mazecache.backends.redis:RedisBackend
    ```

    If `aliases` is provided, a dict containing short name/long name
    mappings, the name is looked up in the aliases first.

    Examples:

    ```sh
    >>> symbol_by_name('mazecache.backends.redis:RedisBackend')
    <class 'mazecache.backends.redis.RedisBackend'>

    >>> symbol_by_name('default', {
    ...     'default': 'mazecache.backends.redis:RedisBackend'})
    <class 'mazecache.backends.redis.RedisBackend'>

    # Does not try to look up non-string names.
    >>> from mazecache.backends.redis import RedisBackend
    >>> symbol_by_name(RedisBackend) is RedisBackend
    True
    ```
    """
    # This code was copied from kombu.utils.symbol_by_name
    imp = importlib.import_module if imp is None else imp

    if not isinstance(name, str):
        return name  # already a class
    name = (aliases or {}).get(name) or name

    module_name, attribute_name = parse_symbol(name, package=package)

    try:
        try:
            module = imp(  # type: ignore
                module_name or "",
                package=package,
                # kwargs can be used to extend symbol_by_name when a custom
                # `imp` function is used.
                # importib does not support additional arguments
                # beyond (name, package=None), so we have to silence
                # mypy error here.
                **kwargs,
            )
        except ValueError as exc:
            raise ValueError(f"Cannot import {name!r}: {exc}").with_traceback(
                sys.exc_info()[2]
            ) from exc

        if attribute_name:
            return cast(_T, getattr(module, attribute_name))
        else:
            return cast(_T, module)
    except (ImportError, AttributeError):
        if default is None:
            raise
        return default


class EntrypointExtension(NamedTuple):
    name: str
    type: type


class RawEntrypointExtension(NamedTuple):
    name: str
    target: str


def load_extension_classes(namespace: str) -> Iterable[EntrypointExtension]:
    """Yield extension classes for setuptools entrypoint namespace.

    If an entrypoint is defined in `setup.py`:

    ```python
    entry_points={
        'faust.codecs': [
            'msgpack = faust_msgpack:msgpack',
        ],
    ```

    Iterating over the 'faust.codecs' namespace will yield
    the actual attributes specified in the path (`faust_msgpack:msgpack`):

    ```python
    from faust_msgpack import msgpack
    attrs = list(load_extension_classes('faust.codecs'))
    assert msgpack in attrs
    ```
    """
    for name, cls_name in load_extension_class_names(namespace):
        try:
            cls: type = symbol_by_name(cls_name)
        except (ImportError, SyntaxError) as exc:
            warnings.warn(
                f"Cannot load {namespace} extension {cls_name!r}: {exc!r}",
                stacklevel=1,
            )
        else:
            yield EntrypointExtension(name, cls)


def load_extension_class_names(
    namespace: str,
) -> Iterable[RawEntrypointExtension]:
    """Get setuptools entrypoint extension class names.

    If the entrypoint is defined in `setup.py` as:

    ```python
    entry_points={
        'faust.codecs': [
            'msgpack = faust_msgpack:msgpack',
        ],
    ```

    Iterating over the 'faust.codecs' namespace will yield the name:

    ```sh
    >>> list(load_extension_class_names('faust.codecs'))
    [('msgpack', 'faust_msgpack:msgpack')]
    ```
    """
    eps = entry_points()
    # Python 3.10+
    if hasattr(eps, "select"):
        for ep in eps.select(group=namespace):
            yield RawEntrypointExtension(
                ep.name, ":".join([ep.module, ep.attr])
            )
    # Python <3.10
    else:
        for ep in eps.get(namespace, []):
            yield RawEntrypointExtension(
                ep.name, ":".join([ep.module, ep.attr])
            )


@contextmanager
def cwd_in_path() -> Generator:
    """Context adding the current working directory to sys.path."""
    cwd = os.getcwd()
    if cwd in sys.path:
        yield
    else:
        sys.path.insert(0, cwd)
        try:
            yield cwd
        finally:
            with suppress(ValueError):
                sys.path.remove(cwd)


def import_from_cwd(
    module: str,
    *,
    imp: Optional[Callable] = None,
    package: Optional[str] = None,
) -> ModuleType:
    """Import module, temporarily including modules in the current directory.

    Modules located in the current directory has
    precedence over modules located in `sys.path`.
    """
    if imp is None:
        imp = importlib.import_module
    with cwd_in_path():
        return imp(module, package=package)


def smart_import(path: str, imp: Any = None) -> Any:
    """Import module if module, otherwise same as `symbol_by_name`."""
    imp = importlib.import_module if imp is None else imp
    if ":" in path:
        # Path includes attribute so can just jump
        # here (e.g., ``os.path:abspath``).
        return symbol_by_name(path, imp=imp)

    # Not sure if path is just a module name or if it includes an
    # attribute name (e.g., ``os.path``, vs, ``os.path.abspath``).
    try:
        return imp(path)
    except ImportError:
        # Not a module name, so try module + attribute.
        return symbol_by_name(path, imp=imp)
