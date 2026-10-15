import pytest

from faceframe.capture.direct import normalize_target


def test_targets():
    assert normalize_target("summit1g") == ("https://www.twitch.tv/summit1g", "summit1g")
    assert normalize_target("https://www.twitch.tv/summit1g?x=1")[1] == "summit1g"
    u, lab = normalize_target("twitch.tv/videos/2901146531?t=1h2m3s")
    assert u == "https://twitch.tv/videos/2901146531?t=1h2m3s" and lab == "videos:2901146531"
    for bad in ("", "a b", "https://www.twitch.tv/"):
        with pytest.raises(ValueError):
            normalize_target(bad)
