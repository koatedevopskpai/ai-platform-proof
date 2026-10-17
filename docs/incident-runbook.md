# Local incident runbook (O04 synthetic Compose profile)

1. Record the response x-trace-id (also in generic error JSON). Collect metadata
   using `docker compose -p <project> -f <bundle>/compose.json logs --no-log-prefix
   --since 5m gateway rag-api`, then grep for that trace ID. Prompts/documents are
   not span fields. Scope the collection to the incident; do not share arbitrary
   third-party logs without checking them.
2. Follow parent IDs: gateway http.request -> rag.application -> rag-api
   http.request -> embedding.embed / retrieval.search / model.generate. An error
   in retrieval.search with component pgvector and a resulting HTTP 503 was the
   database-outage symptom actually observed. A model failure uses safe provider
   reason codes; it may still return a successful deterministic fallback, which
   needs acceptance checking rather than merely HTTP 200.
3. For the demonstrated database-stop incident, inspect `docker compose ... ps`.
   Start the stopped postgres service with the same project/configuration, then
   require aggregated backend health AND the three fixture acceptance cases.
   Do not treat every database error as a stopped service or blindly restart
   unrelated resources. Schema/configuration errors need their own diagnosis.
4. For an application answer regression, hold promotion. Restore the saved
   accepted immutable bundle through release.py deploy (O03), and require its
   quality/functional checks and image-ID verification. Recovery is operator-led
   and may interrupt traffic; this is not automated or zero-downtime failover.
5. Compare healthy/failure/recovery observations with incident_metrics.summarize.
   It demonstrated clear -> firing -> clear at >5% acceptance errors in bounded
   windows. The 1000ms p95 target is configured, not a production SLO. Alerts are
   local JSON records, not scheduled monitoring or externally delivered pages.
6. Retain the UTC event times, source/image/config hashes, trace samples, observed
   counts/latencies, recovery result and residual limits. Destroy only the
   task-created stack/cloud resources after the demo and verify deletion.

See docs/O04.md for the dated real run, exact counts and reproduction command.
The original machine is gone; the dated receipt is not evidence of current uptime.
