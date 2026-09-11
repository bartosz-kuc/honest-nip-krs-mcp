"""nip-krs-mcp — MCP server for Polish company registries.

Wraps two public Polish APIs (no authentication required):
- Biała Lista MF (VAT status + NIP data): https://wl-api.mf.gov.pl/
- KRS API (Krajowy Rejestr Sądowy): https://api-krs.ms.gov.pl/

Tools: lookup_by_nip, lookup_by_krs. Data flows only between your machine
and the Polish government's public endpoints — no third party involved.

Author: Bartosz Kuć <firma@bartosza.pl>
Repo:   https://github.com/bartosz-kuc/honest-nip-krs-mcp
License: MIT
"""

import asyncio
import json
import re
from datetime import date
from typing import Any

import requests
from mcp import types
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import CallToolResult, ListToolsResult, TextContent, Tool

WL_BASE = "https://wl-api.mf.gov.pl"
KRS_BASE = "https://api-krs.ms.gov.pl/api/krs"

NIP_RE = re.compile(r"^\d{10}$")
KRS_RE = re.compile(r"^\d{10}$")


def _validate_nip(nip: str) -> str:
    nip = nip.replace("-", "").replace(" ", "")
    if not NIP_RE.match(nip):
        raise ValueError(f"NIP must be exactly 10 digits, got {nip!r}")
    return nip


def _validate_krs(krs: str) -> str:
    krs = krs.strip()
    if not KRS_RE.match(krs):
        raise ValueError(f"KRS must be exactly 10 digits, got {krs!r}")
    return krs


def _wl_lookup(nip: str, on_date: str) -> dict:
    url = f"{WL_BASE}/api/search/nip/{nip}"
    resp = requests.get(url, params={"date": on_date}, timeout=30)
    if resp.status_code == 400:
        # API returns 400 with a friendly error body for e.g. invalid dates.
        return {"error": resp.json(), "url": resp.url}
    resp.raise_for_status()
    return resp.json()


def _krs_lookup(krs: str, register: str) -> dict:
    # register: "P" = Przedsiębiorcy (businesses), "S" = Stowarzyszenia (associations/NGOs)
    url = f"{KRS_BASE}/OdpisAktualny/{krs}"
    resp = requests.get(url, params={"rejestr": register, "format": "json"}, timeout=30)
    if resp.status_code == 404:
        return {"error": f"KRS {krs} not found in register {register}", "status": 404}
    resp.raise_for_status()
    return resp.json()


server = Server("pl-registries")


async def _list_tools() -> list[Tool]:
    return [
        Tool(
            name="lookup_by_nip",
            description=(
                "Look up a Polish company by NIP on the Biała Lista (official Ministry of Finance VAT payer list). "
                "Returns: legal name, address, VAT status (active/exempt/removed), REGON, KRS, list of confirmed "
                "bank accounts. Include a date to check historical status (default: today) — this matters for "
                "tax deductibility of costs paid to that vendor."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "nip": {"type": "string", "description": "10-digit Polish tax id (with or without dashes/spaces)"},
                    "date": {"type": "string", "description": "YYYY-MM-DD, defaults to today. API supports dates up to 5 years back."},
                },
                "required": ["nip"],
            },
        ),
        Tool(
            name="lookup_by_krs",
            description=(
                "Look up a Polish organization by KRS number in the Krajowy Rejestr Sądowy. Returns the full "
                "current registry entry: name, legal form, address, share capital, board members, PKD codes, "
                "shareholders (for companies), registration/change dates."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "krs": {"type": "string", "description": "10-digit KRS number"},
                    "register": {
                        "type": "string",
                        "enum": ["P", "S"],
                        "default": "P",
                        "description": "P = Przedsiębiorcy (businesses/companies), S = Stowarzyszenia (associations/NGOs/foundations)",
                    },
                },
                "required": ["krs"],
            },
        ),
    ]


async def _call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    if name == "lookup_by_nip":
        nip = _validate_nip(arguments["nip"])
        on_date = arguments.get("date") or date.today().isoformat()  # noqa: DTZ011
        result = _wl_lookup(nip, on_date)
        return [TextContent(type="text", text=json.dumps(result, ensure_ascii=False, indent=2))]

    if name == "lookup_by_krs":
        krs = _validate_krs(arguments["krs"])
        register = arguments.get("register", "P")
        result = _krs_lookup(krs, register)
        return [TextContent(type="text", text=json.dumps(result, ensure_ascii=False, indent=2))]

    raise ValueError(f"Unknown tool: {name}")


async def on_list_tools(ctx, params) -> ListToolsResult:
    return ListToolsResult(tools=await _list_tools())


async def on_call_tool(ctx, params) -> CallToolResult:
    return CallToolResult(content=await _call_tool(params.name, params.arguments or {}))


server.add_request_handler("tools/list", types.PaginatedRequestParams, on_list_tools)
server.add_request_handler("tools/call", types.CallToolRequestParams, on_call_tool)


async def main():
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


def sync_main():
    """Sync entry point for console script."""
    asyncio.run(main())


if __name__ == "__main__":
    sync_main()
