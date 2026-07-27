from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from pulse.dev.manager import (
    _child_env,
    _find_listening_pid,
    _is_cursor_mitm_proxy_url,
    _is_project_channel_command,
    _is_project_web_command,
    is_running,
    restart,
    status,
    stop,
)
from pulse.dev.services import DEFAULT_SERVICES, SERVICES


def test_default_services():
    assert DEFAULT_SERVICES == ("web", "assistant", "channel", "admin", "proxy")
    assert set(SERVICES) == {"web", "admin", "channel", "assistant", "proxy"}


def test_is_cursor_mitm_proxy_url():
    assert _is_cursor_mitm_proxy_url("http://192.168.11.39:8317") is True
    assert _is_cursor_mitm_proxy_url(
        "http://192.168.11.39:8317",
        proxy_public_url="http://192.168.11.39:8317",
    ) is True
    assert _is_cursor_mitm_proxy_url("http://127.0.0.1:7890") is False


def test_child_env_strips_cursor_mitm_https_proxy(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://192.168.11.39:8317")
    monkeypatch.setenv("HTTP_PROXY", "http://192.168.11.39:8317")
    monkeypatch.setenv("PROXY_PUBLIC_URL", "http://192.168.11.39:8317")
    monkeypatch.setenv("PROXY_UPSTREAM_URL", "http://127.0.0.1:7890")
    env = _child_env()
    assert "HTTPS_PROXY" not in env
    assert "HTTP_PROXY" not in env
    # Real egress upstream must not be stripped from unrelated vars.
    assert env.get("PROXY_UPSTREAM_URL") == "http://127.0.0.1:7890"


def test_child_env_keeps_real_egress_https_proxy(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:7890")
    monkeypatch.setenv("PROXY_PUBLIC_URL", "http://192.168.11.39:8317")
    env = _child_env()
    assert env.get("HTTPS_PROXY") == "http://127.0.0.1:7890"


def test_child_env_empty_proxy_in_dotenv_clears_shell_proxy(tmp_path, monkeypatch):
    """Explicit empty HTTPS_PROXY=/HTTP_PROXY= in .env removes shell exports for children."""
    env_file = tmp_path / ".env"
    env_file.write_text(
        "HTTPS_PROXY=\nHTTP_PROXY=\nALL_PROXY=\nASSISTANT_SERVICE_TOKEN=keep-me\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:7890")
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:7890")
    monkeypatch.setenv("ALL_PROXY", "http://127.0.0.1:7890")
    monkeypatch.setenv("ASSISTANT_SERVICE_TOKEN", "from-shell")
    monkeypatch.setattr("pulse.dev.manager.project_root", lambda: tmp_path)

    env = _child_env()
    assert "HTTPS_PROXY" not in env
    assert "HTTP_PROXY" not in env
    assert "ALL_PROXY" not in env
    # Empty-clear is proxy-only; other shell values are not wiped by blank .env keys.
    assert env.get("ASSISTANT_SERVICE_TOKEN") == "from-shell"


def test_services_includes_proxy():
    assert "proxy" in SERVICES
    assert SERVICES["proxy"].port == 8317
    assert "proxy" in DEFAULT_SERVICES


def test_build_command_proxy(tmp_path, monkeypatch):
    import sys

    from pulse.dev import services as svc

    fake = tmp_path / (
        "cursor-pulse-proxy.exe" if sys.platform == "win32" else "cursor-pulse-proxy"
    )
    fake.write_bytes(b"x")
    monkeypatch.setattr(svc, "ensure_proxy_binary", lambda root=None: fake)
    command, cwd, extra = svc.build_command("proxy")
    assert command[0] == str(fake)
    assert "-listen" in command
    assert "0.0.0.0:8317" in command
    assert cwd == svc.project_root() / "proxy"
    assert extra == {}


def test_ensure_proxy_binary_rebuilds_when_go_newer(tmp_path, monkeypatch):
    import os
    import time

    from pulse.dev import services as svc

    proxy_dir = tmp_path / "proxy"
    proxy_dir.mkdir()
    binary = proxy_dir / svc.proxy_binary_name()
    binary.write_bytes(b"old")
    old_mtime = time.time() - 100
    os.utime(binary, (old_mtime, old_mtime))

    src = proxy_dir / "main.go"
    src.write_text("package main\n", encoding="utf-8")
    os.utime(src, None)  # now

    built = {"n": 0}

    def fake_run(cmd, cwd=None, check=False):
        built["n"] += 1
        Path(cwd, svc.proxy_binary_name()).write_bytes(b"new")

    monkeypatch.setattr(svc, "project_root", lambda: tmp_path)
    monkeypatch.setattr(svc, "_find_go", lambda: "go")
    monkeypatch.setattr(svc.subprocess, "run", fake_run)

    out = svc.ensure_proxy_binary(tmp_path)
    assert built["n"] == 1
    assert out.read_bytes() == b"new"

    # Up to date → no rebuild
    out2 = svc.ensure_proxy_binary(tmp_path)
    assert built["n"] == 1
    assert out2 == out


def test_build_command_channel_includes_reload():
    from pulse.dev.services import build_command

    command, _cwd, extra = build_command("channel", config_path="config.yaml")
    assert "--reload" in command
    assert "channel" in command
    assert extra == {}


def test_build_command_web_includes_reload():
    from pulse.dev.services import build_command, project_root

    command, cwd, extra = build_command("web", config_path="config.yaml")
    assert "--reload" in command
    assert "web" in command
    assert cwd == project_root()
    assert extra == {}


def test_build_command_assistant_includes_reload():
    from pulse.dev.services import build_command

    command, _cwd, extra = build_command("assistant", config_path="config.yaml")
    assert "--reload" in command
    assert "assistant_platform" in " ".join(command)
    assert extra == {}


def test_python_reload_dirs_includes_assistant_platform():
    from pulse.dev.reload import python_reload_dirs

    dirs = python_reload_dirs()
    assert any(d.endswith("pulse") for d in dirs)
    assert any(d.endswith("assistant_platform") for d in dirs)


def test_resolve_services_unknown():
    import pytest
    from pulse.dev.manager import DevManagerError, _resolve_services

    with pytest.raises(DevManagerError):
        _resolve_services(["nope"])


def test_status_shape():
    rows = status()
    assert len(rows) == len(SERVICES)
    names = [row["service"] for row in rows]
    assert set(names) == set(SERVICES)
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
    def fake_run(cmd, **kwargs):
        if cmd[0] == "powershell":
            return type("R", (), {"stdout": "", "returncode": 0})()
        return type("R", (), {"stdout": netstat, "returncode": 0})()

    with (
        patch("pulse.dev.manager.sys.platform", "win32"),
        patch("pulse.dev.manager.subprocess.run", side_effect=fake_run),
        patch("pulse.dev.manager._pid_alive", return_value=True),
    ):
        assert _find_listening_pid(8080) == 33012
        assert _find_listening_pid(5173) == 51076
        assert _find_listening_pid(9999) is None


def test_stop_kills_orphan_on_port_when_no_pid_file():
    with (
        patch("pulse.dev.manager._load_state", return_value=None),
        patch("pulse.dev.manager._port_open", side_effect=lambda port: port == 8080),
        patch("pulse.dev.manager._find_web_orphan_pids", return_value=[]),
        patch("pulse.dev.manager._kill_port_listeners", return_value=[33012]) as kill_port,
        patch("pulse.dev.manager._clear_state") as clear,
        patch("pulse.dev.manager.time.sleep"),
    ):
        stopped = stop(["web"])

    assert stopped == ["web"]
    assert kill_port.call_count == 2
    clear.assert_not_called()


def test_stop_cleans_port_after_tracked_pid():
    state = {"pid": 1000}
    with (
        patch("pulse.dev.manager._load_state", return_value=state),
        patch("pulse.dev.manager._port_open", return_value=True),
        patch("pulse.dev.manager._find_web_orphan_pids", return_value=[]),
        patch("pulse.dev.manager._kill_port_listeners", return_value=[2000]) as kill_port,
        patch("pulse.dev.manager._kill_pid") as kill,
        patch("pulse.dev.manager._clear_state") as clear,
        patch("pulse.dev.manager.time.sleep"),
    ):
        stopped = stop(["web"])

    assert stopped == ["web"]
    kill.assert_called_once_with(1000)
    assert kill_port.call_count == 2
    clear.assert_called_once_with("web")


def test_is_project_channel_command():
    root = Path(r"C:\projects\cursor-pulse")
    with patch("pulse.dev.manager.project_root", return_value=root):
        assert _is_project_channel_command(rf'{root}\.venv\Scripts\pulse.exe" channel') is True
        assert _is_project_channel_command(rf'{root}\.venv\Scripts\pulse.exe" serve') is True
        assert _is_project_channel_command(rf'{root}\.venv\Scripts\pulse.exe" web') is False
        assert _is_project_channel_command(r"C:\other\pulse.exe channel") is False


def test_is_running_channel_detects_orphan_serve():
    with (
        patch("pulse.dev.manager._load_state", return_value=None),
        patch("pulse.dev.manager._find_channel_serve_pids", return_value=[61876]),
    ):
        assert is_running("channel") is True


def test_stop_kills_orphan_channel_serve():
    with (
        patch("pulse.dev.manager._load_state", return_value=None),
        patch("pulse.dev.manager._port_open", return_value=False),
        patch("pulse.dev.manager._find_channel_serve_pids", return_value=[61876]),
        patch("pulse.dev.manager._kill_pid") as kill,
        patch("pulse.dev.manager._clear_state") as clear,
        patch("pulse.dev.manager.time.sleep"),
    ):
        stopped = stop(["channel"])

    assert stopped == ["channel"]
    kill.assert_called_once_with(61876)
    clear.assert_not_called()


def test_is_project_web_command():
    root = Path(r"C:\projects\cursor-pulse")
    with patch("pulse.dev.manager.project_root", return_value=root):
        assert _is_project_web_command(rf'{root}\.venv\Scripts\pulse.exe" web --reload') is True
        assert _is_project_web_command(rf'{root}\.venv\Scripts\pulse.exe" serve') is False
        assert (
            _is_project_web_command(
                rf'{root}\.venv\Scripts\python.exe" "-c" "from multiprocessing.spawn import spawn_main; '
                rf'spawn_main(parent_pid=34952, pipe_handle=1220)" "--multiprocessing-fork"'
            )
            is True
        )


def test_stop_kills_orphan_web_children():
    with (
        patch("pulse.dev.manager._load_state", return_value={"pid": 1000}),
        patch("pulse.dev.manager._port_open", return_value=True),
        patch("pulse.dev.manager._find_listening_pid", return_value=None),
        patch("pulse.dev.manager._find_web_orphan_pids", return_value=[44836]),
        patch("pulse.dev.manager._kill_pid") as kill,
        patch("pulse.dev.manager._clear_state"),
        patch("pulse.dev.manager.time.sleep"),
    ):
        stopped = stop(["web"])

    assert stopped == ["web"]
    assert kill.call_args_list == [((1000,),), ((44836,),)]


def test_restart_without_args_targets_all_defaults():
    defaults = list(DEFAULT_SERVICES)
    with (
        patch("pulse.dev.manager.stop") as stop_fn,
        patch("pulse.dev.manager.start", return_value=defaults) as start_fn,
        patch("pulse.dev.manager.time.sleep"),
    ):
        restarted = restart()

    stop_fn.assert_called_once_with(defaults)
    start_fn.assert_called_once_with(defaults, config_path="config.yaml")
    assert restarted == defaults
