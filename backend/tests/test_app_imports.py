"""The whole application imports: a syntax or import error in any router fails here, not at deploy."""


def test_main_imports_with_every_router():
    from app.main import app

    paths = set(app.openapi()["paths"])
    assert "/api/v1/apps/{app_id}/answer" in paths
    assert "/api/v1/apps/{app_id}/lessons" in paths
    assert "/api/apps/{app_id}/copy" in paths
