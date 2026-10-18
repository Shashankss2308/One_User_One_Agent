# Replay submission report

## What is implemented

The source implements a per-viewer goal planner, evidence facts with source and freshness, idempotent event ingestion, dialect-aware catalogue selection, a mock model advisor, independent verification, safe no-action decisions, model failure fallback, load shedding, title-withdrawal repair, outcome reconciliation, and auditable JSONL/SQLite outputs.

## Saved replay evidence

The fresh replay records 2,689 trigger decisions: 1,063 `HOME_PAGE` and 1,626 `NO_ACTION`. Latency was p50 4.0 ms, p95 12.7 ms, p99 22.2 ms, max 63.7 ms, and zero decisions exceeded 60 seconds. It ignored 90 duplicate triggers and 7 duplicate live events, load-shed 186 decisions, failed closed on 16 triggers during data-store degradation, fell back after 11 model failures, rejected 2 model claims, and repaired 10 viewers after title withdrawal. The simulator processed all 1,063 actions with zero policy problems and generated 31,578 events; it showed 820 personalized pages, while 15 published pages were not shown due to an app-dialect mismatch.

## Outcome and impact status

The simulator produced 1,698 first-goal judgments: 186 succeeded and 1,512 failed; none were right-censored in this run. In the historical randomized pilot, treatment success was 18.3% (349/1,912) and holdout success was 19.6% (387/1,979), an ITT risk difference of -1.3 percentage points (95% normal-approximation CI -3.8 to +1.2). The reported 40.1% served versus 17.7% everyone-else comparison is selection-biased and is not causal. The single-seed replay comparison is descriptive because the model can change the selected goal by arm.

## Before submission

The one-command replay is documented in `README.md`. Remaining limitations are listed in `KNOWN_LIMITATIONS.md`.
