from __future__ import annotations

from pathlib import Path

from desktop import app as desktop_app


ROOT = Path(__file__).resolve().parents[1]


def test_internal_url_policy():
    assert desktop_app._is_internal("http://127.0.0.1:4000/")
    assert desktop_app._is_internal("http://localhost:4000/api/channels")
    assert desktop_app._is_internal("about:blank")
    assert not desktop_app._is_internal("https://open.bigmodel.cn/")
    assert not desktop_app._is_internal("https://example.com/x")


def test_backend_control_scheme_is_strictly_scoped():
    assert desktop_app._backend_action_from_url("ai-gateway://backend/start") == "start"
    assert desktop_app._backend_action_from_url("ai-gateway://backend/restart") == "restart"
    assert desktop_app._backend_action_from_url("ai-gateway://backend/stop") == "stop"
    assert desktop_app._backend_action_from_url("ai-gateway://backend/delete") is None
    assert desktop_app._backend_action_from_url("https://example.com/backend/stop") is None


def test_dashboard_url_marks_app_shell():
    url = desktop_app.dashboard_url()
    assert "app=1" in url
    assert url.startswith("http://127.0.0.1:4000/console/")


def test_desktop_assets_exist():
    assert (ROOT / "desktop" / "app.py").is_file()
    assert (ROOT / "desktop" / "icon.svg").is_file()
    assert (ROOT / "desktop" / "ai-gateway-matrix.desktop.in").is_file()


def test_frontend_has_app_shell_styles():
    html = (ROOT / "dashboard" / "static" / "index.html").read_text(encoding="utf-8")
    assert "app-shell" in html
    assert "AI Gateway Matrix" in html
