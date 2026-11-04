"""Tag tree model + persistence.

Tags are organized as a tree (a tag can contain child tags) but identified
globally by name — a tag's path in the tree is organization, not identity.
Projects reference tags by name only; the tree decides how tags are grouped
in the sidebar and the library drill-down.

Persistence is a single JSON file at `<config>/tags.json`, mirroring the
atomic-write pattern used by `settings.py`. Write = drop a temp file and
rename; reads are best-effort, returning an empty tree on corruption so a
malformed file can't wedge startup.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Iterable

from . import fsutil, paths

SCHEMA_VERSION = 2


@dataclass
class TagNode:
    """A single tag in the tree. `children` is a list (order matters).

    ``color`` is a hex string (e.g. ``"#FDCF00"``); an empty string means
    "use the default tint" — the sidebar and chip styling fall back to
    STEEL BLUE when color is unset.
    """

    name: str
    color: str = ""
    children: list["TagNode"] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "color": self.color,
            "children": [c.to_dict() for c in self.children],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TagNode":
        raw_name = str(data.get("name", "")).strip()
        if not raw_name:
            # Callers filter these out; emitting an empty-name node here
            # avoids raising mid-parse.
            return cls(name="", children=[])
        color = str(data.get("color", "") or "").strip()
        raw_kids = data.get("children") or []
        if not isinstance(raw_kids, list):
            raw_kids = []
        kids = [TagNode.from_dict(k) for k in raw_kids if isinstance(k, dict)]
        kids = [k for k in kids if k.name]
        return cls(name=raw_name, color=color, children=kids)


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def load() -> list[TagNode]:
    path = paths.config_root() / "tags.json"
    if not path.exists():
        return []
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return []
    if not isinstance(data, dict):
        return []
    raw_roots = data.get("roots") or []
    if not isinstance(raw_roots, list):
        return []
    roots = [TagNode.from_dict(r) for r in raw_roots if isinstance(r, dict)]
    roots = [r for r in roots if r.name]
    # Deduplicate by name across the whole tree — older files or manual
    # edits might have introduced a clash. First occurrence wins.
    seen: set[str] = set()
    return _dedupe(roots, seen)


def _dedupe(nodes: list[TagNode], seen: set[str]) -> list[TagNode]:
    out: list[TagNode] = []
    for n in nodes:
        if n.name in seen:
            continue
        seen.add(n.name)
        n.children = _dedupe(n.children, seen)
        out.append(n)
    return out


def save(roots: list[TagNode]) -> None:
    paths.ensure_dirs()
    path = paths.config_root() / "tags.json"
    payload = {
        "version": SCHEMA_VERSION,
        "roots": [r.to_dict() for r in roots],
    }
    fsutil.write_text_atomic(path, json.dumps(payload, indent=2))


# ---------------------------------------------------------------------------
# Traversal
# ---------------------------------------------------------------------------


def walk(roots: list[TagNode]):
    """Depth-first iterator yielding every `TagNode` in the tree."""
    stack = list(reversed(roots))
    while stack:
        node = stack.pop()
        yield node
        stack.extend(reversed(node.children))


def all_tag_names(roots: list[TagNode]) -> set[str]:
    return {n.name for n in walk(roots)}


def find(roots: list[TagNode], name: str) -> TagNode | None:
    for n in walk(roots):
        if n.name == name:
            return n
    return None


def find_parent(
    roots: list[TagNode], name: str
) -> tuple[list[TagNode] | None, TagNode | None]:
    """Return `(siblings, parent_node)` for `name`.

    `siblings` is the list the node currently lives in (the tree's
    `roots` list when the node is a root), `parent_node` is None when
    the node is a root. Returns `(None, None)` if `name` isn't found.
    """
    for sibling_list, parent in _iter_siblings(roots, None):
        for node in sibling_list:
            if node.name == name:
                return sibling_list, parent
    return None, None


def _iter_siblings(nodes: list[TagNode], parent: TagNode | None):
    yield nodes, parent
    for n in nodes:
        yield from _iter_siblings(n.children, n)


def children_of(
    roots: list[TagNode], parent_name: str | None
) -> list[TagNode]:
    """Children of `parent_name`, or the root list when `parent_name` is None."""
    if parent_name is None:
        return list(roots)
    node = find(roots, parent_name)
    if node is None:
        return []
    return list(node.children)


def descendants_of(roots: list[TagNode], name: str) -> set[str]:
    """Names of every descendant of `name` (not including `name` itself)."""
    node = find(roots, name)
    if node is None:
        return set()
    return {n.name for n in walk(node.children)}


def path_of(roots: list[TagNode], name: str) -> list[str] | None:
    """Root-to-node path (inclusive). Returns None when `name` isn't found."""

    def _dfs(
        nodes: list[TagNode], trail: list[str]
    ) -> list[str] | None:
        for n in nodes:
            new_trail = trail + [n.name]
            if n.name == name:
                return new_trail
            deeper = _dfs(n.children, new_trail)
            if deeper is not None:
                return deeper
        return None

    return _dfs(roots, [])


# ---------------------------------------------------------------------------
# Mutation (in-place on `roots`)
# ---------------------------------------------------------------------------


class TagError(ValueError):
    """Raised when a tag-tree mutation would violate uniqueness or cycles."""


def add_tag(
    roots: list[TagNode], name: str, parent_name: str | None = None
) -> TagNode:
    """Add a new tag under `parent_name` (or at root if None).

    Raises `TagError` if the name is blank or already exists anywhere.
    """
    clean = (name or "").strip()
    if not clean:
        raise TagError("Tag name cannot be empty")
    if find(roots, clean) is not None:
        raise TagError(f"Tag already exists: {clean}")

    node = TagNode(name=clean, children=[])
    if parent_name is None:
        roots.append(node)
        return node
    parent = find(roots, parent_name)
    if parent is None:
        raise TagError(f"Parent tag not found: {parent_name}")
    parent.children.append(node)
    return node


def remove_tag(roots: list[TagNode], name: str) -> set[str]:
    """Remove `name` and its subtree. Returns the set of removed names.

    The caller is responsible for scrubbing removed names from any
    projects that reference them.
    """
    siblings, _parent = find_parent(roots, name)
    if siblings is None:
        return set()
    idx = next((i for i, n in enumerate(siblings) if n.name == name), None)
    if idx is None:
        return set()
    node = siblings.pop(idx)
    return {n.name for n in walk([node])}


def move_tag(
    roots: list[TagNode], name: str, new_parent_name: str | None
) -> None:
    """Move `name` to become a child of `new_parent_name` (or to the root).

    Raises `TagError` when the move would create a cycle (new parent is
    the tag itself or one of its descendants) or when either node is
    missing.
    """
    if name == new_parent_name:
        raise TagError("A tag cannot be its own parent")
    siblings, _parent = find_parent(roots, name)
    if siblings is None:
        raise TagError(f"Tag not found: {name}")
    idx = next((i for i, n in enumerate(siblings) if n.name == name), None)
    if idx is None:
        raise TagError(f"Tag not found: {name}")

    # Cycle guard — target parent must not be in the moved subtree.
    node = siblings[idx]
    if new_parent_name is not None:
        if new_parent_name == name:
            raise TagError("A tag cannot be its own parent")
        if new_parent_name in {n.name for n in walk([node])}:
            raise TagError(
                f"Cannot move '{name}' under its own descendant '{new_parent_name}'"
            )

    if new_parent_name is None:
        target_list: list[TagNode] = roots
    else:
        parent = find(roots, new_parent_name)
        if parent is None:
            raise TagError(f"Parent tag not found: {new_parent_name}")
        target_list = parent.children

    # Detach and reattach. If the node is already in the target list,
    # leave it where it is — Qt's InternalMove DnD occasionally re-emits
    # a drop onto the existing parent.
    if any(n is node for n in target_list):
        return
    siblings.pop(idx)
    target_list.append(node)


def set_tag_color(roots: list[TagNode], name: str, color: str) -> None:
    """Update a tag's color (empty string clears it)."""
    node = find(roots, name)
    if node is None:
        raise TagError(f"Tag not found: {name}")
    node.color = (color or "").strip()


def rename_tag(roots: list[TagNode], old: str, new: str) -> None:
    """Rename a tag in place. Raises on blank, duplicate, or missing.

    Doesn't touch alpacka.yml files; the caller walks those to keep
    the project ``tags`` lists in sync (see
    ``LibraryView.apply_tag_rename``).
    """
    cleaned = (new or "").strip()
    if not cleaned:
        raise TagError("Tag name cannot be empty")
    if cleaned == old:
        return
    if find(roots, cleaned) is not None:
        raise TagError(f"Tag already exists: {cleaned}")
    node = find(roots, old)
    if node is None:
        raise TagError(f"Tag not found: {old}")
    node.name = cleaned


def ensure_tags_exist(
    roots: list[TagNode], names: Iterable[str]
) -> bool:
    """Auto-add any `names` missing from the tree as new root nodes.

    Returns True when the tree was modified. Idempotent — safe to call
    on every app launch / library refresh.
    """
    existing = all_tag_names(roots)
    dirty = False
    for raw in names:
        clean = (raw or "").strip()
        if not clean or clean in existing:
            continue
        roots.append(TagNode(name=clean, children=[]))
        existing.add(clean)
        dirty = True
    return dirty
