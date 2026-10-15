import threading
import time

from faceframe.core.queues import LatestSlot


def test_latest_slot_capacity_one_and_drop_count():
    s = LatestSlot()
    for i in range(10):
        s.put(i)
    item, v = s.get_newer(0, timeout=0)
    assert item == 9
    assert s.dropped == 9
    assert s.pending == 0


def test_consumer_never_sees_old_item_twice():
    s = LatestSlot()
    s.put("a")
    item, v = s.get_newer(0, timeout=0)
    item2, v2 = s.get_newer(v, timeout=0.05)
    assert item2 is None and v2 == v


def test_clear_flushes_pending_on_source_change():
    s = LatestSlot()
    s.put("old-source-frame")
    s.clear()
    item, _ = s.peek()
    assert item is None
    s.put("new")
    item, _ = s.get_newer(0, timeout=0)
    assert item == "new"


def test_slow_consumer_bounded_latency():
    """Producer at 200 Hz, consumer takes 50 ms: the consumer always gets a recent
    item; nothing queues up (age of consumed item stays < one consumer period)."""
    s = LatestSlot()
    stop = threading.Event()

    def prod():
        while not stop.is_set():
            s.put(time.perf_counter())
            time.sleep(0.005)

    th = threading.Thread(target=prod)
    th.start()
    ages = []
    seen = 0
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < 1.0:
        item, seen = s.get_newer(seen, timeout=0.5)
        ages.append(time.perf_counter() - item)
        time.sleep(0.05)
    stop.set()
    th.join()
    assert max(ages) < 0.03
    assert s.dropped > 50


def test_close_wakes_waiters():
    s = LatestSlot()
    out = []
    th = threading.Thread(target=lambda: out.append(s.get_newer(0, timeout=5)))
    th.start()
    time.sleep(0.05)
    s.close()
    th.join(1)
    assert out and out[0][0] is None
