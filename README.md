<div align="center">

<img src="https://readme-typing-svg.demolab.com?font=Orbitron&weight=900&size=33&duration=3000&pause=1000&color=0891B2&center=true&vCenter=true&width=980&height=70&lines=FEDERATION+PLATFORM;One+Question+%E2%86%92+Four+Layers;Executed%2C+or+it+says+compiled" alt="Federation Platform" />

**A lakehouse where one question crosses four layers, and every number is traced back to the generator that produced it.**

![Trino](https://img.shields.io/badge/Trino-484-DD00A1?style=for-the-badge&logo=trino&logoColor=white)
![Iceberg](https://img.shields.io/badge/Iceberg-v2-1D4ED8?style=for-the-badge)
![Druid](https://img.shields.io/badge/Druid-38-29B5E8?style=for-the-badge&logo=apachedruid&logoColor=white)
![Hive](https://img.shields.io/badge/Hive-metastore_(file)-FDEE21?style=for-the-badge&logo=apachehive&logoColor=black)
![Postgres](https://img.shields.io/badge/PostgreSQL-catalog-336791?style=for-the-badge&logo=postgresql&logoColor=white)
![S3](https://img.shields.io/badge/S3-s3mock-569A31?style=for-the-badge&logo=amazons3&logoColor=white)
![Azure](https://img.shields.io/badge/Azurite-Blob_API-0078D4?style=for-the-badge&logo=microsoftazure&logoColor=white)
![DuckDB](https://img.shields.io/badge/DuckDB-OLAP-FFF000?style=for-the-badge&logo=duckdb&logoColor=black)
![FAISS](https://img.shields.io/badge/FAISS-vector_index-0467DF?style=for-the-badge&logo=meta&logoColor=white)
![Python](https://img.shields.io/badge/Python-3.11-1F5C3D?style=for-the-badge&logo=python&logoColor=white)

![Parity](https://img.shields.io/badge/Engine_parity-5%2F5_identical-2E7D32?style=for-the-badge)
![Reports](https://img.shields.io/badge/Reports-15_PASS-2E7D32?style=for-the-badge)
![Verify](https://img.shields.io/badge/make_verify-21_steps-0891B2?style=for-the-badge)
![Tests](https://img.shields.io/badge/tests-21_passing-2E7D32?style=for-the-badge)
![License](https://img.shields.io/badge/License-MIT-0D1117?style=for-the-badge)

</div>

---

## 🏛️ What this is

Three layers that a data platform job asks for, wired into **one system** instead of three demos:

| Layer | Tooling | The job description that asks for it |
|---|---|---|
| Distributed query | **Trino**, Hive table format, Druid, Presto dialect | Wikimedia — Hive, Presto, Druid |
| Azure data platform | ADLS Gen2 (Azurite), Data Factory, Synapse, TMDL/DAX, Power BI, Azure DevOps | Thales ×2, NTT |
| LLM data layer | chunking, embeddings, FAISS, BM25, labelling + kappa, NER, eval harness | Bio Protocol, Eclipse, Oyster |

They are one repository because they are one system, and the thing that makes it a system is a
single question:

> **Which category holds the most inventory value, and which earned the most revenue — and can the
> retrieval layer quote the document that states the fact?**

```
Druid ──────── revenue per category ──┐
                                      ├──→ join ──→ Azurite (parked blob, byte-compared)
Trino ──────── inventory per category ┘                        │
   (Iceberg over S3)                                           ▼
                                                    retrieval: the document, with a quote
```

> **Why** — "I can run Trino" is worth nothing if it means "a container started". The only claim
> that survives an interview is *the same number out of different engines, checked against a third
> source.*
> **Goal** — make every layer answer the same question, and make every stage fail loudly when its
> number stops lining up.
> **Impact** — **5/5** parity cases identical across engines, **15/15** reports PASS, **60,000**
> sales and **5,000** products through four layers, **0** fabricated claims: every target is either
> `executed` or `compiled`, and the report says which.

```bash
docker compose up -d                      # Trino · Postgres · S3 · Druid
python -m venv .venv && .venv/bin/pip install -r requirements.txt

make verify PYTHON=.venv/bin/python       # 21 steps → RESULT: PASS
```

---

## 📐 The pipeline

```
  ┌───────────────────┐
  │ ingestion.generate│  one deterministic dataset, three storage shapes
  └─────────┬─────────┘
            ├──────────────► data/landing/products/part-00000.parquet
            │                data/landing/sales/dt=YYYY-MM-DD/*.parquet   (30 partitions)
            ├──────────────► data/druid-ingest/{sales,products}.json      (JSON lines)
            └──────────────► quality/reports/expected.json                (the ground truth)

  ┌───────────────────┐        ┌──────────────────────────┐
  │ ingestion.iceberg │───────►│ Postgres   (table metadata)│
  │ _load (pyiceberg) │───────►│ S3         (data files)    │
  └─────────┬─────────┘        └───────────┬──────────────┘
            │                              │
            ▼                              ▼
  ┌───────────────────┐        ┌──────────────────────────┐
  │ Trino   (JVM)     │        │ Druid (single JVM)       │
  │ iceberg + hive    │        │ index_parallel ingestion │
  └─────────┬─────────┘        └───────────┬──────────────┘
            └────────────┬─────────────────┘
                         ▼
              ┌─────────────────────┐
              │ federation.parity   │  5 cases · 3 runs · 3 engines
              └──────────┬──────────┘
                         ▼
              ┌─────────────────────┐
              │ Azurite (ADLS Blob) │  park the join · read back · byte-compare
              └──────────┬──────────┘
                         ▼
              ┌─────────────────────┐
              │ llm/ · bench/       │  chunk · embed · FAISS vs BM25 · cite the source
              └─────────────────────┘
```

---

## 📊 Measured results

Every figure below is a field in `quality/reports/`. Nothing here is estimated.

### One query, three engines

`federation_parity.json` — 5 cases, 3 runs each, every value compared with the **generator**, not
with another engine.

| Case | Trino p50 | Druid p50 | Winner |
|---|---:|---:|---|
| `row_count_sales` | 208.7 ms | 88.4 ms | Druid 2.36× |
| `revenue_by_category` | 367.0 ms | 119.0 ms | Druid 3.08× |
| `inventory_by_category` | 248.2 ms | 106.3 ms | Druid 2.34× |
| `join_sales_products` | 510.2 ms | 306.1 ms | Druid 1.67× |

**Druid won every case — including the join, which was included expecting the opposite.** That is
fixed overhead on Trino's side (query planning + Iceberg metadata), not evidence that Druid is
universally faster. The report keeps the finding instead of deleting the case.

Presto is a **dialect**, checked with `sqlglot` and executed on Trino. The report says
`compiled, executed on trino` — never "Presto ran".

### Hive-format tables, read by Trino

`trino_catalog.json` — schema and tables created, partitions registered, counts compared with the
generator: **60,000** sales, **5,000** products. The metastore is a directory
(`hive.metastore=file`); the Hive Metastore *service* is not running, and the report says so.

### Retrieval, measured not assumed

`retrieval.json` — recall@1, recall@5, MRR and latency for four methods across three chunking
strategies, on 240 questions whose answers are known before any retrieval runs.

| Strategy | BM25 r@5 / MRR | Vector r@5 / MRR | IVF r@5 / MRR | Fusion r@5 / MRR |
|---|---|---|---|---|
| `fixed` | **1.00** / 0.760 | 0.50 / 0.500 | 0.50 / 0.500 | 0.875 / 0.692 |
| `recursive` | **1.00** / 0.760 | **1.00** / 0.760 | 1.00 / 0.760 | 1.00 / 0.760 |
| `semantic` | 0.50 / 0.250 | 0.50 / 0.244 | 0.029 / 0.012 | 0.50 / 0.250 |

Two results worth more than a headline:

* **Fusion lost to BM25** on `fixed` (0.875 vs 1.00). The check is `fusion_result_is_recorded`, not
  "fusion wins" — a benchmark that can only confirm its own expectation measures nothing.
* **Semantic chunking was the worst of the three**, on the strategy with the best name.

### Refusal, calibrated off the test set

`llm_eval.json` — 240 answerable questions, 5 the corpus cannot answer. Thresholds are calibrated
on **held-out** questions (80 answerable, 10 unanswerable, different phrasings and topics) and the
check `calibration_set_is_disjoint_from_eval_set` fails the run if that ever stops being true.

| Method | Answered @k=1 | Refused | Threshold |
|---|---|---|---|
| BM25 | 160/240 (0.667) | **5/5** | 2.967 |
| Vector (LSA) | 160/240 (0.667) | **5/5** | 0.557 |

### Labelling, with the answer known in advance

`llm_labeling.json` — synthetic annotators with **planted** disagreement, so a broken kappa cannot
hide behind a plausible-looking number:

| Planted disagreement | Observed agreement | Cohen's κ |
|---|---|---|
| 0% | 1.00 | **1.000** |
| 10% | 0.90 | **0.880** |
| 20% | 0.80 | **0.760** |
| 40% | 0.60 | **0.520** |

### Azure, per target

| Target | Result |
|---|---|
| ADLS Blob API (Azurite) | 31 files, 984,382 bytes, ~1.5 MB/s, readback **byte-identical** |
| Data Factory | 5 activities validated + executed by a local runner |
| Synapse SQL | every model transpiles and matches the generator |
| TMDL / DAX | every measure translated to SQL and matched against an independent query |
| Power BI | `.pbip` + TMDL written, every measure verbatim — `compiled, not executed` |
| Azure DevOps | YAML validated, same `ruff` + `pytest` commands run locally |

### The rest

| Claim | Number |
|---|---|
| Cohen's κ matches the planted value | 1.000 / 0.880 / 0.760 / 0.520 |
| NER precision & recall (4 labels, 120 docs) | **1.0 / 1.0** |
| Preference ranker, held-out pairs | 40 pairs, pairwise accuracy **1.0**, NDCG@4 **1.0** |
| Local embedding vs API | **9e-6 USD** per million words vs 0.02 → ~2,200× |
| Cross-layer question | electronics holds the most inventory (**1,038,903,990** cents), books earned the most revenue (**244,536,649** cents) |
| Citation check | the quoted document actually contains the fact — `cited_contains_fact: true` |
| `make verify` | **21 steps, 15 reports, RESULT: PASS** |

---

## 🧩 The components

| Path | What it does | Status |
|---|---|---|
| `ingestion/generate.py` | One deterministic dataset: 5,000 products, 60,000 sales over 30 days, integer cents | executed |
| `ingestion/iceberg_load.py` | Writes Iceberg through a Postgres JDBC catalog; data files in S3 | executed |
| `federation/trino/` | Hive + Iceberg catalogs, statement API with the `nextUri` chain | executed |
| `federation/druid/` | `index_parallel` batch ingestion, SQL over HTTP, server-side timing | executed |
| `federation/presto/` | Dialect validation and transpile with `sqlglot` | compiled |
| `federation/parity.py` | 5 cases × 3 runs × 3 engines, checked against the generator | executed |
| `azure_layer/storage/` | ADLS Gen2 Blob half via Azurite, throughput, byte-compare | executed |
| `azure_layer/adf/` | Pipeline JSON + schema validator + local runner (copy → lookup → filter → for-each) | executed (local), compiled (ADF) |
| `azure_layer/synapse/` | T-SQL transpile, executed on DuckDB | compiled |
| `azure_layer/tabular/` | TMDL + DAX → SQL, every measure compared with an independent query | compiled |
| `azure_layer/powerbi/` | `.pbip` project + report folder + semantic model | compiled, not executed |
| `azure_layer/devops/` | `azure-pipelines.yml` validated; the same commands run here | compiled |
| `llm/` | fixed/recursive/semantic chunking, LSA embedding, FAISS + BM25, kappa, NER, eval, preference | executed |
| `crosslayer/question.py` | The question, four layers, every number traced | executed |
| `bench/` | retrieval (recall/MRR/latency) and embedding cost | executed |
| `tools/tasks/verify.py` | 21 steps in order, then every report's `result` field | executed |

---

## ⚡ Engineering problems solved

The interesting part. All of these were **silent** — HTTP 200, a valid-looking table, a green check.

### Challenge 1 — A floating `latest` tag broke the file metastore

`trinodb/trino:latest` resolved to 484, which **disables the local filesystem by default**. The file
metastore failed with `Supported schemes: []`, which reads like a wrong path rather than a policy
change. Pinned to `trino:484` and set `fs.local.enabled=true`.

### Challenge 2 — `file://` and `local://` are not interchangeable

With `file://`, Trino 484 routed reads through Hadoop's `LocalFileSystem`, which resolved the path
under the **JVM temp directory**. Every table was created successfully and every `COUNT(*)` came
back **0**. `local://` uses Trino's own provider and reads the mounted directory. External locations
now use `local://`, and the reason is a comment in the code, not tribal knowledge.

### Challenge 3 — The metastore was writing into `/tmp`

The file metastore resolved under the JVM temp directory, so table metadata landed in
`/tmp/data/warehouse/tables` and would vanish with the container. The data directory is mounted at
both `/data` and `/tmp/data` so the metastore persists on the host.

### Challenge 4 — `external_location` must be a directory

Pointing it at `products.parquet` produced a **valid table that read zero rows**. The generator now
writes `data/landing/products/part-00000.parquet` and the table points at the directory.

### Challenge 5 — The file metastore does not discover partitions

Creating a partitioned external table succeeded and read nothing: `dt=` directories are invisible
until `CALL hive.system.sync_partition_metadata` registers them. A green DDL with no data is exactly
the kind of success this repository refuses.

### Challenge 6 — The generator wrote where the container was not looking

Druid ingests from `/data/druid-ingest`, the generator wrote `data/druid/`. A manually copied file
made it work, and that copy was invisible in the repository. Two directories, one of them wrong.

### Challenge 7 — A sum that was wrong by exactly 2×

When the Druid input moved, the products `__time` shifted, so the new task replaced only its own
interval — and the datasource kept **two copies of every row**. `COUNT(*)` on sales stayed correct;
`SUM(price_cents * stock_count)` on products was exactly double. No error, no warning, three green
checks.

### Challenge 8 — Druid's delete API lies politely

`DELETE /druid/coordinator/v1/datasources/{name}?kill=true` answered **500 with an empty body** on
both the coordinator and the router. A `kill` task then reported **SUCCESS** and the segments stayed
queryable for five minutes. The reliable reset is a fresh container, and the README says so instead
of wrapping the broken call in a retry loop.

### Challenge 9 — One threshold for two different score scales

The first eval used a single cosine threshold (0.15) for both methods. BM25 scores are unbounded
(5.4 for a good hit); cosines are not (0.57 vs 0.47). The vector side refused **0/5** out-of-corpus
questions while BM25 refused 5/5 — a property of the units, not of the data. Fixed with per-method
thresholds calibrated on a **held-out** question set, with a check that fails if the sets ever
overlap.

### Challenge 10 — A stale blob made a count wrong

The Azurite container was never emptied, so the listing check saw **32** blobs for **31** uploaded
files: a `products.parquet` from the previous layout was still there. A stale blob is
indistinguishable from a wrong upload if you only compare counts.

### Challenge 11 — `pytest` died before running a test

`data/trino` contains Docker-created junctions that Windows refuses to `stat`, and `pytest.ini` was
using the wrong section header (`[tool:pytest]`), so it collected `data/`. Collection died with
`WinError 1920`. Header fixed, `norecursedirs` set.

### Challenge 12 — A CI check that ran in a neighbour's virtualenv

`local_equivalent` shelled out to a bare `ruff` and `pytest` from `PATH`, which resolved to another
project's interpreter — the report was measuring a different environment. It now runs
`sys.executable -m ...`, and the step passes for the right reason.

---

## ✅ `make verify`

```bash
python -m tools.tasks.verify            # everything, in dependency order
python -m tools.tasks.verify --skip-docker
python -m tools.tasks.verify --list
```

Two things are checked, and they are different:

1. **the process exit code** — a script that crashes;
2. **the report's `result` field** — a script that prints `FAIL` and returns 0.

The second one is the reason this file exists. Eleven green reports prove nothing if nobody runs
them, and a verdict that only lives in stdout is a verdict nobody audits.

Expected tail:

```
  azure_adf.json                 PASS
  azure_storage.json             PASS
  crosslayer.json                PASS
  federation_parity.json         PASS
  llm_eval.json                  PASS
  retrieval.json                 PASS
  trino_catalog.json             PASS
  ...
RESULT: PASS
```

---

## 🚀 Quick start

Prerequisites: Docker, Python 3.11, GNU make (optional).

```bash
docker compose up -d                      # Trino · Postgres · S3 · Druid
python -m venv .venv
.venv/bin/python -m pip install -r requirements.txt

make verify PYTHON=.venv/bin/python
```

On Windows without GNU make:

```powershell
docker compose up -d
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m tools.tasks.verify
```

Run one layer on its own:

```bash
python -m ingestion.generate                      # the dataset + expected.json
python -m ingestion.iceberg_load                  # Iceberg via Postgres catalog + S3
python -m federation.trino.catalog --register     # Hive tables + partitions
python -m federation.druid.ingest --load          # Druid datasources
python -m federation.parity --runs 3              # one query, three engines
python -m bench.retrieval                         # BM25 vs FAISS vs fusion
python -m crosslayer.question                     # four layers, one question
```

**Resetting Druid.** Batch ingestion replaces the segments whose interval it covers, so re-loading
the same generated data is idempotent. If the generated timestamps ever move, old segments survive
and a sum silently doubles — reset the container:

```bash
docker compose rm -f druid && docker compose up -d druid
```

---

## 📁 Structure

```
federation-platform/
  federation/      trino/  druid/  presto/  parity.py
  azure_layer/     storage/  adf/  synapse/  tabular/  powerbi/  devops/
  ingestion/       generate.py  iceberg_load.py
  llm/             chunking  embed  index  labeling/  ner  eval/  preference
  crosslayer/      question.py
  bench/           retrieval.py  cost.py
  infra/trino/etc/ catalogs: hive · iceberg · memory
  tools/tasks/     verify.py
  quality/reports/ 15 verdicts, written by the run
  docs/adr/        0001..0005
```

---

## 🛠️ Tech stack

| Role | Technology |
|---|---|
| Federated query | Trino 484 (single-node), Hive connector with file metastore |
| Real-time OLAP | Druid 38 (single JVM, batch `index_parallel`) |
| Table format | Iceberg v2, JDBC catalog in Postgres, data files in S3 |
| Dialect checks | `sqlglot` (Presto, T-SQL) |
| Object storage | Azurite (Blob API), `adobe/s3mock` |
| Equivalent-engine execution | DuckDB |
| Model semantics | TMDL + DAX → SQL |
| Retrieval | FAISS `IndexFlatIP` + `IndexIVFFlat`, hand-written BM25, reciprocal rank fusion |
| Embedding | TF-IDF + TruncatedSVD (LSA), deterministic, offline |
| Statistics | `scikit-learn` (logistic ranker), Cohen's κ implemented directly |
| Verification | `pytest`, `ruff`, a 21-step runner that checks every report |

---

## ⚠️ Limitations (honest)

- **Azure is not run on Azure.** Azurite is the official emulator of the **Blob API**, not ADLS Gen2.
  The hierarchical namespace — directory rename, atomic operations, ACL inheritance — is not
  exercised, because Azurite does not implement the DFS endpoint.
- **Synapse, SSAS and Power BI are compiled, not executed.** There is no DAX engine here and no
  Power BI Desktop. What is proven is the model and the artefacts, not the service.
- **Single-node everything.** Trino has no workers and no fault tolerance; Druid is one JVM with an
  embedded ZooKeeper. Ingestion and query latency are proven; cluster operations are not.
- **Druid ingestion is batch, not Kafka.** The plan said Kafka. The claim being tested is query
  latency on the same rows, and a broker would change nothing about that — so the deviation is
  recorded in [ADR 0003](docs/adr/0003-druid-batch-and-rollup.md) rather than described as Kafka.
- **The embedding is LSA, not a neural model.** It is called that everywhere. On this corpus it does
  not beat BM25 overall (both 160/240), and the benchmark reports where it loses.
- **No RLHF.** Preference pairs and an offline logistic ranker. No policy, no rollout loop, no reward
  model training — and the report carries a `not_claiming_rlhf` check so the word cannot drift in
  later.
- **The dataset and corpus are synthetic.** Deterministic by construction, which is what makes parity
  testable at all — and the exact limit of what it proves.
- **`s3mock` is a test double.** MinIO withdrew its public images from Docker Hub; the application
  only knows an endpoint URL, so production is a config change — but `s3mock` is not a system of
  record.
- **Snowflake is still not executed.** Needs an account; none exists. It stays `compiled, not
  executed` in the gap ledger.

---

## 👤 Author

<div align="center">

**Sandi Ridwan** — Data Automation Engineer · Web Scraping · Data Engineering

📍 Palu, Central Sulawesi, Indonesia

[![Upwork](https://img.shields.io/badge/Upwork-Hire_Me-1D4ED8?style=for-the-badge&logo=upwork&logoColor=white)](https://www.upwork.com/freelancers/~011f6d0fbb4a372974)
[![LinkedIn](https://img.shields.io/badge/LinkedIn-Connect-0077B5?style=for-the-badge&logo=linkedin&logoColor=white)](https://linkedin.com/in/sandi-ridwan)
[![GitHub](https://img.shields.io/badge/GitHub-Follow-181717?style=for-the-badge&logo=github&logoColor=white)](https://github.com/SandiRidwan)

</div>

## 📄 License

MIT — the code. The dataset is generated by `ingestion.generate`, so it carries no third-party
rights; the Azurite account key in `tools/common/config.py` is Microsoft's published development
key and only works against the emulator.
