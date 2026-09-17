"""
Tests for sigate.ui.icons and its use across all three
QgsSourceSelectProvider subclasses - confirms a real icon is returned,
not the empty placeholder each provider used before an actual icon.png
existed.
"""


def test_plugin_icon_file_exists_at_expected_path():
    from sigate.ui.icons import _ICON_PATH

    assert _ICON_PATH.is_file()
    assert _ICON_PATH.name == "icon.png"


def test_plugin_icon_returns_a_non_null_icon(qgis_app):
    from sigate.ui.icons import plugin_icon

    icon = plugin_icon()
    assert not icon.isNull()


def test_all_three_providers_return_the_real_icon_not_an_empty_placeholder(qgis_app):
    from sigate.ui.bulk_listing_provider import BulkListingSourceSelectProvider
    from sigate.ui.wfs_provider import WfsSourceSelectProvider
    from sigate.ui.wmts_wms_provider import WmtsWmsSourceSelectProvider

    for provider_cls in (
        WmtsWmsSourceSelectProvider,
        BulkListingSourceSelectProvider,
        WfsSourceSelectProvider,
    ):
        icon = provider_cls().icon()
        assert not icon.isNull(), (
            f"{provider_cls.__name__} still returns an empty/null icon"
        )
