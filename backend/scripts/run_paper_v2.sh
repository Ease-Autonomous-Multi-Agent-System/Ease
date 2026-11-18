#!/usr/bin/env bash
# Every benchmark run behind the journal manuscript (benchmark v2). Resumable: each stage runs only the cases that
# still lack a valid record (see pending_cases.py), so it can be re-run after an interruption or a quota reset.
# Run from backend/:
#
#   BU_PY=<venv with browser-use 0.13.11>/Scripts/python \
#   M1_PY=<venv with autogen-agentchat/ext 0.7.5, playwright 1.64.0>/Scripts/python \
#   BU_CHROME="C:\Program Files\Google\Chrome\Application\chrome.exe" \
#   bash scripts/run_paper_v2.sh [fixture] [live] [hitl] [comparison]
#
# Gemini keys: the free tier allows 500 requests per day per model and key, so the comparison can give each system
# its own key (same model, separate quota). Name the .env variables to use (never the keys themselves):
#   KEY_EASE=GEMINI_API_KEY  KEY_BU=GEMINI_API_KEY_2  KEY_M1="GEMINI_API_KEY_3 GEMINI_API_KEY_3 GEMINI_API_KEY_2"
# (KEY_M1 may list one variable per round.) Defaults: GEMINI_API_KEY everywhere.
# Needs: docker compose up -d fixtures; GROQ_API_KEY and the Gemini key(s) in ../.env. Results: ../data/eval/v2-*.
set -u
cd "$(dirname "$0")/.."
PY=../.venv/Scripts/python
[ -x "$PY" ] || PY=../.venv/bin/python
export PYTHONUTF8=1 PYTHONIOENCODING=utf-8 ANONYMIZED_TELEMETRY=false
FIX="F01-remote-interns,F02-count-fulltime,F03-salary-lookup,F04-apply-cv,F05-rank-apply,F06-paginated-old-posts,\
F07-shop-under-budget,F08-shop-cheapest-phone,F09-shop-best-audio,F10-portal-deadlines,F11-portal-no-session,\
F12-popup-articles,F13-popup-latest,F14-list-instructor,F15-list-credits"
LIVE="R01-arxiv-recent,R02-arxiv-topic-summary,R03-openalex-cited,R04-arxiv-cv,L01-greenhouse-jobs,\
L02-books-under-20,L03-quotes-einstein,L04-quotes-tags,L05-books-category"
HITL="F04-apply-cv,F05-rank-apply,F10-portal-deadlines,F11-portal-no-session"
CMP="F01-remote-interns,F02-count-fulltime,F03-salary-lookup,F04-apply-cv,F06-paginated-old-posts,\
F07-shop-under-budget,F08-shop-cheapest-phone,F09-shop-best-audio,F12-popup-articles,F13-popup-latest,\
F14-list-instructor,F15-list-credits"
PROFILE=../data/eval/v2-synthetic-profile.json
STAGES="${*:-fixture live hitl comparison}"

key() {  # value of the .env variable named $1 (printed only into the child process's environment)
  grep -E "^$1=" ../.env | head -1 | cut -d= -f2-
}
pending() { $PY scripts/pending_cases.py "$1" "$2" "$3"; }

for stage in $STAGES; do
  case $stage in
  fixture)
    for r in 0 1 2; do
      c=$(pending v2-fixture $r "$FIX"); [ -z "$c" ] && continue
      GEMINI_API_KEY=$(key "${KEY_EASE:-GEMINI_API_KEY}") $PY -m ease.eval.harness --suite fixture --cases "$c" \
        --repeats 1 --repeat-start $r --run-id v2-fixture --profile-json $PROFILE --with-vectors --pause 5 \
        --infra-retries 3 >> ../data/eval/v2-fixture.log 2>&1
    done ;;
  live)
    c=$(pending v2-live 0 "$LIVE")
    [ -n "$c" ] && GEMINI_API_KEY=$(key "${KEY_EASE:-GEMINI_API_KEY}") $PY -m ease.eval.harness --suite live \
      --cases "$c" --repeats 1 --run-id v2-live --profile-json $PROFILE --pause 5 --infra-retries 3 \
      >> ../data/eval/v2-live.log 2>&1 ;;
  hitl)
    for cond in hitl; do
      c=$(pending v2-hitl 0 "$HITL")
      [ -n "$c" ] && GEMINI_API_KEY=$(key "${KEY_EASE:-GEMINI_API_KEY}") $PY -m ease.eval.harness --suite fixture \
        --conditions $cond --cases "$c" --repeats 1 --run-id v2-hitl --profile-json ../data/eval/v2-profile-hitl.json \
        --with-vectors --pause 5 --infra-retries 3 >> ../data/eval/v2-hitl.log 2>&1
    done ;;
  comparison)
    # One shared model for every call of every system: Gemini flash-lite, no failover, strong tier pinned too.
    read -r -a M1KEYS <<< "${KEY_M1:-GEMINI_API_KEY}"
    for r in 0 1 2; do
      c=$(pending v2-cmp-ease $r "$CMP")
      [ -n "$c" ] && GEMINI_API_KEY=$(key "${KEY_EASE:-GEMINI_API_KEY}") \
      LLM_TEXT_PROVIDER_ORDER=gemini LLM_PROVIDER_ORDER=gemini \
      LLM_MODEL_GEMINI_FAST=gemini-flash-lite-latest LLM_MODEL_GEMINI_STRONG=gemini-flash-lite-latest \
      LLM_MODEL_GEMINI_VISION_FAST=gemini-flash-lite-latest LLM_MODEL_GEMINI_VISION_STRONG=gemini-flash-lite-latest \
        $PY -m ease.eval.harness --suite fixture --cases "$c" --repeats 1 --repeat-start $r --run-id v2-cmp-ease \
          --profile-json ../data/eval/v2-profile-ease-cmp.json --pause 8 --infra-retries 3 >> ../data/eval/v2-cmp-ease.log 2>&1
      c=$(pending v2-cmp-bu $r "$CMP")
      [ -n "$c" ] && GEMINI_API_KEY=$(key "${KEY_BU:-GEMINI_API_KEY}") "$BU_PY" ease/eval/external_agent.py --cases "$c" \
          --repeat $r --run-id v2-cmp-bu --profile ../data/eval/v2-profile-bu.json --pause 8 >> ../data/eval/v2-cmp-bu.log 2>&1
      m1key=${M1KEYS[$r]:-${M1KEYS[0]}}
      c=$(pending v2-cmp-m1 $r "$CMP")
      [ -n "$c" ] && GEMINI_API_KEY=$(key "$m1key") "$M1_PY" ease/eval/external_magentic.py --cases "$c" \
          --repeat $r --run-id v2-cmp-m1 --profile ../data/eval/v2-profile-m1.json --pause 8 >> ../data/eval/v2-cmp-m1.log 2>&1
    done ;;
  esac
done
echo "done: $STAGES"
