# honest-nip-krs-mcp

MCP server for Polish company registries. Look up any Polish company by **NIP** (tax id) or **KRS** (court registry number) from Claude Code, Claude Desktop, or any MCP-compatible AI client.

Uses two public government APIs — **no authentication required**. Data flows only between your machine and the official Polish government endpoints (Ministry of Finance + Ministry of Justice).

## Why this exists

Every Polish business, accountant, or developer working with Polish data eventually needs to:
- Verify if a client's NIP is a real, active VAT payer
- Check if a vendor's invoice can be tax-deducted (VAT status on the invoice date)
- Look up a company's full legal registry entry — board members, capital, address history
- Confirm a bank account belongs to a whitelisted vendor (to avoid split-payment penalties)

Doing this manually via web forms is tedious. This MCP lets an AI do it in one shot.

## Features (v0.1)

Two tools:

- **`lookup_by_nip`** — official **Biała Lista MF** entry. Returns: legal name, address, VAT status (Czynny / Zwolniony / Wykreślony), REGON, KRS, list of whitelisted bank accounts. Supports historical dates (up to 5 years back) — critical for tax-deductibility of past invoices.
- **`lookup_by_krs`** — full **KRS registry** entry. Returns: name, legal form, address, share capital, board members, PKD codes, shareholders. Supports both registers: `P` (Przedsiębiorcy — companies) and `S` (Stowarzyszenia — associations, foundations, NGOs).

Planned for v0.2:
- `lookup_by_regon` — GUS BIR API (requires free API key)
- `search_by_name` — fuzzy search across biała lista and KRS
- `bulk_check_accounts` — verify many bank accounts against biała lista in one call

## Requirements

- Python 3.10+
- **No API keys.** No accounts. No signup. Both APIs are fully public.

## Setup

```bash
git clone https://github.com/bartosz-kuc/honest-nip-krs-mcp.git
cd honest-nip-krs-mcp
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
```

On Windows, create the venv with `python -m venv venv` and use `venv\Scripts\pip` and `venv\Scripts\python` instead of `venv/bin/pip` and `venv/bin/python` (here and in the configs below).

Register with Claude Code:

```bash
claude mcp add pl-registries /absolute/path/to/venv/bin/python /absolute/path/to/server.py
```

Or with Claude Desktop — edit `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "pl-registries": {
      "command": "/absolute/path/to/venv/bin/python",
      "args": ["/absolute/path/to/server.py"]
    }
  }
}
```

Tools appear as `mcp__pl-registries__lookup_by_nip` etc.

## Example usage

> "Check if Google Poland is an active VAT payer."

AI calls `lookup_by_nip("5252344078")` → returns full biała lista entry with VAT status.

> "What was Orange Polska's VAT status on 2025-11-15?"

AI calls `lookup_by_nip("5260250995", date="2025-11-15")` — status as of that date, important for confirming tax deductibility of an invoice you're posting late.

> "Who's on the board of KRS 0000006042?"

AI calls `lookup_by_krs("0000006042", register="P")` → returns full registry entry with board composition.

## Data flow

```
Your AI client
     ↕  MCP stdio
This server (Python, on your machine)
     ↕  HTTPS
Public Polish gov APIs (wl-api.mf.gov.pl, api-krs.ms.gov.pl)
```

No third party in the middle. No accounts. Nothing to breach.

## Security notes

- **Nothing to leak.** No credentials, no tokens, no OAuth — this MCP has no secrets at all.
- **Rate limits.** Both APIs enforce rate limits (biała lista: 10 req/sec, KRS: similar). This client does not currently implement retry/backoff; a burst may return HTTP 429.
- **Data is public.** Anything this MCP returns is already public information you could pull manually from https://www.podatki.gov.pl/wykaz-podatnikow-vat/ or https://ekrs.ms.gov.pl/.

## Author

**Bartosz Kuć** — Warsaw-based developer, JDG owner running skanfirmy.pl (Polish company verification tools).

- Site: https://skanfirmy.pl
- GitHub: https://github.com/bartosz-kuc

- Email: firma@bartosza.pl

## Consulting

Available for consulting on Polish tax and business integrations (KSeF, GUS/NFZ/GIOŚ APIs, mBank data), MCP server design, and AI-assisted tooling for JDGs and small teams. See **[skanfirmy.pl/uslugi](https://skanfirmy.pl/uslugi)** for productized packages (audit 3k PLN, setup 8-15k PLN, retainer 2-4k PLN/mo), or reach out via email.

## License

MIT — see [LICENSE](LICENSE).

## Related

- [honest-gmail-mcp](https://github.com/bartosz-kuc/honest-gmail-mcp) — local Gmail MCP
- [honest-calendar-mcp](https://github.com/bartosz-kuc/honest-calendar-mcp) — local Google Calendar MCP
