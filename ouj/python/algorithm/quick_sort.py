from typing import List


def quick_sort(target: List[int]) -> List[int]:
    if len(target) <= 1:
        return target
    pivot = target[0]
    center = [pivot]
    smaller = []
    larger = []
    for i in range(1, len(target)):
        if target[i] <= pivot:
            smaller.append(target[i])
        else:
            larger.append(target[i])
    smaller = quick_sort(smaller)
    larger = quick_sort(larger)
    return smaller + center + larger
