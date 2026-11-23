# Provenance

Source catalog SHA-256: `cc6a6159e5d002c8a2bad0280abd42fbed02279faab59e9935fcfd7c9e430760`.

The public data is exported from one internal source catalog by an internal build script. It copies an explicit list of columns (listed in [summary.json](../data/summary.json) and [DATA.md](DATA.md)), keeps every number exactly as recorded and normalizes model, GPU and configuration labels to one public name each. It publishes every record that is a load measurement of one model on one GPU type served by one vLLM or SGLang engine, with GPU, GPU count, engine, concurrency and output throughput recorded, and that passes the checks below.

`row_id` numbers the published rows 1 to 490 in release 2026.11.23. Cite a single result by its `row_id` and the release.

## What was left out

The source holds 958 records. 490 are published; the rest are counted here by the first reason that applies.

| Reason | Rows | Meaning |
|---|---:|---|
| damaged-or-invalid | 54 | damaged records, runs marked invalid or that failed, first attempts repeated after a setup error, and runs whose configuration could not be confirmed |
| overloaded | 12 | levels where no request completed inside the timing window, or the median time to first token exceeded 60 s (requests mostly queued) |
| warm-up-level | 13 | the SGLang concurrency-4 level on Dedicated Endpoints: in most SGLang runs its time to first token was many times that of the levels before and after it (up to about 4 s), so the level is left out for every SGLang run |
| duplicate | 2 | exact duplicates of a published row |
| incomplete | 100 | records missing GPU, GPU count, engine, concurrency or output throughput, such as startup-only timings |
| out-of-scope | 287 | not one model on one GPU type served by one vLLM or SGLang engine: whole-service tests, several engines sharing a GPU, other engines or development builds, and summary-only figures with no per-run records |

## Areas

| Area | What it covers |
|---|---|
| `dedicated-endpoints` | November 2026 runs on Spheron Dedicated Endpoints: one GPU, vLLM or SGLang as installed by default. |
| `multi-gpu-tuning` | October 2026 tuning sweeps on B200 and B300, one to eight GPUs: tensor, data and expert parallelism, speculative decoding, KV cache and context options. |
| `large-model-validation` | October 2026 validation sweeps of large mixture-of-experts models on 4x B300. |
| `single-node-sweeps` | November 2026 `vllm bench serve` sweeps and single-request image tests on one node. |

## Limits

The original harnesses and raw logs are not published, so recorded rows cannot be re-run from this repository. [benchmark.py](../benchmark.py) produces new measurements against any OpenAI-compatible endpoint you can reach; it does not reproduce every historical workload.
