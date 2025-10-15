from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_ui_overview_endpoint():
    response = client.get("/v1/ui/overview")
    assert response.status_code == 200
    body = response.json()
    assert body.get("success") is True
    assert "overview" in body


def test_ui_leaderboard_endpoint():
    response = client.get("/v1/ui/leaderboard?type=rounds&limit=5")
    assert response.status_code == 200
    body = response.json()
    assert body.get("success") is True
    assert "leaderboard" in body


def test_ui_agents_endpoint():
    response = client.get("/v1/ui/agents?limit=5")
    assert response.status_code == 200
    body = response.json()
    assert body.get("success") is True
    assert "agents" in body
