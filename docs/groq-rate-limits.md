# Running with a small Groq quota

For the observed 1,000 output-token/minute quota, configure app and worker:

```
LLM_MAX_TOKENS=900
LLM_MIN_REQUEST_INTERVAL_SECONDS=61
LLM_MAX_CONCURRENCY=1
```

The token cap applies to all completion calls, including repository tools. The interval is per client, not a distributed limiter; keep one worker for this quota. Independent dashboard calls and new jobs can still receive 429s. Groq calls retry boundedly after the provider's delay, without shortening it. Waits over 120 seconds fail clearly so the durable job policy can handle subsequent retries.

Requests exceeding the input quota cannot be solved by waiting and fail with a token-budget error. Output cut off by the cap is rejected, never published as a complete patch. Large issues require a smaller task or a provider/model with enough quota; these settings do not increase the provider allowance. No billing upgrade is required or performed by this change.
