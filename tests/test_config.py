from crewguard.config import DEFAULT_BACKENDS, load_settings


def _env(tmp_path, body: str) -> str:
    p = tmp_path / ".env"
    p.write_text(body)
    return str(p)


def test_loads_values(tmp_path):
    s = load_settings(
        _env(
            tmp_path,
            "DISCORD_TOKEN=abc\nAPP_ID=123\nGUILD_ID=456\nDB_PATH=x.db\n"
            "CLASSIFIER_BACKENDS=http://a:11434,http://b:11434\n",
        )
    )
    assert s.discord_token == "abc"
    assert s.app_id == 123
    assert s.guild_id == 456
    assert s.db_path == "x.db"
    assert s.classifier_backends == ("http://a:11434", "http://b:11434")


def test_placeholder_values_are_treated_as_unset(tmp_path):
    s = load_settings(_env(tmp_path, "APP_ID=paste-application-id\nGUILD_ID=paste-guild-id\n"))
    assert s.app_id is None
    assert s.guild_id is None


def test_backends_default_and_strip_trailing_slash(tmp_path):
    assert load_settings(_env(tmp_path, "")).classifier_backends == DEFAULT_BACKENDS
    s = load_settings(_env(tmp_path, "CLASSIFIER_BACKENDS= http://a:11434/ , http://b:11434 \n"))
    assert s.classifier_backends == ("http://a:11434", "http://b:11434")
