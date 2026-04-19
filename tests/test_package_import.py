def test_package_imports() -> None:
    import prescribe

    assert hasattr(prescribe, "Document")
    assert hasattr(prescribe, "TomlAdapter")
    assert hasattr(prescribe, "JsoncAdapter")
