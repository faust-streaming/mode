"""Union-introspection compatibility contract.

The helpers in :mod:`mode.utils.objects` are consumed by downstream projects
such as Faust. These tests make changes in union recognition explicit so a
Mode upgrade cannot silently change which annotations downstream compilers
receive.
"""

import sys
import typing
from typing import Optional, Union, get_args, get_origin

import pytest

from mode.utils.objects import _remove_optional, is_optional, is_union, remove_optional


PEP604_UNION_CASES = []
PEP604_OPTIONAL_CASES = []
if sys.version_info >= (3, 10):
    PEP604_UNION_CASES = [
        str | int,
        str | None,
        str | list | dict | None,
        list[str] | dict[str, object] | None,
    ]
    PEP604_OPTIONAL_CASES = [
        (str | None, True),
        (str | int | None, True),
        (str | int, False),
        (list[str] | dict[str, object] | None, True),
    ]


@pytest.mark.parametrize(
    "annotation",
    [
        Union[str, int],
        Optional[str],
        Union[str, list, dict, None],
    ],
)
def test_typing_union_is_recognized(annotation):
    assert is_union(annotation)


@pytest.mark.parametrize("annotation", PEP604_UNION_CASES)
def test_pep604_union_is_recognized(annotation):
    assert is_union(annotation)


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


@pytest.mark.parametrize("annotation,expected", PEP604_OPTIONAL_CASES)
def test_pep604_optional_detection(annotation, expected):
    assert is_optional(annotation) is expected


def test_remove_optional_preserves_multi_type_union():
    result = remove_optional(Union[str, list, dict, None])
    assert is_union(result)
    assert set(get_args(result)) == {str, list, dict}


@pytest.mark.skipif(sys.version_info < (3, 10), reason="PEP 604 requires Python 3.10")
def test_remove_optional_normalizes_pep604_multi_type_union():
    result = remove_optional(str | list | dict | None)
    assert get_origin(result) is typing.Union
    assert set(get_args(result)) == {str, list, dict}


@pytest.mark.skipif(sys.version_info < (3, 10), reason="PEP 604 requires Python 3.10")
def test_remove_optional_with_origin_retains_all_non_none_members():
    args, origin = _remove_optional(str | list | dict | None, find_origin=True)
    assert origin is typing.Union
    assert set(args) == {str, list, dict}
