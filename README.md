# One User, One Agent

This project implements a replayable OTT lifecycle agent. Rules select one measurable goal, a provider may advise among rule-eligible goals, a separate verifier gates publication, and SQLite stores durable event and decision state.

## Current workspace status

The workspace includes source code, a 140,730-event history database, and a saved replay under `sample_run/`. The synthetic input pack is available separately at the provided Downloads path and is passed to the replay script. The original assignment PDF is outside this repository.

## Run

Python 3.10+ and the standard library are sufficient for the runtime. From this directory, pass the provided pack path:

```powershell
$pack = "C:\path\to\one_user_one_agent_pack"
.\run_replay.ps1 -PackPath $pack
```

The replay script ingests source events, replays triggers, runs the supplied 14-day simulator, reconciles goal outcomes, and writes the impact report. The mock provider is deterministic and does not require an API key. `pytest` is required only to run the automated tests:

```powershell
python -m pytest
```

The replay command expects the full provided pack, including catalogue, episodes, profiles, goal and intervention config, pilot files, replay controls, triggers, events, model faults, and simulator. It writes decision/action JSONL, an SQLite run database, latency and impact summaries, outcome judgments, and per-viewer state.

## Output guide

- `sample_run/decisions.jsonl`: full machine-readable decisions and verifier results.
- `sample_run/actions.jsonl`: published home-page changes only.
- `sample_run/goals_ledger.jsonl`: first assigned goal per viewer.
- `sample_run/corrections.jsonl`: corrected goal-rule audit records.
- `sample_run/latency_report.json`: replay latency and reliability counters.
- `sample_run/outcomes.jsonl`: goal success, failure, or right-censored judgments against simulated signals.
- `sample_run/impact_report.md`: historical pilot ITT results and a descriptive practice-seed replay comparison.
- `sample_run/final_report.md`: concise summary of the run and its limitations.
- `sample_run/viewer_state/`: per-viewer decision, action, and outcome summaries.

The replay impact comparison uses one simulator seed and is descriptive; it does not quantify simulator-seed uncertainty. The historical pilot ITT comparison uses the randomized assignment file and all matched outcomes.
