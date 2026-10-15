# Scenario: occlusion

- second Edge instance WITHOUT --disable-features=CalculateNativeWinOcclusion at (3307, 300, 1186, 793)
- visible: visibilityState=hidden unique fps 0.0
- fully covered: visibilityState=hidden unique fps 0.0
- partially covered: visibilityState=hidden unique fps 0.0
- uncovered: visibilityState=hidden unique fps 0.0

| t | step | source | track | panel | score | neutral | panel rect | src size | cap fps | uniq fps | message / event |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 4.2 | visible | STALLED | SEARCHING | VALID | 1.0 | False | [237, 238, 356, 238] | (1186, 793) | 0.0 | 0.0 | No new frames for 1.5s / Webcam area confirmed (score 1.00) |
| 4.46 | fully covered | STALLED | SEARCHING | VALID | 1.0 | False | [237, 238, 356, 238] | (1186, 793) | 0.0 | 0.0 | No new frames for 1.5s / Webcam area confirmed (score 1.00) |
| 12.48 | partially covered | STALLED | SEARCHING | VALID | 1.0 | False | [237, 238, 356, 238] | (1186, 793) | 0.0 | 0.0 | No new frames for 1.5s / Webcam area confirmed (score 1.00) |
| 18.49 | uncovered | STALLED | SEARCHING | VALID | 1.0 | False | [237, 238, 356, 238] | (1186, 793) | 0.0 | 0.0 | No new frames for 1.5s / Webcam area confirmed (score 1.00) |