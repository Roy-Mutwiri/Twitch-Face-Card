import numpy as np
import pytest

from faceframe.core.enhance import QualityMode, QualitySettings, Renderer, unsharp
from faceframe.core.geometry import Rect
from faceframe.core.panel_validate import PanelValidator, PanelValidity


def textured(h, w, seed=0):
    rng = np.random.default_rng(seed)
    base = rng.integers(0, 255, (h // 8 + 1, w // 8 + 1, 3), dtype=np.uint8)
    import cv2
    return cv2.resize(base, (w, h), interpolation=cv2.INTER_CUBIC)


@pytest.mark.parametrize("mode", list(QualityMode))
def test_render_output_dims_exact(mode):
    r = Renderer()
    crop = textured(108, 192)
    out, info = r.render(crop, 1280, 720, QualitySettings(mode=mode), fill=True)
    assert out.shape == (720, 1280, 3)
    if mode == QualityMode.ENHANCED:
        assert info.mode_used == QualityMode.FAST and "not loaded" in info.reason  # honest fallback


def test_original_mode_is_1to1():
    r = Renderer()
    crop = textured(100, 150)
    out, info = r.render(crop, 640, 360, QualitySettings(mode=QualityMode.ORIGINAL))
    assert info.drawn_size == (150, 100) and info.scale == 1.0
    y, x = (360 - 100) // 2, (640 - 150) // 2
    assert np.array_equal(out[y:y + 100, x:x + 150], crop)


def test_fit_never_stretches():
    r = Renderer()
    out, info = r.render(textured(300, 300), 1280, 720, QualitySettings(), fill=False)
    assert info.drawn_size == (720, 720)


def test_small_crop_warning_and_no_fake_native_label():
    r = Renderer()
    out, info = r.render(textured(90, 160), 1280, 720, QualitySettings(), fill=True)
    assert "cannot recover" in info.warning
    assert "native" not in info.warning.lower()


def test_unsharp_flat_region_untouched():
    flat = np.full((50, 50, 3), 120, np.uint8)
    assert np.array_equal(unsharp(flat, 0.5, 1.0), flat)


class BrokenEngine:
    ready = True

    def run(self, img):
        raise RuntimeError("CUDA error: out of memory")


def test_enhanced_failure_falls_back_without_crash():
    r = Renderer(BrokenEngine())
    out, info = r.render(textured(108, 192), 1280, 720, QualitySettings(mode=QualityMode.ENHANCED), fill=True)
    assert out.shape == (720, 1280, 3)
    assert info.mode_used == QualityMode.FAST and r.forced_fast and "out of memory" in info.reason


class SlowEngine:
    ready = True

    def run(self, img):
        import time
        time.sleep(0.06)
        return np.repeat(np.repeat(img, 2, 0), 2, 1)


def test_latency_guard_switches_to_fast():
    r = Renderer(SlowEngine())
    q = QualitySettings(mode=QualityMode.ENHANCED, sr_budget_ms=20)
    import time
    t0 = time.time()
    while time.time() - t0 < 3 and not r.forced_fast:
        r.render(textured(54, 96), 640, 360, q, fill=True)
    assert r.forced_fast and "too slow" in r.fallback_reason


def scene(offset=(0, 0), seed=1):
    img = textured(360, 640, seed)
    return np.roll(img, offset, axis=(0, 1))


def test_validator_follows_scroll_and_detects_change():
    v = PanelValidator()
    panel = Rect(100, 150, 160, 90)
    s0 = scene()
    v.set_reference(s0, 1.0, panel, (640, 360))
    st, p, score = v.check(s0, 1.0, panel, (640, 360))
    assert st == PanelValidity.VALID and score > 0.95 and p == panel
    st, p, score = v.check(scene((6, -4)), 1.0, panel, (640, 360))   # page scrolled a bit
    assert st == PanelValidity.VALID and (p.x, p.y) == (96, 156)
    other = textured(360, 640, seed=99)                                # completely different content
    for _ in range(3):
        st, p2, score = v.check(other, 1.0, p, (640, 360))
    assert st == PanelValidity.INVALID
    st, _, _ = v.check(scene((6, -4)), 1.0, p, (640, 360))            # original content returns
    st, _, _ = v.check(scene((6, -4)), 1.0, p, (640, 360))
    assert st == PanelValidity.VALID


def test_validator_relocates_after_uniform_resize():
    import cv2
    v = PanelValidator()
    s0 = scene(seed=3)
    panel = Rect(200, 100, 160, 90)
    v.set_reference(s0, 1.0, panel, (640, 360))
    big = cv2.resize(s0, (800, 450), interpolation=cv2.INTER_LINEAR)  # window grew 1.25x
    st, p, score = v.check(big, 1.0, panel, (800, 450))
    assert st == PanelValidity.CHECKING and p == panel   # one match is not enough
    st, p, score = v.check(big, 1.0, panel, (800, 450))  # second, agreeing overview
    assert st == PanelValidity.VALID, score
    assert abs(p.x - 250) <= 3 and abs(p.y - 125) <= 3 and abs(p.w - 200) <= 4


def test_validator_refuses_to_guess_after_resize_to_unrelated_layout():
    v = PanelValidator()
    v.set_reference(scene(seed=3), 1.0, Rect(200, 100, 160, 90), (640, 360))
    for _ in range(3):
        st, p, score = v.check(textured(450, 800, seed=50), 1.0, Rect(200, 100, 160, 90), (800, 450))
        assert st != PanelValidity.VALID
    assert st == PanelValidity.INVALID


def test_validator_follows_large_scroll_after_confirmation():
    v = PanelValidator()
    panel = Rect(100, 150, 160, 90)
    v.set_reference(scene(), 1.0, panel, (640, 360))
    moved = scene((-110, 0))                       # scrolled 110 px: outside the local search window
    st, p, _ = v.check(moved, 1.0, panel, (640, 360))
    assert st == PanelValidity.CHECKING and p == panel
    st, p, _ = v.check(moved, 1.0, panel, (640, 360))
    assert st == PanelValidity.VALID and (p.x, p.y) == (100, 40)


def test_person_leaving_keeps_panel_valid_but_ad_does_not():
    v = PanelValidator()
    panel = Rect(100, 150, 160, 90)
    s0 = scene()
    v.set_reference(s0, 1.0, panel, (640, 360))
    empty = s0.copy()
    empty[150 + 20:150 + 70, 100 + 35:100 + 125] = 40     # centre (person) replaced: empty chair
    for _ in range(4):
        st, _, score = v.check(empty, 1.0, panel, (640, 360))
    assert st == PanelValidity.VALID, score
    ad = s0.copy()
    ad[150:240, 100:260] = textured(90, 160, seed=77)   # whole panel replaced (ad / scene)
    for _ in range(3):
        st, _, _ = v.check(ad, 1.0, panel, (640, 360))
    assert st == PanelValidity.INVALID
