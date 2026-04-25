import json

import pytest

import agent_platform.config.settings as settings_module
from agent_platform.skills.loader import (
    clear_skill_manifest_cache,
    list_registered_skills,
    load_skill_document,
    load_skill_overlay_for_mode,
    resolve_skill_id_for_mode,
    skill_bundle_version,
)


@pytest.fixture(autouse=True)
def _reset_skill_cache(monkeypatch: pytest.MonkeyPatch):
    clear_skill_manifest_cache()
    yield
    monkeypatch.delenv("RECAP_SKILL_EXTRA_DIRS", raising=False)
    settings_module._settings_instance = None
    clear_skill_manifest_cache()


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


def test_extra_skill_dir_overrides_daily(monkeypatch, tmp_path):
    bundle = tmp_path / "ext"
    (bundle / "plugin_daily").mkdir(parents=True)
    (bundle / "plugin_daily" / "SKILL.md").write_text(
        "---\nname: Plugin Daily\n---\nPlugin body unique xyz",
        encoding="utf-8",
    )
    manifest = {
        "bundle_version": "0.0.0",
        "mode_to_skill_id": {"daily": "plugin.daily"},
        "skills": [
            {
                "id": "plugin.daily",
                "path": "plugin_daily/SKILL.md",
                "description": "from extra dir",
            }
        ],
    }
    (bundle / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    settings_module._settings_instance = None
    monkeypatch.setenv("RECAP_SKILL_EXTRA_DIRS", str(bundle))
    clear_skill_manifest_cache()

    assert resolve_skill_id_for_mode("daily") == "plugin.daily"
    doc = load_skill_overlay_for_mode("daily")
    assert doc is not None
    assert "Plugin Daily" in doc.name
    assert "xyz" in doc.body
    ids = {s["id"] for s in list_registered_skills()}
    assert "plugin.daily" in ids
    assert "a_share.daily_recap" in ids


def test_entry_point_skill_bundle(monkeypatch):
    import agent_platform.skills.loader as loader_mod

    class _Eps:
        @staticmethod
        def select(*, group: str):
            from importlib.metadata import EntryPoint

            if group != "agent_platform.skills":
                return ()
            return (
                EntryPoint(
                    name="fixture_ep",
                    value="tests.fixtures.ep_roots:ROOT",
                    group="agent_platform.skills",
                ),
            )

    monkeypatch.setattr(loader_mod.metadata, "entry_points", lambda: _Eps())
    clear_skill_manifest_cache()

    assert resolve_skill_id_for_mode("strategy") == "ep.strategy_skill"
    doc = load_skill_overlay_for_mode("strategy")
    assert doc is not None
    assert "entry point" in doc.body.lower()
    d = load_skill_document("ep.strategy_skill")
    assert d is not None
    assert "EP Strategy Plugin" in d.name
