from ouj.python.algorithm.selection_sort import selection_sort


def test_selection_sort():
    assert selection_sort([3, 5, 2, 1, 4]) == [1, 2, 3, 4, 5]
