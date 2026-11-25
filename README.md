# Odoo AI Assistant

An LLM assistant for distribution companies that run Odoo. Staff ask questions in plain
language ("which lots expire this week and what do we do with them?", "who owes us money
for more than 15 days?") and get answers built from live Odoo data and the company's own
procedures, with follow-up questions in the same conversation. It also reads supplier
delivery notes and turns them into a receipt proposal checked against Odoo, and exposes
its Odoo tools as an **MCP server** for Claude Desktop, Cursor or any MCP client.

It runs at zero cost: the LLM is Google Gemini's free tier, Groq's free tier or a local
model with Ollama, all through the same OpenAI-compatible client. Without an Odoo server
it uses a demo dataset with the same models and fields (`stock.lot`, `stock.quant`,
`stock.picking`, `account.move`...).

## What it does

| Endpoint | What | How |
|---|---|---|
| `POST /ask` | Answers from the warehouse procedures, with citations | RAG: chunking by section, embeddings, cosine search, grounded prompt |
| `POST /agent` | Answers about stock, lots, expiry, deliveries and overdue invoices; follow-up questions with `session_id` | Tool-calling loop over Odoo (XML-RPC), read-only, conversation memory |
| `POST /extract` | Delivery note (text or PDF) to a receipt proposal | Structured output validated with Pydantic, retry on errors, matching against Odoo |
| `GET /usage` | Tokens and latency per operation | Logged on every LLM call |
| `GET /` | Small web UI for the three features | |
| MCP (`python -m app.mcp_server`) | The same Odoo tools for any MCP client | stdio or streamable HTTP, read-only annotations |

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
- **One set of tools, two consumers.** The agent and the MCP server use the same tool
  functions, the same Pydantic validation and the same error format. The MCP schemas are
  generated from the Pydantic models, so descriptions and limits (e.g. `days` 0-365)
  reach the client's model, and every tool is annotated as read-only.
- **Short conversation memory.** `/agent` keeps the last 6 question/answer pairs per
  `session_id` (1 h, least recently used sessions evicted). Tool calls and their raw
  results are not kept: follow-ups need the context, not the data, and the prompt stays
  small.
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

## MCP server

The agent's tools (products, stock per location, expiring lots, pending deliveries,
overdue invoices, procedures search) as an MCP server. The client brings its own model,
so no LLM key is needed.

```bash
pip install -e ".[mcp]"
python -m app.mcp_server              # stdio
python -m app.mcp_server --http 8001  # streamable HTTP on http://127.0.0.1:8001/mcp
```

Claude Desktop (`claude_desktop_config.json`), with the demo data:

```json
{
  "mcpServers": {
    "odoo": {
      "command": "/path/to/odoo-ai-assistant/.venv/bin/python",
      "args": ["-m", "app.mcp_server"],
      "env": {
        "EMBED_PROVIDER": "local",
        "INDEX_DIR": "/path/to/odoo-ai-assistant/data/index",
        "ODOO_MODE": "mock"
      }
    }
  }
}
```

For a real Odoo, add `ODOO_MODE=live` and the `ODOO_*` variables, with the API key of a
user that only has read access.

## Evaluations

```bash
python -m evals.run retrieval     # no LLM calls
python -m evals.run extraction    # 2 delivery notes, field accuracy
python -m evals.run agent         # 8 questions, tools used + facts in the answer
```

Results are saved in `evals/results/` to compare models, prompts or chunk sizes.

| Suite | Setup | Result |
|---|---|---|
| retrieval | offline hashing embedder | hit@3 0.93, MRR 0.89 |
| retrieval | `gemini-embedding-001` | hit@3 1.00, MRR 0.96 |
| extraction | `gemini-3.1-flash-lite`, 2 delivery notes | field accuracy 1.00 |
| agent | `gemini-flash-lite-latest`, 8 questions (incl. overdue invoices) | pass rate 1.00 (right tools + right facts) |

The hashing embedder misses the Spanish question ("¿Qué hago con un lote caducado?")
because it only matches words. Semantic embeddings handle it: that is the point of
comparing them.

The LLM rows come from the manual `Evals` workflow (free Gemini tier). The default
`gemini-flash-latest` was overloaded (503) during the run, so the client fell back to
`gemini-3.1-flash-lite`, which is what the fallback list is for.

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
  agent/      tools over Odoo, the tool-calling loop and conversation memory
  mcp_server.py  the same tools as an MCP server
  extract/    delivery note schema, extraction with validation retry, Odoo matching
  odoo/       XML-RPC client and the demo dataset
evals/        datasets and runner
infra/        Terraform for AWS
data/docs/    sample warehouse procedures (the RAG corpus)
```

## License

MIT
