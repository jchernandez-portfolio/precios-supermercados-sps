from __future__ import annotations

import precios_supermercados.diagnostics.la_colonia_location_binding_capture as capture

CONSUMED_AUTHORIZATION_IDS = frozenset(
    {
        "LC-location-binding-336",
        "LC-location-binding-331",
        "LC-location-binding-332",
        "LC-location-binding-333",
        "LC-location-binding-334",
        "LC-location-binding-335",
        "LC-location-binding-337",
    }
)


def test_all_location_binding_authorizations_are_consumed_and_live_is_closed() -> None:
    assert capture.LIVE_EXECUTION_ENABLED is False
    assert capture.ACTIVE_AUTHORIZATION_IDS == frozenset()
    assert capture.CONSUMED_AUTHORIZATION_IDS == CONSUMED_AUTHORIZATION_IDS

    for authorization_id in CONSUMED_AUTHORIZATION_IDS:
        result = capture.run_capture(authorization_id=authorization_id)
        assert result.stop_reason == "authorization_id_consumed"
        assert result.browser_started is False
        assert result.target_navigation_started is False
        assert result.production_authority is False
        assert result.catalog_accepted is False
        assert result.extraction_enabled is False
