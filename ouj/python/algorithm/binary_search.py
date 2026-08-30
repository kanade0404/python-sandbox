from typing import List


def binary_search(target: List[int], query: int) -> bool:
    start = 0
    end = len(target) - 1
    while start <= end:
        center = int((start + end) / 2)
        print(query, target[center])
        if query == target[center]:
            return True
        elif query < target[center]:
            end = center - 1
        else:
            start = center + 1
    return False


n = int(input("n?"))
query = int(input("query?"))
binary_search([i for i in range(n)], query)
