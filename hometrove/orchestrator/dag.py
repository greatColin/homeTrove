"""DAG-based plugin scheduler.

M0 has exactly one plugin (``basic.info``), so the graph degenerates to a
single node. The implementation nevertheless runs the *full* prepare /
running / done flow described in README §8.3.1, so M1's wider graphs slot
in without rewrites.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from graphlib import TopologicalSorter


@dataclass
class Node:
    plugin_id: str
    depends_on: list[str] = field(default_factory=list)


def build_graph(nodes: Iterable[Node]) -> tuple[TopologicalSorter, dict[str, Node]]:
    by_id: dict[str, Node] = {}
    for n in nodes:
        if n.plugin_id in by_id:
            raise ValueError(f"duplicate plugin id {n.plugin_id!r}")
        by_id[n.plugin_id] = n

    ts = TopologicalSorter()
    for n in by_id.values():
        ts.add(n.plugin_id, *n.depends_on)
    for n in by_id.values():
        for d in n.depends_on:
            if d not in by_id:
                raise ValueError(
                    f"plugin {n.plugin_id!r} depends on unknown plugin {d!r}"
                )
    return ts, by_id
