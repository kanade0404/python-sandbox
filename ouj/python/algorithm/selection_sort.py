from typing import List


# 選択ソート
def selection_sort(nums: List[int]) -> List[int]:
    for i in range(len(nums) - 1):
        min_pos = i
        for j in range(i + 1, len(nums)):
            if nums[min_pos] > nums[j]:
                min_pos = j
        if nums[i] > nums[min_pos]:
            nums[i], nums[min_pos] = nums[min_pos], nums[i]
    return nums
