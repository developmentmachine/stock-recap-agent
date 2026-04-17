from stock_recap.skills.loader import (
    load_skill_overlay_for_mode,
    resolve_skill_id_for_mode,
    skill_bundle_version,
)


def test_manifest_version():
    assert skill_bundle_version() == "1.0.0"


def test_resolve_daily():
    assert resolve_skill_id_for_mode("daily") == "a_share.daily_recap"


def test_overlay_daily_contains_skill_body():
    doc = load_skill_overlay_for_mode("daily")
    assert doc is not None
    assert "RecapDaily" in doc.body or "daily" in doc.body.lower()


def test_override_unknown_returns_none_body_safe():
    doc = load_skill_overlay_for_mode("daily", override_skill_id="does.not.exist")
    assert doc is None
