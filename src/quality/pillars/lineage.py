"""
Pillar: Lineage

TABLE-level lineage, derived from the `foreign_keys` declared in catalog/inventory.yaml.

A foreign key is used as a proxy for dependency: if order_items references orders, a
problem in orders reaches every analysis that joins order_items to it. The graph answers
the incident question "if this table is broken, who else is affected?" (downstream impact)
and the root-cause question "where could this problem have come from?" (upstream).
"""

from collections import deque
from typing import Any, Literal

Direction = Literal["upstream", "downstream"]
Graph = dict[str, dict[Direction, list[str]]]


def build_lineage_graph(catalog: dict[str, Any]) -> Graph:
    """Constructs a bidirectional table lineage dependency graph from the metadata catalog.

    Args:
        catalog: The metadata inventory catalog dictionary.

    Returns:
        A dictionary mapping each table to its direct 'upstream' and 'downstream' tables
        (each edge listed once, even when several foreign keys link the same pair).
    """
    graph: Graph = {table: {"upstream": [], "downstream": []} for table in catalog["tables"]}

    for table, meta in catalog["tables"].items():
        for fk in meta.get("foreign_keys") or []:
            ref_table = fk["references"].split(".", 1)[0]
            if ref_table not in graph or ref_table == table:
                continue
            if ref_table not in graph[table]["upstream"]:
                graph[table]["upstream"].append(ref_table)
                graph[ref_table]["downstream"].append(table)

    return graph


def _traverse(table_name: str, graph: Graph, direction: Direction) -> list[str]:
    """Breadth-first search collecting every table transitively reachable in one direction."""
    visited: set[str] = set()
    queue = deque(graph.get(table_name, {}).get(direction, []))

    while queue:
        current = queue.popleft()
        if current in visited or current == table_name:
            continue
        visited.add(current)
        queue.extend(graph.get(current, {}).get(direction, []))

    return sorted(visited)


def downstream_impact(table_name: str, graph: Graph) -> list[str]:
    """Every table transitively affected if `table_name` has a problem."""
    return _traverse(table_name, graph, "downstream")


def upstream_dependencies(table_name: str, graph: Graph) -> list[str]:
    """Every parent table transitively feeding into `table_name` (root-cause candidates)."""
    return _traverse(table_name, graph, "upstream")
