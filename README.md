# Signalis

**An agentic buying-signal copilot for GTM teams.** Signalis ingests CRM and
website activity, classifies each lead's buying stage with explainable,
LLM-generated reasoning, and drafts an outreach plan a rep can approve and
send — re-running automatically whenever new signals arrive.

## What it does

- Turns raw CRM rows and website events into normalized, stage-tagged signals
- Scores each lead against a marketer-defined persona and solution ICP
- Classifies buying stage (early / mid / late) with confidence + plain-language reasoning
- Drafts a 1–2 week outreach plan (touchpoints, channels, ready-to-send copy)
- Runs every campaign against its own persona and solution — not one global config
- Gates every plan behind human approval before it's considered final
- Re-classifies automatically the moment a new signal lands
- Ranks the whole pipeline by contact priority, with a reason per lead

## Architecture at a glance

| Layer | Stack |
|---|---|
| Backend | Python 3.11+, FastAPI, SQLAlchemy 2.x, SQLite, Pydantic v2 |
| Agents | LangGraph `StateGraph` coordinating 5 pipeline agents + a standalone ranking agent |
| Agent runtime | [TrueForge](https://github.com/TrueFoundryai/trueforge) — owns model calls, MCP tool discovery, and session state |
| Tool use | 4 real MCP servers: firmographic enrichment, Tavily + Exa research, Hunter.io email |
| Sandboxed execution | Buying-stage signal scoring runs generated Python in a [Daytona](https://daytona.io) sandbox |
| LLM providers | Hugging Face Inference Providers (primary) → Gemini (fallback), multi-key rotation |
| Frontend | React 18, Vite, TypeScript, Tailwind, TanStack Query, Radix-based UI kit |

Full write-ups: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) (system overview),
[docs/AGENT_GRAPH.md](docs/AGENT_GRAPH.md) (agent graph + handoffs),
[docs/DATA_SCHEMA.md](docs/DATA_SCHEMA.md) (database schema),
[docs/API.md](docs/API.md) (endpoint reference),
[docs/TIME_SAVINGS.md](docs/TIME_SAVINGS.md) (manual vs. agent time comparison),
[docs/VERIFICATION.md](docs/VERIFICATION.md) (how to see each agentic behavior in action).
`docs/DECISIONS.md` (design trade-offs and the full change history behind
them) is a local working document, intentionally excluded from the
published repo — see `.gitignore`.

## Repository layout

<details>
<summary>Expand full tree</summary>

```
backend/
├── app/
│   ├── agents/        # 5-agent pipeline + standalone ranking agent, skills/
│   ├── api/routes/    # one FastAPI router per resource (campaigns, leads, ...)
│   ├── core/          # config, TrueForge client, LLM fallback, Daytona wrapper
│   ├── mcp_tools/      # 4 remote MCP servers (enrichment, research, exa, hunter)
│   ├── db/             # session, demo seeding, additive migrations
│   ├── models/         # SQLAlchemy ORM, one module per entity
│   ├── schemas/        # Pydantic request/response schemas
│   ├── services/       # ingestion + pipeline orchestration
│   └── main.py         # app entrypoint
├── data/               # bundled sample CRM CSV + website events JSON
└── tests/              # pytest suite (unit, API, one real-LLM test)

frontend/
└── src/
    ├── api/            # typed API client
    ├── components/     # ui/ (owned component kit), leads/, dashboard/, setup/
    ├── lib/            # shared helpers (agent metadata, className merge, ...)
    ├── pages/          # the 5 application screens
    └── types/          # TypeScript types mirroring the backend schemas

docs/                   # architecture, agent graph, schema, API reference, decisions
.github/                # CI workflow, PR + issue templates
```

</details>

## Quickstart

### Prerequisites
- Python 3.11+
- Node.js 22+ and npm (required by the TrueForge agent harness)
- A [Hugging Face access token](https://huggingface.co/settings/tokens) — primary LLM provider

Everything else — Gemini, Daytona, Tavily, Exa, Hunter.io — is optional. The
app runs correctly without any of them; they just add redundancy or extra
tool calls when configured.

### 1. Configure environment

<details>
<summary>Full <code>.env</code> reference (repo root, not inside <code>backend/</code>)</summary>

```bash
HF_TOKEN=your-hugging-face-token
HF_MODEL=Qwen/Qwen3-4B-Instruct-2507:nscale
# Optional: up to two more keys — a 402/429/401/403 on one rotates to the next
HF_TOKEN_1=your-second-hugging-face-token
HF_TOKEN_2=your-third-hugging-face-token

DATABASE_URL=sqlite:///signalis.db

# Optional fallback provider, tried only if every HF key is unavailable
GEMINI_API_KEY=your-key-here
GEMINI_MODEL=gemini-3.6-flash
GEMINI_API_KEY_1=your-second-key-here

# Optional: sandboxed signal-scoring execution; falls back to a local
# computation if unset or unreachable
DAYTONA_API_KEY=your-daytona-api-key
DAYTONA_API_URL=https://app.daytona.io/api
DAYTONA_SANDBOX_ID=your-sandbox-id

# Optional MCP tool providers — each falls back to a graceful
# "not queried" result if unset or the call fails
TAVILY_API_KEY=your-tavily-api-key      # search_company_news
EXA_API_KEY=your-exa-api-key            # search_company_semantic
HUNTER_API_KEY=your-hunter-api-key      # find_email / verify_email

# TrueForge harness connection
TRUEFORGE_URL=http://localhost:8790
TRUEFORGE_ENABLED=true
TRUEFORGE_MODEL=huggingface/qwen3-4b
TRUEFORGE_MODEL_1=huggingface-2/qwen3-4b
TRUEFORGE_MODEL_2=huggingface-3/qwen3-4b
TRUEFORGE_MODEL_FALLBACK=google-gemini/gemini-3-6-flash
```

</details>

### 2. Start the agent harness + MCP servers

Each runs in its own terminal and stays running:

```bash
npx @truefoundry/trueforge@latest --port 8790  # agent harness

cd backend && source venv/bin/activate         # after step 3 creates the venv
python -m app.mcp_tools.enrichment_server      # :8791 — firmographic enrichment
python -m app.mcp_tools.research_server        # :8792 — Tavily company news
python -m app.mcp_tools.exa_server             # :8793 — Exa semantic search
python -m app.mcp_tools.hunter_server          # :8794 — email find/verify
```

None of these are hard dependencies — if TrueForge or an MCP server isn't
running, the pipeline still works, falling back to a direct HF/Gemini call
with those specific tools simply unavailable.

### 3. Backend

```bash
cd backend
python3 -m venv venv
source venv/bin/activate
pip install -e ".[dev]"                    # app + pytest + ruff
python -m app.core.trueforge_bootstrap     # registers model/sandbox/MCP providers
uvicorn app.main:app --reload
```

API at `http://localhost:8000`, interactive docs at `http://localhost:8000/docs`.
Tables are created automatically on startup.

### 4. Load sample data (recommended for a first run)

```bash
cd backend && source venv/bin/activate
python -m app.db.seed_demo
```

Seeds a default campaign (persona + solution), 20 synthetic leads, and 44
website events. Also available from the UI's Data Sources screen via
**Use Sample Data**.

### 5. Frontend

```bash
cd frontend
npm install
cp .env.example .env      # defaults to http://localhost:8000
npm run dev
```

App at `http://localhost:5173`.

## Testing & linting

```bash
cd backend
pytest -m "not integration" -q     # fast, LLM calls mocked
pytest -m integration -q           # real end-to-end LLM call (needs an API key)
ruff check app tests

cd frontend
npx eslint .
```

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for the development workflow and code
review process. This project follows the
[Contributor Covenant](CODE_OF_CONDUCT.md). Report security issues per
[SECURITY.md](SECURITY.md), not as a public issue.

## License

MIT — see [LICENSE](LICENSE).
