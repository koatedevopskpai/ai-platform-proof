# E04 gate reuse

`gate_policy.py` is copied unchanged from koatedevopskpai/llm-evals-demo,
task/E04-calibrated-gate, handoff commit
`0cd70a0ffd78572d7a88a5252dfd450b9ed689f7`.
SHA-256: `258015f467064b228fc189105a1a81b22021a9ae5b364fb7b7368feaf631aa37`.

It enforces finite scores, per-case and mean floors, nonempty answers/context,
judge errors, expected counts and citation existence. It is not a semantic judge.
The O02 policy is explicitly different: binary, exact fixture acceptance requires
all three cases to pass. E04's 0.92/0.90 model-score thresholds are not transferred
as calibrated thresholds to these binary predicates. R01's provenance principles
are applied to the source, original/staged configuration, dataset, images and run.
