import pytest

from shopapp.utils import chunked, paginate, safe_int


def test_paginate():
    assert paginate(list(range(10)), 2, 3) == [3, 4, 5]
    with pytest.raises(ValueError):
        paginate([], 0)


def test_chunked_and_safe_int():
    assert chunked([1, 2, 3], 2) == [[1, 2], [3]]
    assert safe_int("x", 7) == 7
