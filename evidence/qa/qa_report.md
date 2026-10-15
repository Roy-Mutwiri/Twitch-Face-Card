# FaceFrame live QA report

Run 2026-10-15 18:06:56 → 2026-10-15 18:18:46  
**18 / 19 checks passed**

| Check | Result | Measurements |
|---|---|---|
| stream_acquired_best_rendition | ✅ pass | `{"expected": "1080p60", "got": "1080p60", "video": [1920, 1080], "time_to_first_frame_s": 7.1, "container": "mpegts"}` |
| webcam_face_found | ✅ pass | `{"face": [214, 927, 68, 85], "panel": [18, 855, 460, 225]}` |
| person_auto_selected | ✅ pass | `{"box": [197, 73, 70, 83]}` |
| audio_device_opened | ✅ pass | `{"latency_ms": 22.0, "error": "", "mixer_name": null}` |
| soak_framerate | ❌ FAIL | `{"median": 29.7, "min": 21.7, "samples": 60}` |
| soak_latency_p95_under_100ms | ✅ pass | `{"worst_p95_ms": 87.8}` |
| soak_no_quality_fallback | ✅ pass | `{"modes": ["enhanced"], "fallback": ""}` |
| soak_memory_stable | ✅ pass | `{"rss_start_mb": 1299, "rss_growth_mb": 9.4, "vram_reserved_mb": 66, "vram_start_mb": 66}` |
| soak_stream_stable | ✅ pass | `{"rebuffers": 0, "late_drops": 0}` |
| soak_tracking | ✅ pass | `{"tracking_fraction": 1.0, "area_valid_fraction": 1.0}` |
| audio_continuous | ✅ pass | `{"served_fraction": 1.0, "resyncs": 0, "underruns": 0}` |
| threads_stable | ✅ pass | `{"start": 98, "end": 92}` |
| output_exact_size | ✅ pass | `{"shape": [720, 1080, 3]}` |
| highest_quality_chain_active | ✅ pass | `{"mode": "enhanced", "fusion": true, "crop": [321, 214], "scale": 3.36, "sr_ms": 11.5}` |
| pause_short_resume | ✅ pass | `{"frames_while_paused": 2, "fps_after": 29.7}` |
| pause_long_rejoins_live | ✅ pass | `{"recovery_s": 10.5}` |
| network_drop_recovers | ✅ pass | `{"recovery_s": 11.3}` |
| stop_start_cycles_no_leak | ✅ pass | `{"rss_mb": [1177, 1177, 1177, 1178], "threads": [100, 85, 85, 86]}` |
| offline_channel_handled | ✅ pass | `{"states": ["STARTING: Resolving https://www.twitch.tv/thischanneldoesnotexist_ff_qa_999 \u2026", "STALLED: Twitch stream unavailable: https://www.twitch.tv/thischanneldoesnotexist_ff_qa_9", "STARTING: Resolving https://www.twitch.tv/thischanneldoesnotexist_ff` |