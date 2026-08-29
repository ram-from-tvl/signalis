# Expanding Signalis: Real MCP Servers & Tool Integrations for a B2B Buying-Signal + Outreach Agent

> **Status note:** this is the research memo that led to the work below —
> kept as-is rather than edited after the fact, since it's a point-in-time
> research artifact. Of Phase 1's three recommendations, **Exa MCP** and
> **Hunter.io MCP** are now shipped (`app/mcp_tools/exa_server.py`,
> `app/mcp_tools/hunter_server.py`, attached to Persona Fit and Outreach
> Planner respectively — see `docs/AGENT_GRAPH.md`). Apollo.io, and every
> item in Phase 2/3, remain unimplemented and still describe genuine
> future work.

## TL;DR
- **The MCP ecosystem is now mature enough to make Signalis feel like a real product:** first-party, OAuth-based remote MCP servers now exist from HubSpot (GA April 2026), Salesforce, Attio, Pipedrive (GA June 2026), Apollo.io, Hunter.io, Slack, Exa, and Tavily — most connect to TrueForge as remote servers with header-auth or OAuth, and several have genuine free tiers.
- **The highest "wow-per-friction" additions are:** Apollo.io (free-tier prospecting + enrichment + sequences), Hunter.io (free email discovery/verification), Exa (neural/semantic search with recurring free credits), Attio (free, fully OAuth CRM read/write), Tavily (already integrated), and an aggregator (Composio or Klavis) to add Gmail/Calendar/Slack breadth via one endpoint.
- **The main risk isn't availability — it's tool-schema context bloat and cross-server security.** Connecting many MCP servers floods the context window and creates tool-name collisions and tool-poisoning exposure; TrueForge directly mitigates this with deferred (lazy) tool loading, subagent isolation, and annotation-driven approval gates, which you should lean on hard.

## Key Findings

### 1. First-party CRM MCP servers have arrived and are the strongest additions
The single biggest change in 2025–2026 is that the major CRMs now ship their own hosted, remote MCP servers with OAuth — no self-hosting, no API-key juggling:
- **HubSpot** — official remote CRM MCP server at `mcp.hubspot.com`, GA April 13, 2026, OAuth-based, plus a one-click Claude connector and a self-hosted `@hubspot/mcp-server` npm package. Free to connect (uses your HubSpot plan/permissions). Covers contacts, companies, deals, tickets, engagements, and associations.
- **Attio** — official hosted server at `mcp.attio.com/mcp`, OAuth, available on all plans including free; reads auto-approved, writes ask for confirmation. This is the cleanest "just works" CRM for a demo.
- **Pipedrive** — official remote server at `mcp.pipedrive.ai/mcp`, GA June 30, 2026, OAuth, every plan, respects existing user permissions.
- **Salesforce** — Hosted MCP Servers GA April 2026 (mcp for Agentforce 360 Platform, Data 360, Tableau Next), but restricted to Enterprise Edition org and above; OAuth + PKCE; effectively sales-gated for a hackathon.

### 2. Sales-intelligence / enrichment MCP servers — several with real free tiers
- **Apollo.io** — official remote server at `mcp.apollo.io/mcp` (OAuth via connectors in Claude/ChatGPT/Perplexity, works on any plan including the free tier); many community servers exist too (API-key, MIT-licensed, 27–45 tools). Apollo now markets a **275M+ contact / 73M-company database with 65+ filters** (2026). People search is free; enrichment draws credits. This is the standout for "buying signal + outreach."
- **Hunter.io** — official remote MCP server, works on all plans including the free plan (50 credits/month); API-key (X-API-Key header) or OAuth. Email discovery (Domain Search, Email Finder), verification, and enrichment.
- **People Data Labs, RocketReach, Wiza, Clay** — community/first-party servers exist. Clay has an official hosted MCP (`api.clay.com/v3/mcp`, OAuth, 150+ data providers) but is expensive (Launch ~$185/mo; free plan only 100 credits/mo).
- **Clearbit / ZoomInfo** — no meaningful free tier; ZoomInfo is sales-gated enterprise pricing. Avoid for a demo.

### 3. Search / news / intent MCP servers — cheapest breadth for "signal extraction"
- **Tavily** (already integrated) — official MCP; the free "Researcher" plan gives **1,000 API credits every month, no credit card required** (basic search 1 credit, advanced 2 credits, overage $0.008/credit). Search + extract + crawl + map in one server; native integrations across LangChain/LlamaIndex/CrewAI.
- **Exa** — official MCP, neural/semantic search with dedicated people/company/news indexes; per official billing docs, **new accounts receive $20 in signup credits and Free Tier accounts receive $10 in free credits each month with no payment method required** (Standard Search $7/1,000 requests; monthly credits don't roll over), plus an unauthenticated MCP free tier. Strong for company/person research.
- **Brave Search** — official reference MCP server (`@modelcontextprotocol/server-brave-search`), API-key, has a free tier.

### 4. Communication / scheduling MCP servers
- **Slack** — official remote server at `mcp.slack.com`, OAuth, admin-approved; the server itself is free to use (some capabilities like canvases require a paid Slack plan). Slack also shipped a Slackbot MCP *client* that reaches out to partner app servers.
- **Google Workspace (Gmail + Calendar)** — Google publishes an official Workspace MCP server (single OAuth flow across Drive/Docs/Sheets/Gmail/Calendar), but it's gated/waitlisted; numerous community servers exist requiring a Google Cloud project + OAuth client setup.
- **Calendly** — available via aggregators (Composio/Zapier/Pipedream).

### 5. Aggregators / marketplaces — the fastest way to add breadth
- **Composio** — 1,000+ toolkits (Salesforce, HubSpot, Gmail, Slack, Pipedrive, Tavily, etc.), managed OAuth/auth, one hosted MCP endpoint ("Tool Router") with **context-aware tool loading** (loads only task-relevant tools rather than dumping every schema), SOC 2/ISO 27001, free to start. Explicitly markets a Sales & Revenue bundle ("enrich a lead, draft the follow-up, book the meeting across Salesforce, HubSpot, Gmail, LinkedIn, and 1,000+ more").
- **Klavis AI** (YC X25) — hosted production MCP servers across 600+ tools with built-in OAuth + multi-tenancy; open-source Strata server for self-hosting; freemium (paid tiers ~$70–90/mo).
- **Zapier MCP** — per Zapier's official docs, connects AI clients to **9,000+ apps and 40,000+ actions**; dynamic MCP endpoint; free plan = 100 tasks/month, and **each MCP tool call uses two tasks** from your quota. Paid from $19.99/mo.
- **Pipedream MCP** and **Apify** (wraps Tavily, Hunter, and scraper actors) — additional options.
- **Anthropic Claude Connectors Directory** — Anthropic's docs distinguish Anthropic-**verified** connectors (functionally tested) from **community** connectors; community trackers count ~414–439 verified across ~30 categories (updated weekly), and roughly **1,625 MCP integrations across both catalog surfaces plus ~72 held pending vendor verification**. Useful as a trust-vetted shopping list, since acceptance requires a public privacy policy and correct read-only/destructive tool annotations.

### 6. LinkedIn is the notable trap
There is **no official LinkedIn MCP server**. LinkedIn's API only covers posts/pages/ads (partner-gated to incorporated companies in approved categories); its User Agreement bans scraping and third-party automation. Community "LinkedIn MCP" servers mostly scrape via your `li_at` session cookie — this breaches ToS and risks account restriction/termination (LinkedIn's anti-bot systems actively detect cookie sessions). For LinkedIn-type data, use a people-data layer (Apollo, PDL, Clay/Derrick, Lessie) that aggregates public professional data server-side rather than scraping your account. Official OAuth-based servers exist only for company pages/posts/ads and (via partners like MCPBundles) Sales Navigator.

### 7. TrueForge integration specifics (your runtime)
- **Multiple remote MCP servers: fully supported.** You register each server once under **Settings → Connectors** (UI "Add MCP Server" by URL, showing its auth type: OAuth / API key / none), then each agent attaches any number by name in the agent spec's `mcp_servers[]` array. Auth per server is none / API-key (header auth) / OAuth (with in-chat authorization). Because agents reference servers by name, your existing enrichment + research MCP servers coexist cleanly with new ones. Note: OAuth requires `PUBLIC_BASE_URL` set so the server can build MCP OAuth callbacks.
- **Deferred tool loading is native and is your main defense against context bloat.** Per the official docs: "By default (`preload: false`), each attached MCP server contributes only its `name` and `description`; individual tool schemas are discovered on demand." Turn `preload` on only for small, frequently-used servers; `preload_tools` can eager-load specific tools while the rest stay deferred.
- **Per-server tool scoping fields:** `enable_tools` (`@all`, `@read-only`, or literal names), `disable_tools`, `preload`, `preload_tools`, and `require_approval_for_tools` (default `["@write","@destructive"]`).
- **Approval is annotation-driven:** the harness pauses before a call, shows tool name + arguments, and resumes on Allow/Deny. The `@read-only`/`@write`/`@destructive` selectors resolve from the MCP server's `readOnlyHint`/`destructiveHint` annotations (`@read-only`→`readOnlyHint: true`, `@destructive`→`destructiveHint: true`). **Caveat:** GitHub Issue #318 reports that in **Code Mode**, the destructive gate can "fail open" on tools that publish *no* annotations — so pin approvals explicitly by tool name for anything irreversible.
- **Provider fallback is NOT a native harness feature.** TrueForge supports per-agent/per-session model *switching* (single `provider/model` FQN, e.g. `google/gemini-2.5-flash`), but there is no documented `fallbacks` array in the agent spec. Automatic failover lives in the **TrueFoundry AI Gateway** ("Every provider behind one endpoint, with model-level RBAC, budgets, routing and fallbacks") or the announced early-access `truefailover` add-on. Your Gemini-primary / Qwen-fallback design should be implemented by pointing TrueForge at a gateway/OpenAI-compatible endpoint, not expected from the harness alone.
- **Subagents (native, `dynamic_sub_agents` default on)** run with fresh, isolated context and return only final results — ideal for fanning out per-account enrichment/research without polluting the main context (docs example: 10 PRs → 10 parallel subagents → 10 short summaries). **Skills** are git-backed `SKILL.md` packs loaded via progressive disclosure (only name+description in context until the agent decides the skill is relevant) and require the sandbox enabled.
- **Context engineering extras** you already benefit from: automatic compaction (default at 80% of the model's context length, falling back to ~50,000 tokens), large-result offloading to sandbox files, and Code Mode.

### 8. Integration risks & gotchas
- **Context/schema bloat:** each tool definition costs ~200–500 tokens; 5 servers × 30 tools can burn 30,000–60,000 tokens before the first prompt. Microsoft Research's analysis of 1,470 MCP servers ("Tool-space interference in the MCP era") found **large tool spaces can lower performance by up to 85% for some models**; OpenAI caps developers at 128 tools and recommends fewer than 20 functions at once. Mitigate with deferred loading, per-agent tool scoping, and aggregator-side selective loading.
- **Tool-name collisions:** identical tool names across servers (e.g., two `search` or `send_email` tools) cause misrouting and hallucinated tool names; peer-reviewed work confirms hosts maintain a unified tool list and may invoke the wrong server's tool even when the LLM picks correctly.
- **Tool poisoning / prompt injection:** malicious or compromised servers can embed hidden instructions in tool descriptions/responses. With multiple servers connected, a malicious one can override or intercept calls to trusted servers and exfiltrate data they can reach ("tool shadowing"/confused-deputy), and OWASP documents the connect-time-vs-runtime trust gap. Prefer official/verified servers, keep human approval on writes, and don't mix untrusted web content with action-capable tools in the same context.
- **Reliability of community servers:** many are underdeveloped personal projects; Pipedrive's most-starred community server is read-only, Dynamics 365 has a 5-tool stub, Zoho has no production server, etc. Favor first-party servers where they now exist. Real-fault taxonomies also flag Windows/platform incompatibilities as a common failure mode.
- **Auth/session management:** OAuth across many servers means token storage/refresh per user; aggregators (Composio/Klavis) or the TrueFoundry gateway centralize this. Clay, notably, is OAuth-only for MCP with no static-credential path.
- **Rate limits & credit burn:** enrichment/search credits deplete fast; Zapier MCP calls consume 2 tasks each and agent retry loops can blow through quotas.

## Details

### Cost & auth cheat-sheet (hackathon lens)

**Genuinely free / free-tier usable now:**
- Tavily (1,000 credits/mo free), Exa ($20 signup + $10/mo recurring credits + unauthenticated MCP tier), Brave Search (free tier)
- Apollo.io (free plan; people search free, enrichment uses credits), Hunter.io (free plan, 50 credits/mo)
- Attio (free on all plans, OAuth), HubSpot (free to connect to a free HubSpot account), Pipedrive (all plans)
- Slack official (free), Google Workspace community servers (free but setup-heavy)
- Composio (free to start), Klavis (freemium), Zapier MCP (100 tasks/mo free)

**Paid but cheap:** Zapier Pro ($19.99/mo). **Skip for demo:** Clay Launch (~$185/mo).

**Sales-gated / avoid for demo:** ZoomInfo, Salesforce (Enterprise org+), Clearbit, official LinkedIn API (partner-gated).

**Auth complexity (easiest → hardest):** API-key (Tavily, Exa, Brave, Hunter, community Apollo) < remote OAuth (HubSpot, Attio, Pipedrive, Apollo official, Slack) < Google Cloud OAuth project setup (Gmail/Calendar community) < enterprise OAuth+PKCE (Salesforce).

## Recommendations

### Prioritized "wow-factor + low-friction" additions (pick 5–8)
Staged so each phase adds a visible capability with minimal auth/cost pain.

**Phase 1 — Signal + prospect breadth (all free, mostly API-key):**
1. **Exa MCP** — semantic company/person/news search with dedicated company & people indexes; the single best complement to your existing Tavily research server for "signal extraction." Recurring free credits + unauthenticated tier. API-key.
2. **Apollo.io MCP** — the marquee addition: 275M-contact prospecting, persona/firmographic search (free), enrichment (credits), and email sequences. Directly powers Persona Fit + Outreach Planner. Official OAuth server or community API-key server.
3. **Hunter.io MCP** — email finding + verification to make outreach contacts real and deliverable. Free plan, API-key.

**Phase 2 — CRM + outreach action (OAuth, still free):**
4. **Attio MCP** — the lowest-friction real CRM: hosted, OAuth, free, read/write with built-in write-confirmation. Lets Signalis write scored accounts, log signals, and create outreach tasks — huge demo credibility. (Swap in **HubSpot MCP** if your audience is HubSpot-centric.)
5. **Slack MCP** (official) or **Gmail** — deliver the outreach plan / signal alerts into a channel or draft emails. Slack is far less setup than Gmail's Cloud-project OAuth.

**Phase 3 — Breadth multiplier (one aggregator):**
6. **Composio (or Klavis) MCP** — one endpoint that unlocks Salesforce/HubSpot/Pipedrive/Calendly/Gmail/Teams and 1,000+ more with managed OAuth and context-aware tool loading. This is what makes the product feel "connectable to anything" without wiring each server. Use its selective tool loading to avoid bloat.

**Optional flex:** **Calendly** (book the meeting the outreach plan proposes) and **Brave Search** (cheap web-search fallback for your provider-diversity story).

### Implementation guidance on TrueForge
- Register each new server under Settings → Connectors; attach per-agent via `mcp_servers[]`. Give each Signalis agent only the servers it needs (Signal Extraction → Exa/Tavily; Persona Fit → Apollo/Hunter; Buying Stage / Outreach Planner → Apollo/Slack/Attio; Prioritization → your Daytona scoring sandbox).
- **Keep `preload: false`** everywhere and rely on deferred loading; only preload tiny high-frequency servers. This is your primary bloat mitigation across a large tool surface.
- **Namespace against collisions:** with Tavily + Exa + Apollo all exposing "search," scope `enable_tools` to specific tool names per agent rather than `@all`.
- **Lean on approval gates:** keep `require_approval_for_tools` covering writes/sends (CRM writes, sequence enrollment, email/Slack sends); pin irreversible tools by explicit name given the Code-Mode fail-open issue. This maps cleanly onto your existing plan/classification and `require_approval_for_tools` checkpoints.
- **Use subagents** for per-account fan-out (enrich + research + score N accounts in parallel), returning only compact results to the ranking agent — keeps the orchestrator context clean.
- **For Gemini→Qwen fallback**, route model calls through a TrueFoundry AI Gateway endpoint (or equivalent OpenAI-compatible proxy) rather than expecting harness-native failover.

### Benchmarks that would change the plan
- If tool count per agent exceeds ~low-dozens, or MCP schemas consume a double-digit share of `/context`, consolidate behind an aggregator or split into more subagents.
- If enrichment credit burn becomes limiting in testing, cache enrichment results (you already have Daytona/SQLite) and gate enrichment behind ranking so only top-priority accounts get enriched.
- If a community server proves flaky, prefer the first-party equivalent (now available for HubSpot/Attio/Pipedrive/Apollo/Hunter).

## Caveats
- **Dates and GA claims** (HubSpot April 2026, Pipedrive June 2026, Salesforce April 2026) come from vendor blogs and secondary trackers; verify current endpoints/terms before relying on them, as the ecosystem changes weekly.
- **Free-tier limits change frequently** — figures here (Tavily 1,000 credits, Hunter 50 credits, Exa $20 + $10/mo) reflect mid-2026 reporting and should be confirmed at signup.
- **TrueForge MCP catalog YAML schema** could not be fully verified against official docs; the confirmed path is the UI "Add MCP Server" + Connectors + agent-spec `mcp_servers[]`. Some YAML field lists circulating online belong to the TrueFoundry AI Gateway / IBM ContextForge, not the harness itself.
- **`truefailover` provider-fallback add-on** was described with early-access/future framing; treat it as announced, not guaranteed-shipped. Model fallback is a Gateway concern, not a harness feature.
- **LinkedIn scraping servers**, LinkedIn data via cookie sessions, and any "unofficial endpoint" servers carry ToS/account-ban and reliability risk — recommended against for a real product.
- **Security posture:** connecting third-party MCP servers widens your attack surface (tool poisoning, prompt injection, credential exposure). For anything beyond a demo, add a vetting/allowlist step (or route through Composio/Klavis/the TrueFoundry gateway) and keep untrusted-content processing separate from action-capable tools.