# Known limitations

- The synthetic pack is outside the submission directory. `run_replay.ps1` accepts its path; include the pack or make it available at grading time.
- The historical pilot interval is an unadjusted normal approximation, not a randomization-inference or regression-adjusted interval.
- The replay impact comparison uses one simulator seed and policy-selected first goals; it is descriptive and does not quantify simulator-seed uncertainty.
- Home-page interventions are implemented; push, WhatsApp, and in-app messaging are not.
- The single-process replay is suitable for the provided synthetic workload only. It is not a distributed 10-million-viewer service, and its measured latency does not establish production capacity.
- The runtime creates per-viewer snapshots for viewers present in the current replay, but does not expose an interactive audit API.
