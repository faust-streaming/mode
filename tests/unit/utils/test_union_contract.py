"""Union-introspection compatibility contract."""

import sys
import typing
from typing import Any, Optional, Union, get_args, get_origin

import pytest

from mode.utils.objects import (
    _remove_optional,
    is_optional,
    is_union,
    remove_optional,
)


def _eval_pep604(expression: str) -> Any:
    return eval(expression)


@pytest.mark.parametrize(
    "annotation",
    [Union[str, int], Optional[str], Union[str, list, dict, None]],
)
def test_typing_union_is_recognized(annotation):
    assert is_union(annotation)


@pytest.mark.skipif(
    sys.version_info < (3, 10), reason="PEP 604 requires Python 3.10"
)
def test_pep604_union_is_recognized():
    annotations = [
        _eval_pep604("str | int"),
        _eval_pep604("str | None"),
        _eval_pep604("str | list | dict | None"),
        _eval_pep604("list[str] | dict[str, object] | None"),
    ]
    assert all(is_union(annotation) for annotation in annotations)


@pytest.mark.parametrize(
    "annotation,expected",
    [
        (Optional[str], True),
        (Union[str, int, None], True),
        (Union[str, int], False),
        (str, False),
    ],
)
def test_typing_optional_detection(annotation, expected):
    assert is_optional(annotation) is expected


@pytest.mark.skipif(
    sys.version_info < (3, 10), reason="PEP 604 requires Python 3.10"
)
def test_pep604_optional_detection():
    cases = [
        (_eval_pep604("str | None"), True),
        (_eval_pep604("str | int | None"), True),
        (_eval_pep604("str | int"), False),
        (_eval_pep604("list[str] | dict[str, object] | None"), True),
    ]
    for annotation, expected in cases:
        assert is_optional(annotation) is expected


def test_remove_optional_preserves_multi_type_union():
    result = remove_optional(Union[str, list, dict, None])
    assert is_union(result)
    assert set(get_args(result)) == {str, list, dict}


@pytest.mark.skipif(
    sys.version_info < (3, 10), reason="PEP 604 requires Python 3.10"
)
def test_remove_optional_normalizes_pep604_multi_type_union():
    result = remove_optional(_eval_pep604("str | list | dict | None"))
    assert get_origin(result) is typing.Union
    assert set(get_args(result)) == {str, list, dict}


@pytest.mark.skipif(
    sys.version_info < (3, 10), reason="PEP 604 requires Python 3.10"
)
def test_remove_optional_with_origin_retains_all_non_none_members():
    args, origin = _remove_optional(
        _eval_pep604("str | list | dict | None"), find_origin=True
    )
    assert origin is typing.Union
    assert set(args) == {str, list, dict}
