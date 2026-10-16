# FaceFrame live QA report

Run 2026-10-15 20:37:31 → 2026-10-15 20:44:19  
**19 / 19 checks passed**

| Check | Result | Measurements |
|---|---|---|
| stream_acquired_best_rendition | ✅ pass | `{"expected": "1080p60", "got": "1080p60", "video": [1920, 1080], "time_to_first_frame_s": 7.6, "container": "mpegts"}` |
| webcam_face_found | ✅ pass | `{"face": [165, 431, 89, 95], "panel": [0, 350, 511, 284]}` |
| person_auto_selected | ✅ pass | `{"box": [190, 90, 78, 87]}` |
| audio_device_opened | ✅ pass | `{"latency_ms": 22.0, "error": "", "mixer_name": null}` |
| soak_framerate | ✅ pass | `{"median": 29.8, "min": 29.5, "samples": 30}` |
| soak_latency_p95_under_100ms | ✅ pass | `{"worst_p95_ms": 53.6}` |
| soak_no_quality_fallback | ✅ pass | `{"modes": ["enhanced"], "fallback": ""}` |
| soak_memory_stable | ✅ pass | `{"rss_start_mb": 1295, "rss_growth_mb": 33.7, "vram_reserved_mb": 82, "vram_start_mb": 82}` |
| soak_stream_stable | ✅ pass | `{"rebuffers": 1, "late_drops": 0}` |
| soak_tracking | ✅ pass | `{"tracking_fraction": 1.0, "area_valid_fraction": 1.0}` |
| audio_continuous | ✅ pass | `{"served_fraction": 0.993, "resyncs": 1, "underruns": 0}` |
| threads_stable | ✅ pass | `{"start": 96, "end": 93}` |
| output_exact_size | ✅ pass | `{"shape": [720, 1080, 3]}` |
| highest_quality_chain_active | ✅ pass | `{"mode": "enhanced", "fusion": true, "crop": [384, 256], "scale": 2.81, "sr_ms": 12.1}` |
| pause_short_resume | ✅ pass | `{"frames_while_paused": 1, "fps_after": 29.3}` |
| pause_long_rejoins_live | ✅ pass | `{"recovery_s": 10.0}` |
| network_drop_recovers | ✅ pass | `{"recovery_s": 10.8}` |
| stop_start_cycles_no_leak | ✅ pass | `{"rss_mb": [1178, 1178, 1178, 1178], "threads": [101, 86, 85, 85]}` |
| offline_channel_handled | ✅ pass | `{"states": ["STARTING: Resolving https://www.twitch.tv/thischanneldoesnotexist_ff_qa_999 \u2026", "STALLED: Twitch stream unavailable: https://www.twitch.tv/thischanneldoesnotexist_ff_qa_9", "STARTING: Resolving https://www.twitch.tv/thischanneldoesnotexist_ff` |