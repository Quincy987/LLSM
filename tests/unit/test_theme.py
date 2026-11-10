"""Panel palettes: mode resolution, derived colours and readable contrast."""
from __future__ import annotations

import pytest

from llsm.conversation import theme


def test_explicit_modes_pass_through():
    assert theme.resolve_mode("light") == "light"
    assert theme.resolve_mode("dark") == "dark"


def test_system_mode_never_raises(monkeypatch):
    def boom():
        raise OSError("no registry")

    monkeypatch.setattr(theme, "_windows_prefers_light", boom)
    assert theme.resolve_mode("system") == "light"
    assert theme.resolve_mode(None) == "light"


@pytest.mark.parametrize("prefers_light, expected", [(True, "light"), (False, "dark")])
def test_system_mode_follows_windows(monkeypatch, prefers_light, expected):
    monkeypatch.setattr(theme, "_windows_prefers_light", lambda: prefers_light)
    assert theme.resolve_mode("system") == expected
    assert theme.palette_for("system").mode == expected


def test_system_mode_reads_real_registry_without_raising():
    assert theme.resolve_mode("system") in ("light", "dark")


@pytest.mark.parametrize("palette", [theme.LIGHT, theme.DARK], ids=["light", "dark"])
def test_derive_has_every_key_and_bg_is_the_content_surface(palette):
    c = theme.derive(palette)
    assert set(theme.DERIVED_KEYS) <= set(c)
    assert c["bg"] == palette.surface
    assert c["chrome_bg"] == palette.bg
    assert all(theme.parse_hex(v) for v in c.values())


@pytest.mark.parametrize("palette", [theme.LIGHT, theme.DARK], ids=["light", "dark"])
def test_contrast(palette):
    s = palette.surface
    assert theme.contrast(palette.fg, s) >= 4.5
    assert theme.contrast(palette.accent, s) >= 4.5
    assert theme.contrast(palette.link, s) >= 4.5
    assert theme.contrast(palette.error_accent, s) >= 4.5
    assert theme.contrast(palette.muted, s) >= 3.0
    assert theme.contrast(palette.accent_fg, palette.accent) >= 4.5
    assert theme.contrast(palette.fg, palette.bg) >= 4.5
    c = theme.derive(palette)
    assert theme.contrast(c["llsm_fg"], s) >= 4.5
    assert theme.contrast(c["user_fg"], s) >= 4.5


def test_ref_has_the_panel_reference_keys():
    keys = {"bg", "fg", "muted", "insert", "separator", "user_accent", "llsm_accent",
            "error_accent", "system_accent", "thinking", "running", "idle"}
    for palette in (theme.LIGHT, theme.DARK):
        assert keys <= set(palette.ref())


def test_tint_and_blend():
    assert theme.tint("#000000", "#ffffff", 0.5) == "#7f7f7f"
    assert theme.tint("#fff", "#000", 0.0) == "#ffffff"
    assert theme.tint("SystemButtonFace", "#000000") == theme.LIGHT.muted
