# Agent diagnostic evaluation fixtures

`cases.json` labels the first four deterministic diagnostic scenarios and
`golden_traces.json` contains reviewable traces that satisfy the evidence and
safety contract. Each case's `required_operations` must appear in the trace in
the declared order. Every operation must reference captured evidence, and each
evidence record must include non-empty `content`, `output`, or `raw` data. A
root-cause record is accepted only when its referenced evidence exists and at
least one record explicitly lists the expected label in `supports`.

`evaluator.py` computes completion, known-root-cause accuracy,
developer interventions, authorizations, operation failure rate, session
recovery, regression validation, and unauthorized hardware actions.

These are contract fixtures, not proof that an LLM independently solved the
bugs or that a physical MCU behaved as described. A real Agent run may be
evaluated by replacing a trace while retaining the same case labels and rubric;
the result must preserve raw CLI/MI artifacts and the exact repository/tool
identity before it is reported as evidence.
