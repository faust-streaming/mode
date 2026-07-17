"""Type classes for `mode.utils.graphs`."""

import abc
from collections.abc import Iterable, Mapping, MutableMapping, Sequence
from typing import IO, Any, Generic, Optional, TypeVar

__all__ = ["DependencyGraphT", "GraphFormatterT"]

_T = TypeVar("_T")


class GraphFormatterT(Generic[_T]):
    """Type class for graph formatters."""

    scheme: Mapping[str, Any]
    edge_scheme: Mapping[str, Any]
    node_scheme: Mapping[str, Any]
    term_scheme: Mapping[str, Any]
    graph_scheme: Mapping[str, Any]

    @abc.abstractmethod
    def __init__(
        self,
        root: Any = None,
        type: Optional[str] = None,
        id: Optional[str] = None,
        indent: int = 0,
        inw: str = " " * 4,
        **scheme: Any,
    ) -> None: ...

    @abc.abstractmethod
    def attr(self, name: str, value: Any) -> str: ...

    @abc.abstractmethod
    def attrs(
        self, d: Optional[Mapping] = None, scheme: Optional[Mapping] = None
    ) -> str: ...

    @abc.abstractmethod
    def head(self, **attrs: Any) -> str: ...

    @abc.abstractmethod
    def tail(self) -> str: ...

    @abc.abstractmethod
    def label(self, obj: _T) -> str: ...

    @abc.abstractmethod
    def node(self, obj: _T, **attrs: Any) -> str: ...

    @abc.abstractmethod
    def terminal_node(self, obj: _T, **attrs: Any) -> str: ...

    @abc.abstractmethod
    def edge(self, a: _T, b: _T, **attrs: Any) -> str: ...

    @abc.abstractmethod
    def FMT(self, fmt: str, *args: Any, **kwargs: Any) -> str: ...

    @abc.abstractmethod
    def draw_edge(
        self,
        a: _T,
        b: _T,
        scheme: Optional[Mapping] = None,
        attrs: Optional[Mapping] = None,
    ) -> str: ...

    @abc.abstractmethod
    def draw_node(
        self,
        obj: _T,
        scheme: Optional[Mapping] = None,
        attrs: Optional[Mapping] = None,
    ) -> str: ...


class DependencyGraphT(Generic[_T], Mapping[_T, _T]):
    """Type class for dependency graphs."""

    adjacent: MutableMapping[_T, _T]

    @abc.abstractmethod
    def __init__(
        self,
        it: Optional[Iterable[_T]] = None,
        formatter: Optional[GraphFormatterT[_T]] = None,
    ) -> None: ...

    @abc.abstractmethod
    def add_arc(self, obj: _T) -> None: ...

    @abc.abstractmethod
    def add_edge(self, A: _T, B: _T) -> None: ...

    @abc.abstractmethod
    def connect(self, graph: "DependencyGraphT") -> None: ...

    @abc.abstractmethod
    def topsort(self) -> Sequence: ...

    @abc.abstractmethod
    def valency_of(self, obj: _T) -> int: ...

    @abc.abstractmethod
    def update(self, it: Iterable) -> None: ...

    @abc.abstractmethod
    def edges(self) -> Iterable: ...

    @abc.abstractmethod
    def to_dot(
        self, fh: IO, *, formatter: Optional[GraphFormatterT[_T]] = None
    ) -> None: ...
