def test_phase7_3_data_controls_smoke() -> None:
    from app.auth_routes import router

    paths = {route.path for route in router.routes}
    assert "/api/auth/memory/status" in paths
    assert "/api/auth/memory/clear" in paths
    assert "/api/auth/data-export" in paths
