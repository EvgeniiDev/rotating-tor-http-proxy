from __future__ import annotations

from pathlib import Path

from src.tor_process import (  # type: ignore[import-not-found]
    TorInstance,
    TorRuntimeMetadata,
)


class DummyProcess:
    def __init__(self, pid: int) -> None:
        self.pid = pid

    def poll(self) -> None:
        return None


def test_rotate_circuits_uses_tor_signal(monkeypatch, tmp_path: Path) -> None:
    metadata = TorRuntimeMetadata(
        socks_port=9_050,
        config_path=tmp_path / "torrc",
        data_dir=tmp_path / "data",
        log_path=tmp_path / "tor.log",
        pid_file=tmp_path / "tor.pid",
    )
    instance = TorInstance(
        instance_id=1,
        tor_binary="tor",
        metadata=metadata,
        exit_nodes=[],
        health_check_url="http://example.com",
        health_timeout_seconds=1.0,
        max_health_retries=1,
    )
    instance.process = DummyProcess(pid=1_234)
    calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
        calls.append((cmd, kwargs))

        class Result:
            stdout = ""
            stderr = ""

        return Result()

    monkeypatch.setattr("src.tor_process.subprocess.run", fake_run)

    instance.rotate_circuits()

    assert calls
    cmd, kwargs = calls[0]
    assert cmd == ["tor", "--signal", "NEWNYM", "--PidFile", str(metadata.pid_file)]
    assert kwargs["check"] is True
    assert kwargs["text"] is True
