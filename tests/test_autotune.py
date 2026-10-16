"""Automatic performance: tier choice from specs, governor step-down/up rules, VOD seek helpers."""

from faceframe.capture.direct import _hms_param, _height, parse_hms
from faceframe.core import autotune as A


def spec(**kw):
    d = dict(cpu="x", cores=4, threads=8, ram_gb=16, ms_balanced=10, ms_light=2, ms_detect=6)
    d.update(kw)
    return A.SystemSpec(**d)


def name(i):
    return A.TIERS[i].name


def test_choose_tier():
    assert name(A.choose_tier(spec(cuda=True, gpu="RTX", vram_gb=16, gpu_sms=36))) == "Ultra"
    assert name(A.choose_tier(spec(cuda=True, gpu="GTX 1650", vram_gb=4, gpu_sms=14))) == "High"
    assert name(A.choose_tier(spec())) == "Balanced"                                   # strong CPU, no GPU
    assert name(A.choose_tier(spec(threads=4, ms_balanced=17, ms_light=1.4, ms_detect=6.3))) == "Light"
    assert name(A.choose_tier(spec(threads=2, ms_light=1.2, ms_detect=11))) == "Light"   # measured: 29 fps
    assert name(A.choose_tier(spec(threads=2, ms_light=8, ms_detect=30))) == "Saver"
    assert name(A.choose_tier(spec(threads=1, ms_light=2, ms_detect=10))) == "Saver"     # measured: 29 fps
    assert name(A.choose_tier(spec(threads=2, ms_light=25, ms_detect=60))) == "Minimal"
    assert A.ceiling_tier(spec(cuda=True)) == 0 and A.ceiling_tier(spec()) == 2


def m(t, fps=30.0, exp=30.0, late=0, rebuf=0, cpu=40.0, lat=40.0, enh_req=False, enh_run=False, active=True):
    return A.Metrics(t, exp, fps, late, rebuf, cpu, lat, enh_req, enh_run, active)


def test_governor_steps_down_on_sustained_slow_output_only():
    g = A.Governor(3, A.CPU_PATH)
    assert g.update(m(0)) is None
    assert g.update(m(2, fps=20)) is None             # one bad sample: wait
    assert g.update(m(4, fps=30)) is None             # recovered
    assert g.update(m(6, fps=20)) is None
    assert g.update(m(8, fps=20)) == 4                # two in a row -> Saver (720p: stream reconnects)
    t, r = 8, None
    while r is None:
        t += 2
        r = g.update(m(t, fps=20))
    assert r == 5 and t >= 28                         # 20 s grace after a resolution change
    assert g.update(m(16, fps=5)) is None and g.update(m(30, fps=5)) is None   # already at the bottom


def test_network_hiccups_do_not_lower_quality():
    g = A.Governor(2, A.CPU_PATH)
    g.update(m(0))
    for i, t in enumerate(range(2, 20, 2)):
        assert g.update(m(t, rebuf=i + 1, late=5 * (i + 1), cpu=30)) is None   # decoder late but CPU idle


def test_decoder_overload_lowers_quality():
    g = A.Governor(3, A.CPU_PATH)
    g.update(m(0, cpu=95))
    g.update(m(2, late=10, cpu=95))
    assert g.update(m(4, late=20, cpu=95)) == 4       # -> Saver (720p cap)


def test_gpu_fallback_steps_down_at_once():
    g = A.Governor(0, A.GPU_PATH)
    g.update(m(0, enh_req=True, enh_run=True))
    assert g.update(m(10, enh_req=True, enh_run=False)) == 1


def test_inactive_pipeline_is_not_judged_on_frame_rate():
    g = A.Governor(2, A.CPU_PATH)
    g.update(m(0, active=False, fps=0))
    for t in range(2, 30, 2):
        assert g.update(m(t, active=False, fps=0)) is None


def test_steps_up_with_headroom_and_blocks_after_a_failed_step_up():
    g = A.Governor(4, A.CPU_PATH)
    t = 0
    g.update(m(t))
    up = None
    while up is None and t < 200:
        t += 2
        up = g.update(m(t, cpu=30))
    assert up == 3 and 44 <= t <= 50                  # ~45 s healthy
    for _ in range(15):                               # the higher level doesn't hold
        t += 2
        g.update(m(t, fps=18, cpu=90))
    assert g.tier == 4
    for _ in range(150):                              # 5 healthy minutes: still blocked (10 min)
        t += 2
        assert g.update(m(t, cpu=20)) is None


def test_never_above_ceiling():
    g = A.Governor(2, A.CPU_PATH)
    for t in range(0, 400, 2):
        assert g.update(m(t, cpu=10)) is None


def test_vod_helpers():
    assert parse_hms("1:02:03") == 3723 and parse_hms("62:03") == 3723 and parse_hms("1h2m3s") == 3723
    assert parse_hms("45") == 45 and parse_hms("2m") == 120 and parse_hms("abc") is None and parse_hms("") is None
    assert _hms_param(3723.9) == "1h2m3s"
    assert _height("1080p60") == 1080 and _height("audio_only") == 0


def test_spec_cache_roundtrip():
    s = spec(cuda=True, gpu="RTX", vram_gb=8)
    d = A.spec_to_dict(s)
    s2 = A.spec_from_dict(d)
    assert s2 == s and s.same_hardware(s2)
    assert A.spec_from_dict({"cpu": "x", "unknown_key": 1}).cpu == "x"


def test_gpu_path_skips_cpu_heavy_balanced():
    g = A.Governor(1, A.GPU_PATH)                     # High
    g.update(m(0))
    g.update(m(2, fps=18, cpu=95))
    assert name(g.update(m(4, fps=18, cpu=95))) == "Light"
    assert A.Governor(2, A.GPU_PATH).tier == 3        # off-path start snaps to the next lighter level


def test_saturated_cpu_steps_down_even_if_frames_keep_up():
    g = A.Governor(3, A.CPU_PATH)
    g.update(m(0, cpu=99))
    assert g.update(m(2, cpu=99)) is None
    assert g.update(m(4, cpu=99)) is None
    assert g.update(m(6, cpu=99)) is None              # 3 hot samples -> bad #1
    assert name(g.update(m(8, cpu=99))) == "Saver"     # bad #2 -> down


def test_step_up_needs_low_average_cpu_not_one_low_sample():
    g = A.Governor(3, A.CPU_PATH)
    g.update(m(0))
    for t in range(2, 200, 2):                          # mostly 80 % with an occasional 40 %
        assert g.update(m(t, cpu=40 if t % 20 == 0 else 80)) is None


def test_other_programs_load_does_not_push_quality_down():
    g = A.Governor(3, A.CPU_PATH)
    g.update(m(0))
    for t in range(2, 60, 2):                          # CPU saturated by someone else, frames on time
        assert A.Metrics is not None
        assert g.update(A.Metrics(t, 30, 30, 0, 0, 99, 40, False, False, True, own_pct=15)) is None


def test_saturation_alone_stops_at_saver():
    g = A.Governor(4, A.CPU_PATH)                      # already Saver
    g.update(m(0))
    for t in range(2, 60, 2):
        assert g.update(A.Metrics(t, 30, 30, 0, 0, 99, 40, False, False, True, own_pct=60)) is None
