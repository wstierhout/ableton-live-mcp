import json

import pytest

from ableton_live_mcp import __version__, cli


def test_install_creates_missing_remote_scripts_and_is_idempotent(tmp_path):
    assert cli.install(tmp_path) == 0
    dest = tmp_path / "Remote Scripts" / "AbletonMCP" / "__init__.py"
    assert dest.read_bytes() == cli._remote_script_source()
    assert cli.install(tmp_path) == 0
    assert not list(dest.parent.glob("*.bak"))


def test_replace_requires_opt_in_and_preserves_every_backup(tmp_path):
    cli.install(tmp_path)
    dest = cli._target(tmp_path)
    for old in (b"other bridge", b"older version"):
        dest.write_bytes(old)
        with pytest.raises(ValueError, match="different"):
            cli.install(tmp_path)
        assert dest.read_bytes() == old
        cli.install(tmp_path, replace=True)
    backups = {p.read_bytes() for p in dest.parent.glob("*.bak")}
    assert backups == {b"other bridge", b"older version"}


def test_missing_library_and_symlink_are_rejected(tmp_path):
    with pytest.raises(ValueError, match="not found"):
        cli.install(tmp_path / "missing")
    (tmp_path / "Remote Scripts").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        cli.install(tmp_path)


def test_uninstall_preserves_recovery_and_refuses_foreign_script(tmp_path):
    cli.install(tmp_path)
    dest = cli._target(tmp_path)
    assert cli.uninstall(tmp_path) == 0
    assert not dest.exists()
    assert next(dest.parent.glob("*.bak")).read_bytes() == cli._remote_script_source()
    dest.write_bytes(b"foreign")
    with pytest.raises(ValueError):
        cli.uninstall(tmp_path)


def test_doctor_requires_running_and_installed_version_match(tmp_path, monkeypatch, capsys):
    cli.install(tmp_path)
    monkeypatch.setattr(cli, "_probe", lambda: {"tempo": 120, "bridge_version": "old"})
    assert cli.doctor(tmp_path, json_output=True) == 1
    capsys.readouterr()
    monkeypatch.setattr(cli, "_probe", lambda: {"tempo": 120, "bridge_version": __version__})
    assert cli.doctor(tmp_path, json_output=True) == 1
    capsys.readouterr()
    monkeypatch.setattr(
        cli, "_probe", lambda: {"tempo": 120, "bridge_version": __version__, "bridge_protocol": 2}
    )
    assert cli.doctor(tmp_path, json_output=True) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True
    cli._target(tmp_path).write_bytes(b"old script on disk")
    assert cli.doctor(tmp_path) == 1


@pytest.mark.parametrize("reply", [b'{"status":"error"}', b'{"status":"success","result":{}}'])
def test_probe_rejects_errors_and_wrong_service(reply, monkeypatch):
    class Socket:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def settimeout(self, _):
            pass

        def sendall(self, _):
            pass

        def recv(self, _):
            return reply

    monkeypatch.setattr(cli.socket, "create_connection", lambda *a, **kw: Socket())
    with pytest.raises(ValueError):
        cli._probe()


def test_user_library_override(tmp_path, monkeypatch):
    monkeypatch.setenv("ABLETON_USER_LIBRARY", str(tmp_path))
    assert cli._remote_scripts_dir() == tmp_path / "Remote Scripts"
    assert cli._remote_scripts_dir(tmp_path / "explicit") == tmp_path / "explicit/Remote Scripts"
