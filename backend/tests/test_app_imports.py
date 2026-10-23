"""The whole application imports: a syntax or import error in any router fails here, not at deploy."""


def test_main_imports():
    from app.main import app

    paths = {getattr(route, "path", None) for route in app.routes}
    assert "/api/v1/apps/{app_id}/answer" in paths
    assert "/api/v1/apps/{app_id}/lessons" in paths
    assert "/api/apps/{app_id}/copy" in paths
