# Evaluation (benchmark v2)

Run records for the journal version are in `data/eval/v2-*` (one JSON line per run, with the final answer, the models
that served it and the submissions the fixture site received). `v2-discarded/` keeps the first round, run before the
fix that stops a provider refusal from being treated as an agent error.

    .venv/Scripts/python evaluation/analysis_v2.py      # every number and figure (writes results_v2.json, numbers.tex, figures/)
    .venv/Scripts/python evaluation/failure_audit.py v2-fixture v2-cmp-ease   # failed answers with their step logs
    python evaluation/relabel_provider_stops.py v2-cmp-bu v2-cmp-bu100        # see below

`failure_causes.json` classifies every failed Ease run; `live_audit.json` checks the live answers against their
sources. Browser-use runs in `v2-cmp-bu*` that carry a `relabelled` field were first scored as wrong answers although
the provider had stopped them (HTTP 503); a mis-escaped pattern in the wrapper missed 503. `relabel_provider_stops.py`
applies the wrapper's rule to each run's logged step errors and marks a failed run RATE_LIMITED when the provider
ended it (five consecutive failed steps, every one a provider refusal, after which browser-use stops); they were then
re-run. A refusal the agent recovered from does not count, for any system.

`v2-sens-cmp-ease` and `v2-sens-fixture` are a sensitivity check: the five failed Ease runs (three comparison runs,
two fixture F06 runs) that logged refused calls the agent recovered from, re-run with the same configuration. They
stand under the rule above; `analysis_v2.py` reports the results with the re-runs in their place as well.

To run the benchmark again, see `backend/scripts/run_paper_v2.sh`.
