from ouj.python.algorithm.binary_search import binary_search


def test_binary_search():
    assert (
        binary_search([1, 6, 10, 12, 21, 22, 25, 29, 38, 43, 44, 63, 71, 85, 94, 96], 1)
        is True
    )
    assert (
        binary_search([1, 6, 10, 12, 21, 22, 25, 29, 38, 43, 44, 63, 71, 85, 94, 96], 5)
        is False
    )
    assert (
        binary_search(
            [1, 6, 10, 12, 21, 22, 25, 29, 38, 43, 44, 63, 71, 85, 94, 96], 96
        )
        is True
    )
    assert (
        binary_search(
            [1, 6, 10, 12, 21, 22, 25, 29, 38, 43, 44, 63, 71, 85, 94, 96], 100
        )
        is False
    )
