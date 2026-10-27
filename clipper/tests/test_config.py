import os
from pathlib import Path

import pytest

from clipper.config import PROJECT_DIR, Config, ConfigError, Paths, Secrets, build, load_config, load_dotenv


def test_shipped_config_is_valid():
    config = load_config(PROJECT_DIR / "config.yaml")
    assert "squeezie" in config.twitch.streamers
    assert config.ai.model == "claude-opus-5-5"
    assert config.publish.tiktok.mode == "inbox"


def test_unknown_keys_and_bad_values_are_explained():
    with pytest.raises(ConfigError, match="shortz"):
        build(Config, {"shortz": {}})
    with pytest.raises(ConfigError, match="config.shorts.layout"):
        build(Config, {"shorts": {"layout": "diagonal"}})
    with pytest.raises(ConfigError, match="config.twitch.min_views"):
        build(Config, {"twitch": {"min_views": "beaucoup"}})
    with pytest.raises(ConfigError, match="config.publish.youtube.enabled"):
        build(Config, {"publish": {"youtube": {"enabled": "peut-être"}}})


def test_values_are_coerced_where_harmless():
    config = build(Config, {"publish": {"youtube": {"category_id": 24, "enabled": True}}, "shorts": {"min_duration": 10}})
    assert config.publish.youtube.category_id == "24"
    assert config.publish.youtube.enabled is True
    assert config.shorts.min_duration == 10.0


def test_streamer_overrides_and_blocklist(tmp_path: Path):
    path = tmp_path / "config.yaml"
    path.write_text(
        "twitch:\n  streamers: ['@Squeezie', Gotaga]\nblocklist: ['@NoClips']\n"
        "streamers:\n  Gotaga:\n    layout: split\n    facecam: [0, 0.6, 0.25, 0.4]\n  Other:\n    skip: true\n",
        encoding="utf-8",
    )
    config = load_config(path)
    assert config.twitch.streamers == ["squeezie", "gotaga"]
    assert config.override_for("GOTAGA").layout == "split"
    assert config.is_blocked("noclips") and config.is_blocked("other") and not config.is_blocked("gotaga")


def test_facecam_override_is_validated(tmp_path: Path):
    path = tmp_path / "config.yaml"
    path.write_text("streamers:\n  a:\n    facecam: [0, 0, 2, 1]\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="facecam"):
        load_config(path)


def test_secrets_from_env():
    secrets = Secrets.from_env({"TWITCH_CLIENT_ID": " id ", "TWITCH_CLIENT_SECRET": "s", "YOUTUBE_CLIENT_ID": "x", "GH_SECRETS_TOKEN": "t"})
    assert secrets.twitch_client_id == "id"
    assert secrets.twitch_api and not secrets.youtube and not secrets.tiktok and not secrets.telegram
    assert secrets.gh_secrets_token == "t"


def test_dotenv_never_overrides_the_environment(tmp_path: Path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("# comment\nCLIPPER_TEST_A='from file'\nexport CLIPPER_TEST_B=b\nCLIPPER_TEST_C=c\n", encoding="utf-8")
    monkeypatch.setenv("CLIPPER_TEST_C", "real")
    monkeypatch.delenv("CLIPPER_TEST_A", raising=False)
    monkeypatch.delenv("CLIPPER_TEST_B", raising=False)
    load_dotenv(env)
    assert os.environ["CLIPPER_TEST_A"] == "from file"
    assert os.environ["CLIPPER_TEST_B"] == "b"
    assert os.environ["CLIPPER_TEST_C"] == "real"


def test_paths_follow_environment(tmp_path: Path):
    paths = Paths.resolve({"CLIPPER_DATA_DIR": str(tmp_path / "d"), "CLIPPER_STATE_PATH": str(tmp_path / "s.json")})
    assert paths.state == (tmp_path / "s.json").resolve()
    assert paths.work == (tmp_path / "d" / "work").resolve()
