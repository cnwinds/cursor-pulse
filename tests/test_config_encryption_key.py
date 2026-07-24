"""Regression: empty PULSE_CREDENTIAL_ENCRYPTION_KEY must not clear yaml encryption_key."""

from pulse.config import load_config


def test_yaml_encryption_key_preserved_when_env_empty(monkeypatch, tmp_path):
    monkeypatch.delenv("PULSE_CREDENTIAL_ENCRYPTION_KEY", raising=False)
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        "tenant:\n  slug: enc-test\n"
        "credentials:\n  encryption_key: yaml-key-123\n",
        encoding="utf-8",
    )
    cfg = load_config(cfg_path)
    assert cfg.credentials.encryption_key == "yaml-key-123"


def test_env_encryption_key_overrides_yaml(monkeypatch, tmp_path):
    monkeypatch.setenv("PULSE_CREDENTIAL_ENCRYPTION_KEY", "env-key-456")
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        "tenant:\n  slug: enc-test\n"
        "credentials:\n  encryption_key: yaml-key-123\n",
        encoding="utf-8",
    )
    cfg = load_config(cfg_path)
    assert cfg.credentials.encryption_key == "env-key-456"
