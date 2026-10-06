"""
ui.layer_groups - puts layers SIGate adds into groups of the QGIS layer
tree instead of leaving them flat at the project's top level.

Why a scope instead of passing a group to every call: layers reach the
project through QGIS's own addRasterLayer/addVectorLayer/
addPointCloudLayer signals, which have no group parameter and which QGIS
handles synchronously. So a caller opens a `group_scope(...)` naming the
group path, emits as usual through `emit_in_scope`, and every layer that
appeared in the project during that emit is moved into the group. The
scope is a plain stack, so nested scopes concatenate:

    with group_scope("IGN (France)"):          # source
        with group_scope("IGNF_..._metadata", "MNT"):  # layer, product
            emit_in_scope(lambda: self.addRasterLayer.emit(...))

lands the layer in IGN (France) > IGNF_..._metadata > MNT.

This module is deliberately the single place that turns "what is being
added" into "where it goes in the layer tree": the current rule is the
default one (source > layer > product, mirroring the on-disk download
folders), and a future user-configurable organisation module would
replace the path decision here, not the call sites.

An empty scope leaves layers exactly where QGIS put them (the old
behaviour), so any caller that opens no scope is unaffected.
"""

from contextlib import contextmanager
from typing import Callable, Iterable, List, Optional

from qgis.core import QgsLayerTree, QgsProject

_scope: List[str] = []


@contextmanager
def group_scope(*names: Optional[str]):
    """Appends `names` (blank ones skipped) to the current group path for
    the duration of the block."""
    added = [n for n in names if n and str(n).strip()]
    _scope.extend(added)
    try:
        yield
    finally:
        if added:
            del _scope[len(_scope) - len(added) :]


def current_group_path() -> List[str]:
    return list(_scope)


def _child_group(parent, name: str):
    for child in parent.children():
        if QgsLayerTree.isGroup(child) and child.name() == name:
            return child
    return None


def ensure_group(root, path: Iterable[str]):
    """The layer-tree group at `path` below `root`, creating any missing
    level (new groups go on top, where QGIS itself puts new layers).
    Existing groups are reused, so repeated downloads fill one group
    rather than creating duplicates."""
    group = root
    for name in path:
        group = _child_group(group, name) or group.insertGroup(0, name)
    return group


def move_layers_into_group(layer_ids: Iterable[str], path: List[str], project) -> None:
    root = project.layerTreeRoot()
    group = ensure_group(root, path)
    for layer_id in layer_ids:
        node = root.findLayer(layer_id)
        if node is None or node.parent() is None:
            continue
        parent = node.parent()
        group.addChildNode(node.clone())
        parent.removeChildNode(node)


def emit_in_scope(emit: Callable[[], None], project=None) -> None:
    """Runs `emit` (which asks QGIS to add layer(s)) and moves whatever
    layers it added into the group named by the current scope. With no
    scope open, just runs `emit`."""
    path = current_group_path()
    if not path:
        emit()
        return
    project = project or QgsProject.instance()
    before = set(project.mapLayers())
    emit()
    new_ids = [layer_id for layer_id in project.mapLayers() if layer_id not in before]
    if new_ids:
        move_layers_into_group(new_ids, path, project)
