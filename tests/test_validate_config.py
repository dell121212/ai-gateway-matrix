from scripts import validate_config


def test_repository_configuration_is_consistent():
    errors, _warnings = validate_config.validate()
    assert errors == []
