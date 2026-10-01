"""Clustering con restricciones (una oferta por supermercado por identidad).

Se procesan las aristas elegibles de mayor a menor peso (GTIN primero) con
union-find, como un single-linkage restringido. Una unión sólo ocurre si:

1. los dos grupos no comparten contexto de precio (supermercado + tienda):
   "≤ 1 oferta por contexto de cadena" (``slot_of``);
2. el grupo resultante no supera ``max_cluster_size``;
3. con ``linkage: complete`` (por defecto), *todas* las parejas cruzadas son
   elegibles para auto-match: la transitividad no se presume (política v1,
   regla 7). Con ``single`` basta la arista que une.

Así, un componente conexo que violaría las restricciones queda partido por sus
aristas más fuertes en lugar de fusionarse.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Callable, Iterable, Mapping, Sequence

PairStatus = Callable[[str, str], str]


@dataclass(frozen=True, slots=True)
class Edge:
    left_id: str
    right_id: str
    weight: float
    is_gtin: bool


@dataclass(frozen=True, slots=True)
class Cluster:
    cluster_id: str
    members: tuple[str, ...]
    contexts: tuple[str, ...]
    gtin_only: bool


@dataclass
class ClusteringResult:
    clusters: list[Cluster]
    rejected: dict[str, int]


def cluster_id(members: Iterable[str]) -> str:
    digest = hashlib.sha256("|".join(sorted(members)).encode("utf-8")).hexdigest()
    return f"mcl_{digest[:20]}"


def constrained_clustering(
    edges: Sequence[Edge],
    slot_of: Mapping[str, str],
    pair_status: PairStatus,
    *,
    linkage: str = "complete",
    max_cluster_size: int = 8,
) -> ClusteringResult:
    if linkage not in {"complete", "single"}:
        raise ValueError("clustering_linkage_invalid")
    parent: dict[str, str] = {}
    members: dict[str, list[str]] = {}
    retailers: dict[str, set[str]] = {}
    gtin_only: dict[str, bool] = {}
    rejected = {"retailer_collision": 0, "cluster_size": 0, "complete_linkage": 0}

    def find(item: str) -> str:
        parent.setdefault(item, item)
        if item not in members:
            members[item] = [item]
            retailers[item] = {slot_of[item]}
            gtin_only[item] = True
        root = item
        while parent[root] != root:
            root = parent[root]
        while parent[item] != root:
            parent[item], item = root, parent[item]
        return root

    ordered = sorted(edges, key=lambda edge: (not edge.is_gtin, -edge.weight, edge.left_id, edge.right_id))
    for edge in ordered:
        left_root, right_root = find(edge.left_id), find(edge.right_id)
        if left_root == right_root:
            continue
        if retailers[left_root] & retailers[right_root]:
            rejected["retailer_collision"] += 1
            continue
        if len(members[left_root]) + len(members[right_root]) > max_cluster_size:
            rejected["cluster_size"] += 1
            continue
        if linkage == "complete":
            ok = all(
                pair_status(a, b) in {"auto", "gtin"}
                for a in members[left_root]
                for b in members[right_root]
                if (a, b) != (edge.left_id, edge.right_id) and (b, a) != (edge.left_id, edge.right_id)
            )
            if not ok:
                rejected["complete_linkage"] += 1
                continue
        parent[right_root] = left_root
        members[left_root].extend(members.pop(right_root))
        retailers[left_root] |= retailers.pop(right_root)
        gtin_only[left_root] = gtin_only[left_root] and gtin_only.pop(right_root) and edge.is_gtin

    clusters = []
    for root, items in members.items():
        if len(items) < 2:
            continue
        ordered_items = tuple(sorted(items))
        clusters.append(
            Cluster(
                cluster_id=cluster_id(ordered_items),
                members=ordered_items,
                contexts=tuple(sorted(slot_of[item] for item in ordered_items)),
                gtin_only=gtin_only[root],
            )
        )
    clusters.sort(key=lambda cluster: cluster.cluster_id)
    return ClusteringResult(clusters=clusters, rejected=rejected)
