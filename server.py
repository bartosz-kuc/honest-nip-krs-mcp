"""nip-krs-mcp — MCP server for Polish company registries.

Wraps two public Polish APIs (no authentication required):
- Biała Lista MF (VAT status + NIP data): https://wl-api.mf.gov.pl/
- KRS API (Krajowy Rejestr Sądowy): https://api-krs.ms.gov.pl/

Tools: lookup_by_nip, check_bank_account, lookup_by_krs. Data flows only between your
machine and the Polish government's public endpoints — no third party involved.

Sole traders and civil partnerships (entities without a KRS number) are natural persons
under the GDPR. For them lookup_by_nip returns only what a payer needs to verify a
counterparty: name, NIP, VAT status, town and the number of whitelisted bank accounts.
A specific account is verified with check_bank_account (yes/no), not by listing accounts.

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
# Stable gov.pl page of the VAT whitelist (links to the current official search engine).
MF_SEARCH_PAGE = "https://www.gov.pl/web/kas/wykaz-podatnikow-vat"

NIP_RE = re.compile(r"^\d{10}$")
KRS_RE = re.compile(r"^\d{10}$")
ACCOUNT_RE = re.compile(r"^\d{26}$")

PRIVACY_REASON = (
    "Natural person running a business (sole trader or civil partnership): data limited to what is needed "
    "to verify a counterparty (GDPR data minimisation). The full entry is published by the Ministry of "
    "Finance (officialSource)."
)

# Fields kept for entities with a KRS number. Representatives, proxies and partners are left out: the
# Ministry's API may return their PESEL numbers, and the board is in KRS (lookup_by_krs).
COMPANY_FIELDS = (
    "name", "nip", "statusVat", "regon", "krs", "workingAddress", "residenceAddress",
    "registrationLegalDate", "registrationDenialDate", "registrationDenialBasis",
    "restorationDate", "restorationBasis", "removalDate", "removalBasis",
    "accountNumbers", "hasVirtualAccounts",
)
PERSON_HIDDEN_FIELDS = (
    "accountNumbers", "residenceAddress", "workingAddress", "regon", "pesel", "registrationLegalDate",
    "registrationDenialDate", "registrationDenialBasis", "restorationDate", "restorationBasis",
    "removalDate", "removalBasis", "representatives", "authorizedClerks", "partners", "hasVirtualAccounts",
)
PEOPLE_FIELDS = ("representatives", "authorizedClerks", "partners")

# Town from an MF address: "UL. KWIATOWA 5, 00-001 WARSZAWA" or "00-001 WARSZAWA UL. KWIATOWA".
# Only after a postal code — without one we do not guess (no town is better than part of a street).
_POSTCODE_TOWN = re.compile(r"(?:^|[\s,])\d{2}-\d{3}\s+([^,\d]+?)\s*(?:,|$)")
_STREET_MARK = re.compile(
    r"\s(?:ul|al|pl|os|ulica|aleja|aleje|osiedle|plac|rondo|skwer|bulwar)\.?(?=\s|$)", re.IGNORECASE
)


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


def _validate_account(account: str) -> str:
    account = re.sub(r"[\s-]", "", account).upper().removeprefix("PL")
    if not ACCOUNT_RE.match(account):
        raise ValueError("Bank account must be 26 digits (NRB), optionally with a PL prefix")
    return account


def _is_natural_person(subject: dict) -> bool:
    # Entities without a KRS number: sole traders (JDG), civil partnerships (s.c.) and public bodies
    # outside KRS. The last group gets the same cautious answer on purpose.
    return bool(subject) and not subject.get("krs")


def _city_from_address(address: str | None) -> str | None:
    if not address:
        return None
    text = re.sub(r"\s*\n\s*", ", ", str(address)).strip()
    match = _POSTCODE_TOWN.search(text)
    if not match:
        return None
    town = _STREET_MARK.split(match.group(1))[0].strip()
    return town or None


def _minimize_subject(subject: dict) -> tuple[dict, dict | None]:
    """Whitelist of fields returned for one Ministry of Finance subject, plus a privacy note."""
    if not _is_natural_person(subject):
        out = {k: subject[k] for k in COMPANY_FIELDS if k in subject}
        omitted = [k for k in PEOPLE_FIELDS if subject.get(k)]
        if omitted:
            out["note"] = "Representatives, proxies and partners are omitted; use lookup_by_krs for the board."
        return out, None

    accounts = subject.get("accountNumbers") or []
    city = _city_from_address(subject.get("residenceAddress")) or _city_from_address(subject.get("workingAddress"))
    out = {"name": subject.get("name"), "nip": subject.get("nip"), "statusVat": subject.get("statusVat"), "krs": None}
    if city:
        out["city"] = city
    out["accountCount"] = len(accounts)
    if accounts:
        out["accountCheck"] = {
            "tool": "check_bank_account",
            "arguments": {"nip": subject.get("nip"), "account": "<26-digit account number>"},
        }
    privacy = {
        "naturalPerson": True,
        "hidden": [k for k in PERSON_HIDDEN_FIELDS if subject.get(k)],
        "reason": PRIVACY_REASON,
        "officialSource": MF_SEARCH_PAGE,
    }
    return out, privacy


def _nip_result(raw: dict, on_date: str) -> dict:
    if "error" in raw:
        return raw
    result = raw.get("result") or {}
    subject = result.get("subject")
    out = {
        "date": on_date,
        "found": bool(subject),
        "subject": None,
        "privacy": None,
        "requestId": result.get("requestId"),
        "requestDateTime": result.get("requestDateTime"),
    }
    if subject:
        out["subject"], out["privacy"] = _minimize_subject(subject)
    return out


def _wl_get(path: str, on_date: str) -> dict:
    resp = requests.get(f"{WL_BASE}{path}", params={"date": on_date}, timeout=30)
    if resp.status_code == 400:
        # API returns 400 with a friendly error body for e.g. invalid dates.
        return {"error": resp.json()}
    resp.raise_for_status()
    return resp.json()


def _wl_lookup(nip: str, on_date: str) -> dict:
    return _nip_result(_wl_get(f"/api/search/nip/{nip}", on_date), on_date)


def _wl_check_account(nip: str, account: str, on_date: str) -> dict:
    raw = _wl_get(f"/api/check/nip/{nip}/bank-account/{account}", on_date)
    if "error" in raw:
        return raw
    result = raw.get("result") or {}
    return {
        "nip": nip,
        "date": on_date,
        "accountAssigned": result.get("accountAssigned"),
        "requestId": result.get("requestId"),
        "requestDateTime": result.get("requestDateTime"),
    }


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
                "Look up a Polish entity by NIP on the Biała Lista (official Ministry of Finance VAT payer list). "
                "Companies (with a KRS number): legal name, address, VAT status (active/exempt/removed), REGON, KRS, "
                "registration and removal dates, whitelisted bank accounts. Sole traders and civil partnerships "
                "(no KRS number) are natural persons: only name, NIP, VAT status, town and the number of "
                "whitelisted accounts (GDPR data minimisation) — verify a specific account with check_bank_account. "
                "Include a date to check historical status (default: today) — this matters for tax deductibility "
                "of costs paid to that vendor."
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
            name="check_bank_account",
            description=(
                "Check on the Biała Lista whether a specific bank account is assigned to a NIP (TAK/NIE) on a given "
                "date. Use it before paying an invoice: paying a VAT payer's invoice above 15,000 PLN to an account "
                "that is not on the list has tax consequences for the payer."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "nip": {"type": "string", "description": "10-digit Polish tax id (with or without dashes/spaces)"},
                    "account": {"type": "string", "description": "26-digit account number (NRB), spaces and PL prefix allowed"},
                    "date": {"type": "string", "description": "YYYY-MM-DD, defaults to today."},
                },
                "required": ["nip", "account"],
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


def _as_text(result: dict) -> list[TextContent]:
    return [TextContent(type="text", text=json.dumps(result, ensure_ascii=False, indent=2))]


async def _call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    on_date = arguments.get("date") or date.today().isoformat()  # noqa: DTZ011

    if name == "lookup_by_nip":
        nip = _validate_nip(arguments["nip"])
        return _as_text(_wl_lookup(nip, on_date))

    if name == "check_bank_account":
        nip = _validate_nip(arguments["nip"])
        account = _validate_account(arguments["account"])
        return _as_text(_wl_check_account(nip, account, on_date))

    if name == "lookup_by_krs":
        krs = _validate_krs(arguments["krs"])
        register = arguments.get("register", "P")
        return _as_text(_krs_lookup(krs, register))

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
