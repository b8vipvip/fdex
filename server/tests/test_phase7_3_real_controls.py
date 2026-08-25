from app.auth_routes import router


def test_phase7_3_real_control_surfaces() -> None:
    methods = {(route.path, tuple(sorted(route.methods or []))) for route in router.routes}
    assert ("/api/auth/memory/status", ("GET",)) in methods
    assert ("/api/auth/memory/clear", ("POST",)) in methods
    assert ("/api/auth/data-export", ("GET",)) in methods
