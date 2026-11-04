# Agent diagnostic evaluation fixtures

`cases.json` labels the first four deterministic diagnostic scenarios and
`golden_traces.json` contains reviewable traces that satisfy the evidence and
safety contract. `evaluator.py` computes completion, known-root-cause accuracy,
developer interventions, authorizations, operation failure rate, session
recovery, regression validation, and unauthorized hardware actions.

These are contract fixtures, not proof that an LLM independently solved the
bugs or that a physical MCU behaved as described. A real Agent run may be
evaluated by replacing a trace while retaining the same case labels and rubric;
the result must preserve raw CLI/MI artifacts and the exact repository/tool
identity before it is reported as evidence.
