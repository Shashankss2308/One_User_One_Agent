# Architecture and trust boundaries

## Ownership and planning

`Runtime` is the replay orchestrator. Each decision is keyed by a trigger event ID in SQLite, and the goals ledger retains one initial assignment per viewer. `Planner` derives candidate goals from event history and the latest profile snapshot, applies a stable priority order, then builds a dialect-specific page. A live eligible goal is kept active while its deadline remains open. Controls update withdrawal and goal-rule state.

## Evidence and memory

`ingest.py` stores events in SQLite with a normalized best-time estimate, server receipt time, device clock offset, and buffering metadata. Event IDs are unique, so repeated deliveries do not create duplicate history. `facts.py` attaches source, observation time, age, confidence, and conflicts to facts. Subscription events newer than the profile snapshot supersede the snapshot; the current app setting is authoritative for the page dialect while profile and deeplink disagreements remain visible.

SQLite is durable event and decision memory. JSONL outputs provide inspectable decisions, actions, goal assignments, and outcome judgments; viewer-state JSON summarizes each viewer's replay. `outcomes.py` checks the captured goal success rule against simulator events, marks signals seen before the deadline as successes, complete windows with no signal as failures, and windows beyond the simulation horizon as inconclusive.

## Model and tools

The model is an advisor only. It can choose among goals already marked eligible by deterministic rules. Its output cannot establish eligibility or override the independent verifier. The mock provider replays configured faults without external credentials. Tool/model failure falls back to the rule-selected goal; load shedding skips the advisor.

## Verification and trust boundaries

`Verifier` independently reloads raw catalogue, arm, and profile files and checks the arm, freshness, dialect, content availability window, duplicate slugs, row shape, instruction-like catalogue text, and selected goal constraints. A failed check converts a proposed page to `NO_ACTION`. Catalogue text is untrusted data and is never treated as an instruction. Only the supplied synthetic pack may provide viewer or title facts.

Messaging is not implemented in the current runtime; therefore it cannot violate message consent, quiet-hour, template, or frequency policies by sending messages. Those channels need their own independent policy checks before implementation.

## Latency and recovery

Decisions are idempotent by trigger ID. The runtime bounds the model to one advisory call per eligible decision (below the eight-call limit), skips it under load shedding, records exceptions, and fails closed on planner/verifier errors. During the injected user-data-store degradation window, it records no-action decisions instead of planning. A title withdrawal triggers repair decisions for active pages that included that content. The fresh replay reports p50 4.0 ms, p95 12.7 ms, p99 22.2 ms, max 63.7 ms, and zero decisions over 60 seconds; these are measurements of the saved environment, not a production guarantee.

## Measurement

The pilot comparison uses randomized assignment (intent to treat), retaining all matched assigned viewers in their arm whether or not they were served. Served-versus-unserved is selection-biased. `impact_report.py` reports the historical pilot ITT difference with a simple normal-approximation interval and a separate single-seed simulator comparison. The latter is descriptive because the model can change the selected goal by arm and one seed does not capture simulation uncertainty.
