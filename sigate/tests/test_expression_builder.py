"""
Tests for sigate.ui.expression_builder.
"""


def test_build_memory_layer_from_rows_creates_valid_layer_with_correct_data(qgis_app):
    from sigate.ui.expression_builder import build_memory_layer_from_rows

    rows = [
        {"name": "LHD_D001", "id_chantier": "137"},
        {"name": "LHD_D002", "id_chantier": "137"},
        {"name": "LHD_D003", "id_chantier": "140"},
    ]
    layer = build_memory_layer_from_rows(["name", "id_chantier"], rows)

    assert layer.isValid()
    assert [f.name() for f in layer.fields()] == ["name", "id_chantier"]
    assert layer.featureCount() == 3

    field_index = layer.fields().lookupField("id_chantier")
    assert layer.uniqueValues(field_index) == {"137", "140"}


def test_build_memory_layer_from_rows_handles_missing_keys_gracefully(qgis_app):
    from sigate.ui.expression_builder import build_memory_layer_from_rows

    rows = [{"name": "A"}, {"name": "B", "extra": "ignored downstream"}]
    layer = build_memory_layer_from_rows(["name", "id_chantier"], rows)

    assert layer.isValid()
    assert layer.featureCount() == 2
    field_index = layer.fields().lookupField("id_chantier")
    # missing key -> empty string, not a crash
    assert layer.uniqueValues(field_index) == {""}


def test_build_memory_layer_from_rows_handles_field_names_with_special_characters(
    qgis_app,
):
    """Regression test for a real reported bug ("select a field, click
    Load some/Load all, nothing happens") - fields are now added via the
    provider API rather than encoded into the memory provider's own
    connection-string syntax, which has no escaping for '&'/'=' - both
    legal in a real field name but also the syntax's own delimiters. A
    field name using either used to silently corrupt the resulting
    layer's schema; this confirms it no longer does."""
    from sigate.ui.expression_builder import build_memory_layer_from_rows

    fieldnames = ["name&extra", "a=b"]
    rows = [{"name&extra": "X", "a=b": "1"}, {"name&extra": "Y", "a=b": "1"}]
    layer = build_memory_layer_from_rows(fieldnames, rows)

    assert layer.isValid()
    assert [f.name() for f in layer.fields()] == fieldnames
    field_index = layer.fields().lookupField("a=b")
    assert field_index >= 0
    assert layer.uniqueValues(field_index) == {"1"}


def test_default_spatial_functions_use_no_st_prefix():
    """Regression test for the actual root cause of a real reported
    failure ("ST_Within($geom,@map_extent) error 400 bad request"):
    neither GeoServer's CQL nor QGIS's own native expression engine uses
    SQLite/SpatiaLite/PostGIS-style "ST_"-prefixed function names - this
    project's own autocomplete list was suggesting syntax that couldn't
    work in either target language."""
    from sigate.ui.expression_builder import DEFAULT_SPATIAL_FUNCTIONS

    assert not any(fn.upper().startswith("ST_") for fn in DEFAULT_SPATIAL_FUNCTIONS)
    assert "WITHIN" in DEFAULT_SPATIAL_FUNCTIONS
    assert "INTERSECTS" in DEFAULT_SPATIAL_FUNCTIONS

    """Regression test for a real reported failure ("ST_Within($geom,
    @map_extent) error 400 bad request"): @map_extent must substitute
    unquoted - CQL's spatial predicate functions take a bare geometry
    literal (WITHIN(geom, POLYGON(...))), not a quoted string
    (WITHIN(geom, 'POLYGON(...)') is a real, confirmed 400 - wrong type
    entirely, not just wrong syntax)."""
    from sigate.ui.expression_builder import substitute_tokens

    result = substitute_tokens(
        "WITHIN($geom, @map_extent)", "the_geom", "POLYGON((0 0,1 0,1 1,0 1,0 0))"
    )
    assert result == "WITHIN(the_geom, POLYGON((0 0,1 0,1 1,0 1,0 0)))"


def test_substitute_tokens_leaves_extent_token_when_no_extent_available():
    from sigate.ui.expression_builder import substitute_tokens

    result = substitute_tokens("WITHIN($geom, @map_extent)", "the_geom", None)
    assert result == "WITHIN(the_geom, @map_extent)"


def test_substitute_tokens_no_op_when_no_tokens_present():
    from sigate.ui.expression_builder import substitute_tokens

    result = substitute_tokens("name = 'D003'", "the_geom", "POLYGON(...)")
    assert result == "name = 'D003'"


def test_substitute_tokens_normalizes_st_prefixed_predicate_from_the_actual_reported_bug():
    """Regression test for the actual reported failure ("Query failed:
    HTTP Error 400: Bad Request" with an active filter of
    "st_intersect(geom,Polygon (...))") - a second real occurrence of
    the same mistake class the ST_Within fix above already covers,
    just with a different (and slightly misspelled) PostGIS-style
    name. substitute_tokens must normalize this automatically, not
    just the two token substitutions."""
    from sigate.ui.expression_builder import substitute_tokens

    result = substitute_tokens(
        "st_intersect($geom, @map_extent)",
        "geom",
        "Polygon ((6.6 45.9, 6.7 45.9, 6.7 46.0, 6.6 46.0, 6.6 45.9))",
    )
    assert result == (
        "INTERSECTS(geom, Polygon ((6.6 45.9, 6.7 45.9, 6.7 46.0, 6.6 46.0, 6.6 45.9)))"
    )


def test_normalize_spatial_function_names_covers_every_confirmed_predicate():
    from sigate.ui.expression_builder import normalize_spatial_function_names

    cases = {
        "st_within(a, b)": "WITHIN(a, b)",
        "ST_Intersects(a, b)": "INTERSECTS(a, b)",
        "st_intersect(a, b)": "INTERSECTS(a, b)",
        "St_Contains(a, b)": "CONTAINS(a, b)",
        "st_dwithin(a, b, 10)": "DWITHIN(a, b, 10)",
        "ST_OVERLAPS(a, b)": "OVERLAPS(a, b)",
        "st_touches(a, b)": "TOUCHES(a, b)",
        "st_disjoint(a, b)": "DISJOINT(a, b)",
    }
    for given, expected in cases.items():
        assert normalize_spatial_function_names(given) == expected


def test_normalize_spatial_function_names_is_idempotent_on_already_correct_names():
    from sigate.ui.expression_builder import normalize_spatial_function_names

    already_correct = "INTERSECTS(geom, x) AND WITHIN(geom, y)"
    assert normalize_spatial_function_names(already_correct) == already_correct


def test_normalize_spatial_function_names_handles_whitespace_before_parenthesis():
    from sigate.ui.expression_builder import normalize_spatial_function_names

    # The whole matched span (name + whitespace + opening parenthesis)
    # is replaced with a fixed literal ("INTERSECTS(") - whitespace
    # between the name and "(" is correctly collapsed away, not
    # preserved, since there's nothing in the replacement to carry it
    # forward. This test's own original assertion (expecting the
    # whitespace preserved) was simply wrong about what the regex
    # actually does - caught only once run against a real environment
    # that could execute it at all.
    assert (
        normalize_spatial_function_names("st_intersects  (a, b)") == "INTERSECTS(a, b)"
    )


def test_normalize_spatial_function_names_does_not_misfire_mid_identifier():
    """A field or value name that merely contains one of these
    substrings, without it actually being a function call (no
    following parenthesis, or preceded by other identifier
    characters), must not be rewritten."""
    from sigate.ui.expression_builder import normalize_spatial_function_names

    unchanged = "field_st_intersects_something = 1"
    assert normalize_spatial_function_names(unchanged) == unchanged


def _make_widget(qgis_app, extent_wkt="POLYGON((0 0,1 0,1 1,0 1,0 0))"):
    from sigate.ui.expression_builder import ExpressionBuilderWidget

    return ExpressionBuilderWidget(get_canvas_extent_wkt=lambda: extent_wkt)


def test_set_fields_and_rows_populates_field_list(qgis_app):
    widget = _make_widget(qgis_app)
    rows = [{"name": "A", "id_chantier": "137"}]
    widget.set_fields_and_rows(["name", "id_chantier"], rows)

    assert widget.fields_list.count() == 2
    assert [widget.fields_list.item(i).text() for i in range(2)] == [
        "name",
        "id_chantier",
    ]


def test_selecting_a_field_enables_load_buttons(qgis_app):
    widget = _make_widget(qgis_app)
    widget.set_fields_and_rows(["name"], [{"name": "A"}])

    assert not widget.load_some_button.isEnabled()
    widget.fields_list.setCurrentRow(0)
    assert widget.load_some_button.isEnabled()
    assert widget.load_all_button.isEnabled()


def test_load_all_populates_values_list_with_unique_values(qgis_app):
    widget = _make_widget(qgis_app)
    rows = [
        {"id_chantier": "137"},
        {"id_chantier": "137"},
        {"id_chantier": "140"},
    ]
    widget.set_fields_and_rows(["id_chantier"], rows)
    widget.fields_list.setCurrentRow(0)
    widget._load_all_values()

    values = {
        widget.values_list.item(i).text() for i in range(widget.values_list.count())
    }
    assert values == {"137", "140"}  # deduplicated, not 3 entries


def test_clicking_load_all_button_populates_values_through_the_real_signal_path(
    qgis_app,
):
    """The earlier tests call _load_all_values()/_load_sample_values()
    directly; this exercises the actual click -> signal -> slot path a
    real user triggers, since that's a genuinely different code path
    (button.clicked -> connected slot) and the one a reported "clicking
    does nothing" bug would actually go through."""
    widget = _make_widget(qgis_app)
    widget.set_fields_and_rows(["id_chantier"], [{"id_chantier": "137"}])
    widget.fields_list.setCurrentRow(0)
    assert widget.load_all_button.isEnabled()

    widget.load_all_button.click()

    values = {
        widget.values_list.item(i).text() for i in range(widget.values_list.count())
    }
    assert values == {"137"}


def test_load_all_works_for_field_names_with_special_characters(qgis_app):
    """End-to-end regression test for the reported bug, through the
    field selection and the real button click, using a field name that
    would have broken the old URI-string-based memory layer
    construction."""
    widget = _make_widget(qgis_app)
    widget.set_fields_and_rows(["a=b"], [{"a=b": "1"}, {"a=b": "1"}, {"a=b": "2"}])
    widget.fields_list.setCurrentRow(0)

    widget.load_all_button.click()

    values = {
        widget.values_list.item(i).text() for i in range(widget.values_list.count())
    }
    assert values == {"1", "2"}
    assert "2 distinct value" in widget.values_status_label.text()


def test_load_values_reports_when_field_has_no_values(qgis_app):
    widget = _make_widget(qgis_app)
    widget.set_fields_and_rows(["name"], [])
    widget.fields_list.setCurrentRow(0)

    widget.load_all_button.click()

    assert widget.values_list.count() == 0
    assert "No values found" in widget.values_status_label.text()


def test_selecting_a_different_field_clears_the_values_status_label(qgis_app):
    widget = _make_widget(qgis_app)
    widget.set_fields_and_rows(["a", "b"], [{"a": "1", "b": "2"}])
    widget.fields_list.setCurrentRow(0)
    widget.load_all_button.click()
    assert widget.values_status_label.text() != ""

    widget.fields_list.setCurrentRow(1)
    assert widget.values_status_label.text() == ""


def test_load_some_respects_sample_size(qgis_app):
    widget = _make_widget(qgis_app)
    rows = [{"id_chantier": str(i)} for i in range(100)]
    widget.set_fields_and_rows(["id_chantier"], rows)
    widget.fields_list.setCurrentRow(0)
    widget._load_sample_values()

    from sigate.ui.expression_builder import DEFAULT_SAMPLE_SIZE

    assert widget.values_list.count() <= DEFAULT_SAMPLE_SIZE


def test_value_filter_narrows_without_reloading(qgis_app):
    widget = _make_widget(qgis_app)
    rows = [{"name": "LHD_D001"}, {"name": "LHD_D074"}, {"name": "ORTHO_D001"}]
    widget.set_fields_and_rows(["name"], rows)
    widget.fields_list.setCurrentRow(0)
    widget._load_all_values()
    assert widget.values_list.count() == 3

    widget.value_filter_edit.setText("LHD")
    shown = {
        widget.values_list.item(i).text() for i in range(widget.values_list.count())
    }
    assert shown == {"LHD_D001", "LHD_D074"}


def test_double_click_field_inserts_field_name(qgis_app):
    widget = _make_widget(qgis_app)
    widget.set_fields_and_rows(
        ["name", "id_chantier"], [{"name": "A", "id_chantier": "1"}]
    )
    widget.fields_list.setCurrentRow(0)
    widget._on_field_double_clicked(widget.fields_list.item(0))

    assert widget.expression_text() == "name"


def test_double_click_geometry_field_inserts_geom_token_instead_of_real_name(qgis_app):
    widget = _make_widget(qgis_app)
    widget.set_fields_and_rows(
        ["name", "the_geom"], [{"name": "A"}], geometry_field_name="the_geom"
    )
    widget.fields_list.setCurrentRow(1)
    widget._on_field_double_clicked(widget.fields_list.item(1))

    assert widget.expression_text() == "$geom"


def test_double_click_value_inserts_quoted_and_escaped_literal(qgis_app):
    widget = _make_widget(qgis_app)
    widget.set_fields_and_rows(["name"], [{"name": "D'Artagnan"}])
    widget.fields_list.setCurrentRow(0)
    widget._load_all_values()
    widget._on_value_double_clicked(widget.values_list.item(0))

    assert widget.expression_text() == "'D''Artagnan'"


def test_expression_text_vs_resolved_expression_text(qgis_app):
    widget = _make_widget(qgis_app, extent_wkt="POLYGON((0 0,1 0,1 1,0 1,0 0))")
    widget.set_fields_and_rows(
        ["the_geom"], [{"the_geom": "x"}], geometry_field_name="the_geom"
    )
    widget.set_expression_text("WITHIN($geom, @map_extent)")

    assert widget.expression_text() == "WITHIN($geom, @map_extent)"
    assert (
        widget.resolved_expression_text()
        == "WITHIN(the_geom, POLYGON((0 0,1 0,1 1,0 1,0 0)))"
    )


def test_resolved_expression_text_leaves_extent_token_when_no_provider_given(qgis_app):
    from sigate.ui.expression_builder import ExpressionBuilderWidget

    widget = ExpressionBuilderWidget(get_canvas_extent_wkt=None)
    widget.set_fields_and_rows(["geom"], [{"geom": "x"}])
    widget.set_expression_text("WITHIN($geom, @map_extent)")

    assert widget.resolved_expression_text() == "WITHIN(geom, @map_extent)"


def test_sql_editor_has_field_names_registered_after_populate(qgis_app):
    widget = _make_widget(qgis_app)
    widget.set_fields_and_rows(
        ["name", "id_chantier"], [{"name": "A", "id_chantier": "1"}]
    )

    assert set(widget.sql_editor.fieldNames()) == {"name", "id_chantier"}


def test_substitute_tokens_for_qgis_builds_a_qgis_expression_not_cql():
    """Confirmed live against IGN's LiDAR HD metadata layer: QGIS's native
    WFS provider silently drops a CQL filter (bare POLYGON literal) and
    loads everything, but honours the same predicate as a QGIS expression
    - geom_from_wkt('...') over $geometry."""
    from sigate.ui.expression_builder import substitute_tokens_for_qgis

    wkt = "Polygon ((6.8 45.9, 6.95 45.9, 6.95 46, 6.8 46, 6.8 45.9))"
    result = substitute_tokens_for_qgis("ST_Within($geom, @map_extent)", wkt)

    assert result == f"WITHIN($geometry, geom_from_wkt('{wkt}'))"
    assert "$geom," not in result and "@map_extent" not in result


def test_substitute_tokens_for_qgis_leaves_plain_attribute_filters_alone():
    from sigate.ui.expression_builder import substitute_tokens_for_qgis

    assert (
        substitute_tokens_for_qgis("code_mission = '21LHD5QK2'", None)
        == "code_mission = '21LHD5QK2'"
    )
    # no extent supplied: the token is left as typed rather than guessed
    assert (
        substitute_tokens_for_qgis("intersects($geom, @map_extent)", None)
        == "intersects($geometry, @map_extent)"
    )
