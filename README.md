# Ease

Supervised-autonomy multimodal multi-agent system for web navigation and task automation.
Final-year major project, Department of Computer Science, ABES Engineering College.

A user types a goal in plain English. A supervisor LLM turns it into a typed plan (a DAG of steps).
Each step runs through a REST connector when one exists, or through a browser agent
(DOM/accessibility-tree grounding with Set-of-Marks vision fallback) when it doesn't.
Irreversible actions pause for human approval, and the pause survives worker restarts
(LangGraph `interrupt()` + Postgres checkpoints).

Status: under active development.
