"""
Tests for sigate.ui.layer_groups against a real QgsProject (not the
global instance, so tests don't leak into each other).
"""


def _project_and_adder(qgis_app):
    from qgis.core import QgsProject, QgsVectorLayer

    project = QgsProject()

    def add(name="layer"):
        layer = QgsVectorLayer("Point?crs=EPSG:4326", name, "memory")
        project.addMapLayer(layer)  # lands at the top level, like QGIS's own add
        return layer

    return project, add


def _tree(node, depth=0):
    """Layer tree as nested (name, children) tuples, layers as plain names."""
    from qgis.core import QgsLayerTree

    out = []
    for child in node.children():
        if QgsLayerTree.isGroup(child):
            out.append((child.name(), _tree(child)))
        else:
            out.append(child.name())
    return out


def test_layers_added_in_a_scope_land_in_nested_groups(qgis_app):
    from sigate.ui.layer_groups import emit_in_scope, group_scope

    project, add = _project_and_adder(qgis_app)
    with group_scope("IGN (France)"):
        with group_scope("IGNF_LIDAR-HD_METADONNEE_metadata", "MNT"):
            emit_in_scope(lambda: add("tile_a"), project)
            emit_in_scope(lambda: add("tile_b"), project)
        with group_scope("IGNF_LIDAR-HD_METADONNEE_metadata", "NPL"):
            emit_in_scope(lambda: add("cloud_a"), project)

    tree = _tree(project.layerTreeRoot())
    assert len(tree) == 1 and tree[0][0] == "IGN (France)"
    (layer_group,) = tree[0][1]
    assert layer_group[0] == "IGNF_LIDAR-HD_METADONNEE_metadata"
    products = {name: sorted(layers) for name, layers in layer_group[1]}
    assert products == {"MNT": ["tile_a", "tile_b"], "NPL": ["cloud_a"]}
    # nothing left loose at the top level
    assert all(not isinstance(c, str) for c in _tree(project.layerTreeRoot()))


def test_existing_groups_are_reused_not_duplicated(qgis_app):
    from sigate.ui.layer_groups import emit_in_scope, group_scope

    project, add = _project_and_adder(qgis_app)
    for name in ("first", "second"):
        with group_scope("Source", "Layer"):
            emit_in_scope(lambda n=name: add(n), project)

    tree = _tree(project.layerTreeRoot())
    assert len(tree) == 1 and tree[0][0] == "Source"
    assert len(tree[0][1]) == 1 and tree[0][1][0][0] == "Layer"
    assert sorted(tree[0][1][0][1]) == ["first", "second"]


def test_no_scope_leaves_layers_at_the_top_level(qgis_app):
    from sigate.ui.layer_groups import emit_in_scope

    project, add = _project_and_adder(qgis_app)
    emit_in_scope(lambda: add("loose"), project)

    assert _tree(project.layerTreeRoot()) == ["loose"]


def test_blank_names_are_skipped_and_scope_unwinds(qgis_app):
    from sigate.ui.layer_groups import current_group_path, group_scope

    with group_scope("A", None, "  ", "B"):
        assert current_group_path() == ["A", "B"]
        with group_scope():
            assert current_group_path() == ["A", "B"]
        with group_scope("C"):
            assert current_group_path() == ["A", "B", "C"]
        assert current_group_path() == ["A", "B"]
    assert current_group_path() == []


def test_scope_unwinds_even_when_the_block_raises(qgis_app):
    import pytest

    from sigate.ui.layer_groups import current_group_path, group_scope

    with pytest.raises(RuntimeError):
        with group_scope("A"):
            raise RuntimeError("boom")
    assert current_group_path() == []


def test_default_path_uses_the_global_project_like_qgis_does(qgis_app):
    from qgis.core import QgsProject, QgsVectorLayer

    from sigate.ui.layer_groups import emit_in_scope, group_scope

    project = QgsProject.instance()
    project.clear()
    try:
        with group_scope("Source"):
            emit_in_scope(
                lambda: project.addMapLayer(
                    QgsVectorLayer("Point?crs=EPSG:4326", "x", "memory")
                )
            )
        assert _tree(project.layerTreeRoot()) == [("Source", ["x"])]
    finally:
        project.clear()
