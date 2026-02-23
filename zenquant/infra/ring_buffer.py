from __future__ import annotations

from typing import Generic, TypeVar


T = TypeVar("T")


class RingBuffer(Generic[T]):
    def __init__(self, capacity: int) -> None:
        if capacity <= 0:
            raise ValueError("capacity must be > 0")
        self._capacity = capacity
        self._items: list[T | None] = [None] * capacity
        self._head = 0
        self._tail = 0
        self._size = 0

    @property
    def capacity(self) -> int:
        return self._capacity

    def __len__(self) -> int:
        return self._size

    def push(self, item: T) -> bool:
        if self._size == self._capacity:
            return False
        self._items[self._tail] = item
        self._tail = (self._tail + 1) % self._capacity
        self._size += 1
        return True

    def pop(self) -> T | None:
        if self._size == 0:
            return None
        item = self._items[self._head]
        self._items[self._head] = None
        self._head = (self._head + 1) % self._capacity
        self._size -= 1
        return item

    def clear(self) -> None:
        self._items = [None] * self._capacity
        self._head = 0
        self._tail = 0
        self._size = 0

