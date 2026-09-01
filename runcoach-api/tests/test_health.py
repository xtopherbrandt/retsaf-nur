from fastapi.testclient import TestClient

from runcoach_api import __version__
from runcoach_api.main import app


def test_health_endpoint_returns_ok_and_version() -> None:
    client = TestClient(app)

    response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert isinstance(body["version"], str)
    assert len(body["version"]) > 0
    assert body["version"] == __version__
