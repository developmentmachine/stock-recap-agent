from stock_recap.resources.prompts import loader


def test_manifest_bundle_version():
    v = loader.prompt_bundle_version()
    assert len(v) >= 8
    assert loader.PROMPT_BASE_VERSION == v


def test_system_recap_non_empty():
    text = loader.system_recap_base()
    assert "JSON" in text or "json" in text.lower()
    assert len(text) > 200
