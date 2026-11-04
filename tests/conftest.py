import pytest


@pytest.fixture(autouse=True)
def _isolate_config(monkeypatch, tmp_path):
    """Keep every test away from the developer's real credentials."""
    monkeypatch.setenv("RIPPLING_CONFIG_DIR", str(tmp_path / "_rippling_cfg"))
