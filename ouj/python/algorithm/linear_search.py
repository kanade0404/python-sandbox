from typing import List


def linear_search(target: List[int], query: int) -> bool:
    for i in target:
        if i == query:
            return True
    return False
