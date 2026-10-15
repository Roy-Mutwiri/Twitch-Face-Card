import cv2
import numpy as np

from faceframe.core.temporal import TemporalFuser


def scene(seed=0, w=320, h=180):
    rng = np.random.default_rng(seed)
    return cv2.resize(rng.integers(0, 255, (h // 10, w // 10, 3), dtype=np.uint8), (w, h), interpolation=cv2.INTER_CUBIC)


def test_static_noise_is_reduced():
    base = scene().astype(np.float32)
    rng = np.random.default_rng(1)
    f = TemporalFuser(0.75, 8.0)
    errs_in, errs_out = [], []
    for i in range(30):
        noisy = np.clip(base + rng.normal(0, 4, base.shape), 0, 255).astype(np.uint8)
        out = f.process(noisy)
        if i > 10:
            errs_in.append(np.abs(noisy - base).mean())
            errs_out.append(np.abs(out.astype(np.float32) - base).mean())
    assert np.mean(errs_out) < 0.7 * np.mean(errs_in)


def test_moving_object_not_smeared():
    f = TemporalFuser(0.9, 5.0)
    bg = np.full((180, 320, 3), 60, np.uint8)
    for i in range(20):
        img = bg.copy()
        x = 20 + i * 12                       # square jumps 12 px per frame
        img[60:120, x:x + 40] = 230
        out = f.process(img)
    # the square is at its current position at full brightness, no ghost trail behind it
    assert out[90, x + 20].min() > 200
    assert out[90, x - 30].max() < 90


def test_reset_from_another_thread_never_breaks_process():
    """Regression (live QA): set_panel() resets the fuser on the UI thread while the
    pipeline thread is inside process() -> TypeError on a cleared grid."""
    import threading

    import numpy as np

    from faceframe.core.temporal import TemporalFuser

    fz = TemporalFuser(0.75)
    rng = np.random.default_rng(0)
    frames = [rng.integers(0, 255, (220, 460, 3), np.uint8) for _ in range(4)]
    stop = threading.Event()
    errors = []

    def resetter():
        while not stop.is_set():
            fz.reset()

    t = threading.Thread(target=resetter)
    t.start()
    try:
        for i in range(400):
            try:
                out = fz.process(frames[i % 4])
                assert out.shape == frames[0].shape
            except Exception as e:      # pragma: no cover - the failure being tested
                errors.append(repr(e))
                break
    finally:
        stop.set()
        t.join()
    assert not errors
