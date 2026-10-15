import math

import pytest

from faceframe.core.geometry import Rect
from faceframe.core.tracking import (CropSmoother, Detection, DetectionResult, FaceTracker, SmootherParams,
                                     TrackerConfig, TrackState)


def res(seq, dets, sid=1, t=None):
    return DetectionResult(sid, seq, (t if t is not None else seq) * 1_000_000, (350, 190),
                           [Detection(b, s) for b, s in dets])


def make_tracker(**kw):
    t = FaceTracker(TrackerConfig(**kw))
    t.reset(1)
    return t


def test_select_and_track_continuity():
    t = make_tracker()
    t.update(res(1, [(Rect(100, 20, 40, 48), 0.9), (Rect(250, 30, 20, 24), 0.8)]), 0.0)
    assert t.state == TrackState.SEARCHING
    assert t.select_at(120, 40, 0.0) is not None
    assert t.state == TrackState.TRACKING
    # small motion: stays on the same face
    t.update(res(2, [(Rect(106, 22, 40, 48), 0.9), (Rect(250, 30, 20, 24), 0.8)]), 0.1)
    assert t.target.cx == pytest.approx(126, abs=4)


def test_never_switches_to_other_face_when_target_disappears():
    t = make_tracker(lost_after_s=0.3, grace_s=1.0)
    t.update(res(1, [(Rect(100, 20, 40, 48), 0.9)]), 0.0)
    t.select_at(120, 40, 0.0)
    # target vanishes; a different face (game character / second person) far away
    for i in range(2, 40):
        t.update(res(i, [(Rect(280, 120, 40, 48), 0.95)]), i * 0.1)
    assert t.state == TrackState.LOST
    assert t.target.cx < 200          # still the old position, never jumped to x=300
    assert t.switch_guard_rejections > 0


def test_lost_grace_then_panel_then_reacquire_near_last_position():
    t = make_tracker(lost_after_s=0.3, grace_s=1.0, after_grace="panel")
    t.update(res(1, [(Rect(100, 20, 40, 48), 0.9)]), 0.0)
    t.select_at(120, 40, 0.0)
    t.update(res(2, []), 0.5)
    d = t.decision(0.6)
    assert d.state == TrackState.LOST and d.mode == "hold"
    d = t.decision(2.0)
    assert d.mode == "panel"
    # comes back slightly displaced -> reacquired
    t.update(res(3, [(Rect(130, 25, 40, 48), 0.9)]), 2.1)
    assert t.state == TrackState.TRACKING


def test_stale_and_foreign_session_results_rejected():
    t = make_tracker()
    assert t.update(res(5, []), 0.0)
    assert not t.update(res(4, []), 0.1)            # older frame
    assert not t.update(res(6, [], sid=99), 0.1)    # other session (source changed)
    assert t.stale_rejected == 2


def test_source_unavailable_state_and_recovery():
    t = make_tracker()
    t.update(res(1, [(Rect(100, 20, 40, 48), 0.9)]), 0.0)
    t.select_at(120, 40, 0.0)
    t.set_source_available(False, 1.0)
    assert t.decision(1.0).mode == "neutral"
    t.set_source_available(True, 2.0)
    assert t.state == TrackState.LOST   # must re-confirm the person, not assume
    t.update(res(2, [(Rect(102, 20, 40, 48), 0.9)]), 2.1)
    assert t.state == TrackState.TRACKING


def test_single_clear_candidate_needs_consistency():
    t = make_tracker()
    for i in range(1, 15):
        t.update(res(i, [(Rect(100, 20, 40, 48), 0.9)]), i * 0.1)
    assert t.single_clear_candidate() is None
    t.update(res(15, [(Rect(100, 20, 40, 48), 0.9)]), 1.5)
    assert t.single_clear_candidate() is not None
    t.update(res(16, [(Rect(100, 20, 40, 48), 0.9), (Rect(250, 20, 40, 48), 0.9)]), 1.6)
    assert t.single_clear_candidate() is None   # two similar faces: ask the user


def test_dominant_streamer_preselected_despite_poster():
    t = make_tracker()
    for i in range(1, 20):
        t.update(res(i, [(Rect(100, 20, 50, 60), 0.9), (Rect(280, 30, 14, 16), 0.8)]), i * 0.1)
    c = t.single_clear_candidate()
    assert c is not None and c.box.x == 100


def test_poster_face_never_auto_preselected():
    """Streamer (big face) and poster (small face) both visible; the streamer
    turns away so only the poster is detected for a while -> not preselected."""
    t = make_tracker()
    for i in range(1, 11):
        t.update(res(i, [(Rect(100, 20, 50, 60), 0.9), (Rect(280, 30, 14, 16), 0.8)]), i * 0.1)
    for i in range(11, 40):
        t.update(res(i, [(Rect(280, 30, 14, 16), 0.8)]), i * 0.1)
    assert t.single_clear_candidate() is None
    # a lone small face in a fresh session is also not preselected (12% of panel height rule)
    t2 = make_tracker()
    for i in range(1, 30):
        t2.update(res(i, [(Rect(280, 30, 14, 16), 0.8)]), i * 0.1)
    assert t2.single_clear_candidate() is None


def test_smoother_dead_zone_and_speed_limit():
    sm = CropSmoother(SmootherParams(smoothness=0.5))
    sm.snap(Rect(0, 0, 160, 90))
    # tiny jitter inside the dead zone: no movement
    for i in range(30):
        r = sm.update(Rect(1.0 if i % 2 else -1.0, 0.5, 160, 90), 1 / 30)
    assert r.x == pytest.approx(0) and r.y == pytest.approx(0)
    # a big jump is followed with bounded speed (no teleport)
    r = sm.update(Rect(300, 0, 160, 90), 1 / 30)
    step = r.cx - 80
    assert 0 < step <= sm.params.speed * 90 / 30 + 1e-6


def test_smoother_converges_and_time_based():
    a = CropSmoother(SmootherParams(smoothness=0.3))
    b = CropSmoother(SmootherParams(smoothness=0.3))
    a.snap(Rect(0, 0, 160, 90))
    b.snap(Rect(0, 0, 160, 90))
    tgt = Rect(40, 10, 160, 90)
    for _ in range(60):
        a.update(tgt, 1 / 60)
    for _ in range(30):
        b.update(tgt, 1 / 30)
    # same elapsed time -> similar position regardless of frame rate
    assert a.current().x == pytest.approx(b.current().x, abs=3.0)
    for _ in range(300):
        a.update(tgt, 1 / 60)
    assert a.current().x == pytest.approx(40, abs=2.0)


def test_smoother_slow_zoom():
    sm = CropSmoother(SmootherParams(smoothness=0.5))
    sm.snap(Rect(0, 0, 160, 90))
    r = sm.update(Rect(-80, -45, 320, 180), 1 / 30)
    # log-size change per frame bounded by max_zoom_rate
    assert math.log(r.h / 90) <= sm.params.max_zoom_rate / 30 + 1e-9


def test_smoother_stall_does_not_jump():
    sm = CropSmoother(SmootherParams(smoothness=0.5))
    sm.snap(Rect(0, 0, 160, 90))
    r = sm.update(Rect(500, 0, 160, 90), 10.0)  # 10 s stall clamped to 0.25 s
    assert r.cx - 80 <= sm.params.speed * 90 * 0.25 + 1e-6
