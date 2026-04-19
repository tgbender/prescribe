def test_package_imports() -> None:
    import config_helper

    assert hasattr(config_helper, "Document")
    assert hasattr(config_helper, "TomlAdapter")
    assert hasattr(config_helper, "JsoncAdapter")
