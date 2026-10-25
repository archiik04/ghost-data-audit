# Usage report - final full-dataset run

Generated from `code/evaluation/out/run_report.json` of the run that wrote `output.csv` (518 requests, runtime 298.5 s). The model caches were cleared before this run, so every token below was actually spent in it.

Provider: **OpenAI** (Chat Completions API, structured JSON-schema output for intent).

## Per model

| Model | Purpose | Calls | Input tokens | Output tokens | Price in/out (USD per 1M) | Cost (USD) |
|---|---|---:|---:|---:|---|---:|
| gpt-5.4 | screenshot transcription (vision) | 11 | 27,093 | 4,097 | 2.50 / 15.00 | 0.1292 |
| gpt-5.4-mini | request-intent classification (3-vote majority) | 1194 | 330,723 | 60,641 | 0.75 / 4.50 | 0.5209 |
| **Total** | | **1205** | **357,816** | **64,738** | | **0.6501** |

## Per request

- Total tokens: 422,554 (input 357,816, output 64,738)
- Average tokens per request: 815.7
- Average model calls per request: 2.33
- Estimated total cost: $0.6501
- Estimated cost per request: $0.001255
- Failed model calls (retried, then rule fallback): 0
- Cache hits during this run: 527 (each request first warms the cache in a concurrent prefetch, then reads it back, so hits are expected). Tokens recorded on the cached results read back: 440,868 in / 80,661 out - this is the same spend as above when the cache was cleared before the run, not additional cost.

## Where the tokens go and why

- Every deterministic stage (evidence parsing, identity graph, state reconstruction, policy, planning, serialization) uses **zero** model tokens.
- Intent: one short prompt per distinct request text, sampled 3 times for a majority vote (the 32 samples contain no NOT_APPLICABLE example, so this step gets extra robustness). The 32 sample requests are classified in the same run because every participant-facing request competes in the assignment of identifier-less workflow records (a non-deletion request must not claim one); their calls are included in the totals above.
- Vision: one transcription per screenshot (11 images), cached by image SHA-256; identifiers are then extracted deterministically from the transcription.
- Re-runs are free: results are cached by (model, prompt version, content hash).

Prices: OpenAI model pages for GPT-5.4 mini ($0.75 / $4.50) and GPT-5.4 ($2.50 / $15.00) per 1M input/output tokens, standard tier. Costs are estimates computed from the token counts the API returned.
