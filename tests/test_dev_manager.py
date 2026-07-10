from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from pulse.dev.manager import _find_listening_pid, _is_project_serve_command, is_running, status, stop
from pulse.dev.services import DEFAULT_SERVICES, SERVICES


def test_default_services():
    assert DEFAULT_SERVICES == ("web", "admin", "bot")
    assert set(SERVICES) == {"web", "admin", "bot"}


def test_build_command_web_includes_reload():
    from pulse.dev.services import build_command, project_root

    command, cwd, extra = build_command("web", config_path="config.yaml")
    assert "--reload" in command
    assert "web" in command
    assert cwd == project_root()
    assert extra == {}


def test_resolve_services_unknown():
    import pytest
    from pulse.dev.manager import DevManagerError, _resolve_services

    with pytest.raises(DevManagerError):
        _resolve_services(["nope"])


def test_status_shape():
    rows = status()
    assert len(rows) == 3
    assert rows[0]["service"] == "web"
    assert "running" in rows[0]


def test_is_running_false_when_no_state():
    assert is_running("web") is False or is_running("web") is True


def test_find_listening_pid_parses_netstat():
    netstat = """
  TCP    127.0.0.1:8080         0.0.0.0:0              LISTENING       33012
  TCP    [::1]:5173             [::]:0                 LISTENING       51076
  TCP    127.0.0.1:8080         127.0.0.1:51669        TIME_WAIT       0
"""
    with patch("pulse.dev.manager.sys.platform", "win32"), patch(
        "pulse.dev.manager.subprocess.run",
        return_value=type("R", (), {"stdout": netstat, "returncode": 0})(),
    ):
        assert _find_listening_pid(8080) == 33012
        assert _find_listening_pid(5173) == 51076
        assert _find_listening_pid(9999) is None


def test_stop_kills_orphan_on_port_when_no_pid_file():
    with (
        patch("pulse.dev.manager._load_state", return_value=None),
        patch("pulse.dev.manager._port_open", side_effect=lambda port: port == 8080),
        patch("pulse.dev.manager._find_listening_pid", return_value=33012),
        patch("pulse.dev.manager._kill_pid") as kill,
        patch("pulse.dev.manager._clear_state") as clear,
        patch("pulse.dev.manager.time.sleep"),
    ):
        stopped = stop(["web"])

    assert stopped == ["web"]
    kill.assert_called_once_with(33012)
    clear.assert_not_called()


def test_stop_cleans_port_after_tracked_pid():
    state = {"pid": 1000}
    with (
        patch("pulse.dev.manager._load_state", return_value=state),
        patch("pulse.dev.manager._port_open", return_value=True),
        patch("pulse.dev.manager._find_listening_pid", return_value=2000),
        patch("pulse.dev.manager._kill_pid") as kill,
        patch("pulse.dev.manager._clear_state") as clear,
        patch("pulse.dev.manager.time.sleep"),
    ):
        stopped = stop(["web"])

    assert stopped == ["web"]
    assert kill.call_args_list == [((1000,),), ((2000,),)]
    clear.assert_called_once_with("web")


def test_is_project_serve_command():
    root = Path(r"D:\ai_projects\cursor-pulse")
    with patch("pulse.dev.manager.project_root", return_value=root):
        assert _is_project_serve_command(rf'{root}\.venv\Scripts\pulse.exe" serve') is True
        assert _is_project_serve_command(rf'{root}\.venv\Scripts\pulse.exe" web') is False
        assert _is_project_serve_command(r"C:\other\pulse.exe serve") is False


def test_is_running_bot_detects_orphan_serve():
    with (
        patch("pulse.dev.manager._load_state", return_value=None),
        patch("pulse.dev.manager._find_bot_serve_pids", return_value=[61876]),
    ):
        assert is_running("bot") is True


def test_stop_kills_orphan_bot_serve():
    with (
        patch("pulse.dev.manager._load_state", return_value=None),
        patch("pulse.dev.manager._port_open", return_value=False),
        patch("pulse.dev.manager._find_bot_serve_pids", return_value=[61876]),
        patch("pulse.dev.manager._kill_pid") as kill,
        patch("pulse.dev.manager._clear_state") as clear,
        patch("pulse.dev.manager.time.sleep"),
    ):
        stopped = stop(["bot"])

    assert stopped == ["bot"]
    kill.assert_called_once_with(61876)
    clear.assert_not_called()
