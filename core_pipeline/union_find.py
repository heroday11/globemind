"""Union-Find (Disjoint Set Union) with path compression."""

from __future__ import annotations

from typing import Dict, Iterable


class UnionFind:
    def __init__(self, nodes: Iterable[str]) -> None:
        self._p: Dict[str, str] = {n: n for n in nodes}

    def find(self, x: str) -> str:
        if self._p[x] != x:
            self._p[x] = self.find(self._p[x])
        return self._p[x]

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self._p[rb] = ra
