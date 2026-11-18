# Odoo AI Assistant

An LLM assistant for warehouses that run Odoo. Staff ask questions in plain language
("which lots expire this week and what do we do with them?") and get answers built from
live stock data and the company's own procedures. It also reads supplier delivery notes
and turns them into a receipt proposal checked against Odoo.

It runs at zero cost: the LLM is Google Gemini's free tier, Groq's free tier or a local
model with Ollama, all through the same OpenAI-compatible client. Without an Odoo server
it uses a demo dataset with the same models and fields (`stock.lot`, `stock.quant`,
`stock.picking`...).

## What it does

| Endpoint | What | How |
|---|---|---|
| `POST /ask` | Answers from the warehouse procedures, with citations | RAG: chunking by section, embeddings, cosine search, grounded prompt |
| `POST /agent` | Answers about stock, lots, expiry and deliveries | Tool-calling loop over Odoo (XML-RPC), read-only |
| `POST /extract` | Delivery note (text or PDF) to a receipt proposal | Structured output validated with Pydantic, retry on errors, matching against Odoo |
| `GET /usage` | Tokens and latency per operation | Logged on every LLM call |
| `GET /` | Small web UI for the three features | |

What an agent run looks like with the demo data (the wording depends on the model):

```
> Which lots expire this week and what should we do with them?

tool: get_expiring_lots(days=7)
tool: search_procedures("near expiry and expired lots")

Expired: MOZ-2610-Z (12 units, quarantine), YOG-2611-A (36 units).
Expiring: YOG-2611-B in 4 days (120), MOZ-2611-C in 6 days (80).
Expired lots go to WH/Quarantine and get a scrap order with reason "Expired" [1].
Lots with 1 to 7 days left are offered to sales or the food bank [2].
```

## Design decisions

- **No framework for the agent.** The loop is ~60 lines (`app/agent/loop.py`): call the
  model with the tool schemas, run the tools it asks for, send back the results, repeat
  up to N steps. Tool arguments are validated with Pydantic and errors go back to the
  model so it can correct itself.
- **Read-only on Odoo.** The agent can only read. Extraction returns a proposal plus a
  list of issues (unknown product, lot-tracked line without lot or expiry date), and a
  person decides. LLM output never writes to the ERP directly.
- **Don't call the model when there is nothing to say.** If no chunk passes the
  similarity threshold, `/ask` answers "not found" without spending a request.
- **Provider-agnostic.** Gemini, Groq, OpenRouter and Ollama all speak the
  OpenAI-compatible API; switching is one environment variable. Free tiers return 429
  often, so the client retries with exponential backoff.
- **Two vector stores.** NumPy brute force for small corpora (a few thousand chunks
  search in under a millisecond, and the index fits in S3 for Lambda), pgvector when it
  grows or several instances share it.
- **Evaluated, not eyeballed.** `evals/` has datasets and metrics for each feature:
  hit@k and MRR for retrieval, pass rate (right tools + right facts) for the agent,
  field-level accuracy for extraction. CI fails if retrieval drops below 0.85 hit@3.

## Run it

```bash
make install                 # venv + dependencies
cp .env.example .env         # put a free Gemini key in LLM_API_KEY
make ingest                  # build the index from data/docs
make run                     # http://localhost:8000 and /docs for the API
```

No key at all: `LLM_PROVIDER=ollama EMBED_PROVIDER=ollama` with
`ollama pull qwen2.5:3b && ollama pull nomic-embed-text`. For tests only,
`EMBED_PROVIDER=local` uses an offline hashing embedder.

With PostgreSQL + pgvector: `docker compose up --build`.

Against a real Odoo (15 to 19), set `ODOO_MODE=live` and the `ODOO_*` variables. Use an
API key of a user with read access to Inventory.

## Evaluations

```bash
python -m evals.run retrieval     # no LLM calls
python -m evals.run extraction    # 2 delivery notes, field accuracy
python -m evals.run agent         # 7 questions, tools used + facts in the answer
```

Results are saved in `evals/results/` to compare models, prompts or chunk sizes.

| Suite | Setup | Result |
|---|---|---|
| retrieval | offline hashing embedder | hit@3 0.93, MRR 0.89 |

The hashing embedder misses the Spanish question ("¿Qué hago con un lote caducado?")
because it only matches words. Semantic embeddings (Gemini, nomic) handle it: that is the
point of comparing them.

## Deploy on AWS

`infra/terraform` deploys the API as a Lambda container behind a Function URL, with the
index in S3, the LLM key in SSM Parameter Store and a 1 USD budget alarm. Everything
used fits in the AWS free tier. See [docs/aws.md](docs/aws.md).

```bash
cp infra/terraform/terraform.tfvars.example infra/terraform/terraform.tfvars
./scripts/deploy_aws.sh
```

## Project layout

```
app/
  llm/        OpenAI-compatible client, retries, usage tracking, FakeLLM for tests
  rag/        chunking, embeddings, vector stores, ingestion, grounded answers
  agent/      tools over Odoo and the tool-calling loop
  extract/    delivery note schema, extraction with validation retry, Odoo matching
  odoo/       XML-RPC client and the demo dataset
evals/        datasets and runner
infra/        Terraform for AWS
data/docs/    sample warehouse procedures (the RAG corpus)
```

## License

MIT
