# Methodology

This repository holds two separate things: a catalog of recorded measurements (`historical-catalog-v1`) and a client for taking new ones (`spheron-sse-run-v1`). They use different methods and must not be mixed in one comparison.

## Recorded measurements

Every number is kept exactly as recorded; nothing is re-run, repaired, re-rounded or converted to the new client's definitions. Model, GPU and configuration labels are normalized to one public name each. Each row is one load measurement: one model on one GPU type with vLLM or SGLang, at a recorded concurrency. The load methods behind the rows:

- **Dedicated Endpoints** (`dedicated-endpoints`, November 2026). A fixed number of requests per concurrency level (at least 8, or twice the concurrency), all with the same prompt: 220 input tokens and up to 256 output tokens (some answers end earlier), temperature 0, streaming with usage reported. The first streamed content or reasoning token counts as the first token. Because the prompt repeats, prefix caching can help, so these numbers suit comparing GPUs and engines with each other, not predicting throughput on unique traffic.
- **Tuning sweeps** (`multi-gpu-tuning`, October 2026). Closed loop with unique prompts (unique prefix plus shuffled filler), a fixed duration per concurrency level (90 to 180 seconds) and a warm-up period that is excluded. Tokens are counted inside a steady-state window, including tokens from requests still streaming when it closes, while `req_s` counts only requests that finished inside it. That is why output tok/s can be higher than `req_s` times the output length at high load. Thinking is usually turned off and some runs ignore end-of-sequence, as the `workload` text states. `req_s` is completed requests divided by the window length, so two runs that complete the same number of requests show the same value.
- **Large-model validation** (`large-model-validation`, October 2026). Streaming sweeps with prompts of about 750 or 5,900 words on 4x B300, 8 to 64 concurrent requests. Engine version and precision were not recorded, and neither was whether prompts were reused across requests, so prefix caching may apply; some contexts show higher throughput with the longer prompt. Do not compare the two prompt lengths directly.
- **Single-node sweeps** (`single-node-sweeps`, November 2026). Mostly `vllm bench serve` with random prompts, ignore end-of-sequence and seed 0, plus single-request image questions. Where two model copies share an 8-GPU node, the row is the node total and the `workload` text gives the load per copy.
- **Research studies** (`research`, November 2026). `vllm bench serve` with random prompts, 1,024 input and 512 output tokens, ignore end-of-sequence and prefix caching off, three repeats per concurrency level with seeds 1 to 3, each kept as its own row. Each study's `REPORT.md` and `DATA.md` under [research/](../research) give the full method, the engine flags and the deviations from plan.

Rates are aggregate across all concurrent requests. Do not pool rows from different areas or workloads.

Left out (counted in [PROVENANCE.md](PROVENANCE.md)): levels where no request completed inside the timing window or the median time to first token exceeded 60 s, which means requests were mostly queued rather than served; the SGLang concurrency-4 level on Dedicated Endpoints, the first level with more than one request in flight, because in most SGLang runs its time to first token was many times that of the levels before and after it (up to about 4 s); and exact duplicate rows.

## New runs with benchmark.py

Python 3.9 or newer, standard library only. The client sends streaming chat completion requests to a full OpenAI-compatible URL, asks for usage in the stream, uses temperature 0 and caps output with `--max-tokens`. It never provisions, changes or deletes infrastructure. HTTPS is required except on loopback, and redirects are refused so the key cannot leak to another host. The key is read only from `OPENAI_API_KEY`. The output never contains the URL, the key, prompts, responses or server error text.

Each concurrency level is a fixed-count closed loop on a thread pool: at most C requests are in flight, and a new one starts when one finishes. Warm-up requests run first and are excluded. Timing starts before the first measured request is submitted and stops when the last one ends, so it includes ramp-up, queueing, failures and the drain at the end. There is no open-loop arrival process and no steady-state window. Each prompt starts with a unique UUID and random filler words to limit prefix reuse; this cannot rule out caching of server-side templates. `--input-words` counts words, not tokens.

The SSE parser handles comments, blank-line frame boundaries and multi-line data. A request succeeds only with a finish reason and `[DONE]`. Abrupt end of stream, invalid JSON, error frames, HTTP errors and timeouts count as failures. There are no retries. `--timeout` applies to each socket operation, so a stream that keeps sending data can run longer. At high concurrency the client machine or network can become the bottleneck; results describe the endpoint as seen from the client, not GPU kernel time.

## Definitions

- **Wall time**: seconds measured across all attempts in a level, warm-up excluded.
- **Request rate**: successful requests divided by wall time.
- **Tokens**: only the non-negative integer `prompt_tokens` and `completion_tokens` reported by the API. No tokenizer estimates and no counting of stream chunks.
- **Output tok/s**: completion tokens of successful requests divided by wall time, only when every successful request reported usage; otherwise `null`. Failed requests are excluded even if they produced tokens.
- **Total tok/s**: prompt plus completion tokens divided by wall time, with the same full-usage rule for both.
- **TTFT**: from sending the request to the first non-empty content, reasoning or tool-call delta, including network and queueing time. p50 and p95 use the nearest rank, no interpolation, over successful requests only.
- **TPOT estimate**: (request time minus TTFT) divided by (completion tokens minus 1), for successful requests with more than one token. It includes transport and finalization time. The median is reported as `tpot_estimate_p50_ms`.
- **ITL**: not produced by the client, because one SSE chunk can carry several tokens.

## Which rows are charted

[PROVENANCE.md](PROVENANCE.md) counts the source records left out. Every published row is a load measurement. A row is charted when its concurrency is a positive integer, its output throughput is a finite non-negative number, and its exact context (model, config, engine, engine_version, gpu, gpu_count, area, workload, precision, input_tokens, output_tokens, date) has at least two concurrency levels. Missing values are kept as missing and never inferred. Repeated runs stay separate points instead of being averaged.

## Limits

Cache state, timing windows, sample counts and model settings vary between areas. Most levels were run once; where a context was repeated, runs differ by up to about 15%. There are no confidence intervals and none are made up. Some engine versions are not pinned (`managed default` on Dedicated Endpoints). Before sharing a new run, record the engine and version, GPU and count, precision, model revision, serving configuration, cache policy, background load, reasoning settings and network location yourself: the client does not read them from the endpoint.
