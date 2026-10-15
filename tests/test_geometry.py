import math

import pytest

from faceframe.core.geometry import (FRAMING_PRESETS, Rect, clamp_rect_inside, fit_crop_to_panel, from_normalized,
                                     letterbox, object_fit_mapping, panel_rect_from_normalized_checked, parse_aspect,
                                     snap_crop_to_aspect, snap_crop_to_pixels, target_crop_for_face, to_normalized)


def approx_rect(a: Rect, b: Rect, tol=1e-6):
    return all(abs(x - y) <= tol for x, y in zip(a.as_tuple(), b.as_tuple()))


def test_normalized_roundtrip():
    r = Rect(245, 451, 262, 149)
    n = to_normalized(r, 1586, 993)
    assert approx_rect(from_normalized(n, 1586, 993), r)


def test_normalized_checked_rejects_degenerate():
    assert panel_rect_from_normalized_checked(Rect(0.9, 0.9, 0.2, 0.2), 1000, 1000) is None
    assert panel_rect_from_normalized_checked(Rect(0.0, 0.0, 0.001, 0.5), 1000, 1000) is None
    assert panel_rect_from_normalized_checked(Rect(0.1, 0.1, 0.2, 0.2), 1000, 1000) == Rect(100, 100, 200, 200)


@pytest.mark.parametrize("fit", ["contain", "cover", "fill", "none", "scale-down"])
def test_object_fit_roundtrip(fit):
    m = object_fit_mapping(1920, 1080, 996, 700, fit)
    r = Rect(10, 400, 350, 190)
    assert approx_rect(m.container_to_content(m.content_to_container(r)), r)


def test_object_fit_contain_letterbox_offsets():
    # 16:9 video in a 4:3 box -> bars top/bottom
    m = object_fit_mapping(1920, 1080, 800, 600, "contain")
    assert m.scale_x == pytest.approx(800 / 1920)
    assert m.offset_x == pytest.approx(0)
    assert m.offset_y == pytest.approx((600 - 1080 * 800 / 1920) / 2)
    # the top-left of the visible video maps to video (0,0)
    v = m.container_to_content(Rect(0, m.offset_y, 1, 1))
    assert v.x == pytest.approx(0) and v.y == pytest.approx(0)


def test_object_fit_cover_crops_and_position():
    m = object_fit_mapping(1920, 1080, 600, 600, "cover", pos_x=0.0)
    assert m.scale_x == pytest.approx(600 / 1080)
    assert m.offset_x == pytest.approx(0)  # left aligned
    m2 = object_fit_mapping(1920, 1080, 600, 600, "cover")
    assert m2.offset_x == pytest.approx((600 - 1920 * 600 / 1080) / 2)


def test_dpi_mapping_is_scale_invariant():
    # same layout at 100% and 150% DPI (physical px) -> same normalized panel
    p100 = Rect(245, 451, 262, 149)
    p150 = p100.scaled(1.5)
    assert approx_rect(to_normalized(p100, 1586, 993), to_normalized(p150, 1586 * 1.5, 993 * 1.5))


def test_letterbox_fit():
    s, x, y, w, h = letterbox(400, 300, 1280, 720)
    assert (w, h) == (960, 720) and (x, y) == (160, 0)
    s, x, y, w, h = letterbox(1920, 1080, 1280, 720)
    assert (w, h) == (1280, 720) and (x, y) == (0, 0)


def test_clamp_inside_shifts_then_shrinks():
    r = clamp_rect_inside(Rect(-10, -5, 50, 40), 100, 100)
    assert r == Rect(0, 0, 50, 40)
    big = clamp_rect_inside(Rect(0, 0, 400, 200), 100, 100)
    assert big.w <= 100 and big.h <= 100
    assert big.w / big.h == pytest.approx(2.0)


def test_snap_crop_to_pixels_bounds():
    for r in (Rect(-3.2, 5.7, 50.1, 40.9), Rect(90, 90, 30, 30), Rect(0, 0, 0.2, 0.2)):
        x, y, w, h = snap_crop_to_pixels(r, 100, 100)
        assert 0 <= x and 0 <= y and w >= 1 and h >= 1 and x + w <= 100 and y + h <= 100


@pytest.mark.parametrize("aspect", [16 / 9, 3 / 2, 1.0, 9 / 16])
def test_snap_crop_to_aspect(aspect):
    for r in (Rect(10.3, 20.7, 120.2, 70.1), Rect(0, 0, 500, 500), Rect(200, 100, 64, 64)):
        x, y, w, h = snap_crop_to_aspect(r, aspect, 262, 149)
        assert 0 <= x and 0 <= y and x + w <= 262 and y + h <= 149
        assert abs(w - h * aspect) <= 1.0 or w == 262  # within integer rounding unless width-limited


@pytest.mark.parametrize("framing", list(FRAMING_PRESETS))
@pytest.mark.parametrize("aspect", [16 / 9, 3 / 2, 1.0, 9 / 16])
def test_target_crop_aspect_and_bounds(framing, aspect):
    face = Rect(100, 20, 40, 48)
    c = target_crop_for_face(face, aspect, framing, 350, 190, fill=True)
    assert c.x >= -1e-9 and c.y >= -1e-9 and c.x2 <= 350 + 1e-9 and c.y2 <= 190 + 1e-9
    assert c.w / c.h == pytest.approx(aspect, rel=1e-6)
    # the face stays inside the crop
    assert c.x <= face.x + 1e-6 and c.x2 >= face.x2 - 1e-6


def test_target_crop_headroom():
    face = Rect(400, 300, 100, 120)
    c = target_crop_for_face(face, 16 / 9, "head_shoulders", 2000, 2000)
    # face centre sits above the crop centre (headroom + shoulders)
    assert face.cy < c.cy
    assert c.y < face.y  # space above the head


def test_fit_mode_keeps_whole_dims_without_stretch():
    c = fit_crop_to_panel(Rect(-50, -50, 600, 300), 350, 190, fill=False)
    assert c == Rect(0, 0, 350, 190)


def test_parse_aspect():
    assert parse_aspect("16:9") == pytest.approx(16 / 9)
    assert parse_aspect("1.5") == 1.5
