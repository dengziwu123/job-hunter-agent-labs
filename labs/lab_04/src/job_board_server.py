"""Course-provided MCP server: live job openings from public ATS job boards.

Lab 3 reads one webpage you hand it. This server gives the agent something it
did not have — a configurable list of current openings at a company — and the
data is real.

Greenhouse, Lever, and Ashby all publish their customers' job boards over HTTP
with **no authentication**. This is the documented, intended use: companies rely
on it to embed their own careers pages, so reading it is not scraping and needs
no key or account.

    Greenhouse  https://boards-api.greenhouse.io/v1/boards/{company}/jobs
    Lever       https://api.lever.co/v0/postings/{company}?mode=json
    Ashby       https://api.ashbyhq.com/posting-api/job-board/{company}

Two design choices are worth reading, because they are the reason a server is
more useful here than a bare HTTP call:

1. **One stable contract over three messy sources.** Greenhouse calls the title
   `title`, Lever calls it `text`, Ashby calls it `title`; locations and
   timestamps disagree just as much. Callers should not care. `JobOpening` is
   the contract, and normalizing into it is this server's actual job.

2. **Progressive disclosure.** `list_openings` returns short records;
   `get_opening` returns one full description. A single Ashby board can be two
   megabytes of HTML — pulling all of it into context to answer "what is open?"
   would waste model context on records the task never uses. Fetch detail when
   you need it, not before.

Safety: `ats` selects from a fixed host allowlist and `company` must be a plain
slug. No caller-supplied URL is ever fetched, so this tool cannot be pointed at
an arbitrary host.
"""

from __future__ import annotations

import argparse
import html
import re
from datetime import datetime, timezone
from typing import Any, Callable

import anyio
import httpx
from mcp.server.mcpserver import MCPServer
from pydantic import BaseModel, ConfigDict

from labs.shared.artifacts import read_json
from labs.shared.config import ROOT_DIR
from labs.shared.web.web_fetch import ReadableHTMLParser, normalize_page_text


SERVER_NAME = "job_board"
SERVER_VERSION = "0.1.0"

FIXTURE_PATH = ROOT_DIR / "labs" / "lab_04" / "data" / "job_board_sample.json"
FIXTURE_COMPANIES = {
    "greenhouse": "stripe",
    "lever": "spotify",
    "ashby": "ramp",
}

# The only hosts this server will contact. `ats` picks one of these; nothing a
# caller sends can add to the list.
ATS_HOSTS = {
    "greenhouse": "https://boards-api.greenhouse.io",
    "lever": "https://api.lever.co",
    "ashby": "https://api.ashbyhq.com",
}

COMPANY_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")

REQUEST_TIMEOUT_SECONDS = 20.0
MAX_RESPONSE_BYTES = 8_000_000
# List records stay short on purpose; `get_opening` is how you ask for more.
SUMMARY_CHARS = 400


class JobOpening(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: str
    title: str
    company: str
    ats: str
    location: str
    department: str
    url: str
    published_at: str
    summary: str


class JobBoardError(RuntimeError):
    """Raised for a bad slug, an unknown ATS, or an unreachable board."""


def board_url(ats: str, company: str) -> str:
    if ats not in ATS_HOSTS:
        raise JobBoardError(f"Unknown ats {ats!r}. Use one of: {', '.join(sorted(ATS_HOSTS))}.")
    if not COMPANY_SLUG.match(company):
        raise JobBoardError(
            f"Invalid company slug {company!r}. Use the board slug, for example 'stripe'."
        )

    host = ATS_HOSTS[ats]
    if ats == "greenhouse":
        return f"{host}/v1/boards/{company}/jobs?content=true"
    if ats == "lever":
        return f"{host}/v0/postings/{company}?mode=json"
    return f"{host}/posting-api/job-board/{company}?includeCompensation=false"


def fetch_board(url: str) -> Any:
    with httpx.Client(timeout=REQUEST_TIMEOUT_SECONDS, follow_redirects=True) as client:
        response = client.get(url, headers={"Accept": "application/json"})
    if response.status_code == 404:
        raise JobBoardError("That company does not publish a board on this ATS.")
    if response.status_code >= 400:
        raise JobBoardError(f"The job board returned HTTP {response.status_code}.")
    if len(response.content) > MAX_RESPONSE_BYTES:
        raise JobBoardError("The job board response was too large to process.")
    return response.json()


def html_to_text(value: str) -> str:
    """ATS descriptions arrive as HTML, and Greenhouse escapes theirs twice."""
    parser = ReadableHTMLParser()
    parser.feed(html.unescape(value))
    return normalize_page_text("".join(parser.text_parts))


def epoch_ms_to_iso(value: Any) -> str:
    try:
        return datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc).isoformat()
    except (TypeError, ValueError):
        return ""


def normalize(payload: Any, ats: str, company: str) -> list[tuple[JobOpening, str]]:
    """Map one ATS payload into `(opening, full_description)` pairs."""
    records: list[tuple[JobOpening, str]] = []

    if ats == "greenhouse":
        for job in payload.get("jobs", []):
            body = html_to_text(job.get("content") or "")
            departments = job.get("departments") or []
            records.append(
                (
                    JobOpening(
                        job_id=str(job.get("id", "")),
                        title=(job.get("title") or "").strip(),
                        company=job.get("company_name") or company,
                        ats=ats,
                        location=(job.get("location") or {}).get("name") or "",
                        department=departments[0]["name"] if departments else "",
                        url=job.get("absolute_url") or "",
                        published_at=job.get("first_published") or job.get("updated_at") or "",
                        summary=body[:SUMMARY_CHARS],
                    ),
                    body,
                )
            )
    elif ats == "lever":
        for job in payload:
            body = job.get("descriptionPlain") or html_to_text(job.get("description") or "")
            categories = job.get("categories") or {}
            records.append(
                (
                    JobOpening(
                        job_id=str(job.get("id", "")),
                        title=(job.get("text") or "").strip(),
                        company=company,
                        ats=ats,
                        location=categories.get("location") or "",
                        department=categories.get("department") or categories.get("team") or "",
                        url=job.get("hostedUrl") or job.get("applyUrl") or "",
                        published_at=epoch_ms_to_iso(job.get("createdAt")),
                        summary=normalize_page_text(body)[:SUMMARY_CHARS],
                    ),
                    normalize_page_text(body),
                )
            )
    else:
        for job in payload.get("jobs", []):
            body = job.get("descriptionPlain") or html_to_text(job.get("descriptionHtml") or "")
            records.append(
                (
                    JobOpening(
                        job_id=str(job.get("id", "")),
                        title=(job.get("title") or "").strip(),
                        company=company,
                        ats=ats,
                        location=job.get("location") or "",
                        department=job.get("department") or job.get("team") or "",
                        url=job.get("jobUrl") or job.get("applyUrl") or "",
                        published_at=job.get("publishedAt") or "",
                        summary=normalize_page_text(body)[:SUMMARY_CHARS],
                    ),
                    normalize_page_text(body),
                )
            )

    return records


def load_fixture_payload(ats: str, company: str | None = None) -> Any:
    expected_company = FIXTURE_COMPANIES.get(ats)
    if expected_company is None:
        raise JobBoardError(f"No offline fixture for ATS {ats!r}.")
    if company is not None and company != expected_company:
        raise JobBoardError(
            f"The offline {ats} fixture represents {expected_company!r}, not {company!r}."
        )
    return read_json(FIXTURE_PATH)[ats]


def build_job_board_server(
    fetch: Callable[[str], Any] | None = None,
    offline: bool = False,
) -> MCPServer:
    """Wire the job board tools onto an MCP server.

    `fetch` is injectable so tests never touch the network; `offline` swaps in
    the bundled fixture board so the lab still runs without connectivity.
    """
    server = MCPServer(name=SERVER_NAME, version=SERVER_VERSION)

    def load(ats: str, company: str) -> list[tuple[JobOpening, str]]:
        if offline:
            payload = load_fixture_payload(ats, company)
        else:
            payload = (fetch or fetch_board)(board_url(ats, company))
        return normalize(payload, ats, company)

    @server.tool(
        name="list_openings",
        description=(
            "List the current job openings published on a company's public ATS "
            "board. Returns short records (title, location, department, url, "
            "and a short summary). Use get_opening for the full description of "
            "one posting."
        ),
    )
    def list_openings(company: str, ats: str = "greenhouse", limit: int = 20) -> list[dict]:
        records = load(ats, company)
        return [opening.model_dump() for opening, _ in records[: max(0, limit)]]

    @server.tool(
        name="get_opening",
        description=(
            "Return the full job description text for one opening, identified by "
            "the job_id returned from list_openings."
        ),
    )
    def get_opening(company: str, job_id: str, ats: str = "greenhouse") -> dict:
        for opening, body in load(ats, company):
            if opening.job_id == job_id:
                return {**opening.model_dump(), "description": body}
        raise JobBoardError(f"No opening {job_id!r} on the {company} {ats} board.")

    return server


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Lab 4 job board MCP server.")
    parser.add_argument("--offline", action="store_true", help="Serve the bundled fixture board.")
    args = parser.parse_args()

    server = build_job_board_server(offline=args.offline)
    anyio.run(server.run_stdio_async)


if __name__ == "__main__":
    main()
