"""Remote MCP server exposing firmographic enrichment tools.

Run standalone (`python -m app.mcp_tools.enrichment_server`) so TrueForge can
reach it as a `remote` MCP server over HTTP. This is a genuine tool call
boundary: agents ask for an industry classification rather than having that
lookup embedded in their own prompt or Python code.
"""
from __future__ import annotations

from mcp.server import MCPServer

server = MCPServer(
    name="signalis-enrichment",
    instructions="Firmographic enrichment tools for buying-signal analysis.",
)

_INDUSTRY_KEYWORDS: dict[str, list[str]] = {
    "SaaS": ["software", "saas", "cloud", "platform", "app"],
    "Fintech": ["bank", "capital", "finance", "payments", "fintech", "lending"],
    "Healthcare": ["health", "clinic", "medical", "pharma", "care"],
    "Retail": ["retail", "commerce", "shop", "store", "market"],
    "Manufacturing": ["manufacturing", "industrial", "factory", "supply"],
    "Education": ["education", "school", "university", "academy", "learning"],
}


@server.tool()
def classify_company_industry(company_name: str) -> dict:
    """Infer a normalized industry classification from a company name.

    Real firmographic data providers (Clearbit, ZoomInfo, etc.) do this via a
    paid lookup API; this offline heuristic stands in for that call so the
    tool boundary and its usage are real even without a paid data vendor.
    """
    lowered = company_name.lower()
    for industry, keywords in _INDUSTRY_KEYWORDS.items():
        if any(keyword in lowered for keyword in keywords):
            return {"company_name": company_name, "industry": industry, "confidence": "heuristic"}
    return {"company_name": company_name, "industry": "Unknown", "confidence": "heuristic"}


@server.tool()
def estimate_company_size_band(company_name: str, stated_size: str | None = None) -> dict:
    """Return a normalized company-size band, using the stated size if plausible."""
    valid_bands = ["1-10", "11-50", "51-200", "201-1000", "1000+"]
    if stated_size and stated_size in valid_bands:
        return {"company_name": company_name, "size_band": stated_size, "source": "stated"}
    return {"company_name": company_name, "size_band": "unknown", "source": "unavailable"}


def create_app():
    return server.streamable_http_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(create_app(), host="127.0.0.1", port=8791)
