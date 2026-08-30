from typing import List


def exhaustive_search(target: List[int], query: int) -> bool:
    exists = False
    for n in target:
        if n == query:
            exists = True
    return exists
