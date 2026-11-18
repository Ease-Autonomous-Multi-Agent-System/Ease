# Evaluation (benchmark v2)

Run records for the journal version are in `data/eval/v2-*` (one JSON line per run, with the final answer, the models
that served it and the submissions the fixture site received). `v2-discarded/` keeps the first round, run before the
fix that stops a provider refusal from being treated as an agent error.

    .venv/Scripts/python evaluation/analysis_v2.py      # every number and figure (writes results_v2.json, numbers.tex, figures/)
    .venv/Scripts/python evaluation/failure_audit.py v2-fixture v2-cmp-ease   # failed answers with their step logs

To run the benchmark again, see `backend/scripts/run_paper_v2.sh`.
