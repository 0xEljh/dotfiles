# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "requests",
#     "notion-client",
#     "python-dotenv",
# ]
# ///
"""Land Notion "Paper Inbox" captures into the digital garden.

Queries the Paper Inbox database for rows whose Status is not "landed",
appends each as a per-paper block to the matching topic note
(content/posts/<topic-slug>.mdx, created from a template if new), downloads
sketch attachments into public/field-sketches/, commits, pushes to master —
and only then flips the row to landed. The deploy timer on sleeper-service
picks the push up and the stub goes live.

Deliberately mechanical: reaction text is copied verbatim, never rewritten
(voice charter). Rows are marked landed only after a successful push; a
failed push leaves them pending, and the content-based idempotency guard
makes the retry safe. Works in its own clone (GARDEN_BRIDGE_DIR), never the
serving checkout — digital-garden-deploy hard-resets that one.

Spec: digital-garden docs/design/paper-log-pipeline.md §5.2.
"""

import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import requests
from dotenv import load_dotenv
from notion_client import Client

current_dir = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(current_dir, ".env"))

NOTION_API_KEY = os.getenv("NOTION_TOKEN") or os.getenv("NOTION_TIME_ACCOUNTANT_SECRET")
NOTION_DATASOURCE_ID = os.getenv("NOTION_PAPER_INBOX_DATASOURCE_ID")
BRIDGE_DIR = Path(os.getenv("GARDEN_BRIDGE_DIR", "~/.local/share/garden-bridge")).expanduser()
SERVE_DIR = Path(os.getenv("GARDEN_SERVE_DIR", "~/digital-garden")).expanduser()
GARDEN_REMOTE = os.getenv("GARDEN_REMOTE")  # default: derived from SERVE_DIR's origin
SITE_URL = os.getenv("GARDEN_SITE_URL", "https://0xeljh.com").rstrip("/")

PROP_TITLE = "Title"
PROP_LINK = "Link"
PROP_TOPIC = "Topic"
PROP_REACTION = "Reaction"
PROP_SKETCH = "Sketch"
PROP_STATUS = "Status"
PROP_LANDED_URL = "Landed URL"
STATUS_LANDED = "landed"
DEFAULT_TOPIC = "misc"

ARXIV_ID_RE = re.compile(r"(?:arxiv\.org/(?:abs|pdf|html)/)?(\d{4}\.\d{4,5})(?:v\d+)?")


@dataclass
class PaperRow:
    page_id: str
    title: str
    link: str | None
    topic: str
    reaction: str
    sketches: list = field(default_factory=list)  # [(filename, url), ...]


def parse_arxiv_id(text: str | None) -> str | None:
    if not text:
        return None
    match = ARXIV_ID_RE.search(text)
    if not match:
        return None
    # A bare number inside an unrelated URL is not an arXiv id; only accept
    # bare ids when the whole string is the id, or the arxiv.org host matched.
    if "arxiv.org" not in text and match.group(0) != text.strip():
        return None
    return match.group(1)


def slugify(raw: str) -> str:
    """Mirror the site's slug rules (lib/content/categories.ts slugifyCategory)."""
    return re.sub(r"-+$|^-+", "", re.sub(r"[^a-z0-9]+", "-", raw.strip().lower()))


def topic_note_template(topic: str, today: str) -> str:
    safe_title = topic.replace('"', '\\"')
    return (
        "---\n"
        f'title: "{safe_title}"\n'
        f'date: "{today}"\n'
        'categories: ["paper log"]\n'
        "stage: sighted\n"
        "---\n"
        "\n"
        f"Tracking: {topic}.\n"
    )


def build_paper_block(row: PaperRow, arxiv_id: str | None, sketch_rel_paths: list) -> str:
    parts = [f"## {row.title}"]
    if arxiv_id:
        parts.append(f'<PaperPreview arxivId="{arxiv_id}" />')
    elif row.link:
        parts.append(f"[source]({row.link})")
    if row.reaction.strip():
        parts.append(row.reaction.strip())
    for rel in sketch_rel_paths:
        parts.append(f"![field sketch]({rel})")
    return "\n\n".join(parts)


def already_landed(note_content: str, row: PaperRow, arxiv_id: str | None) -> bool:
    if arxiv_id and arxiv_id in note_content:
        return True
    if row.link and row.link in note_content:
        return True
    return f"## {row.title}" in note_content


def _plain_text(rich: list) -> str:
    return "".join(part.get("plain_text", "") for part in rich)


def extract_row(page: dict) -> PaperRow:
    props = page.get("properties", {})
    title = _plain_text(props.get(PROP_TITLE, {}).get("title", [])) or "untitled sighting"
    link = props.get(PROP_LINK, {}).get("url")
    topic = (props.get(PROP_TOPIC, {}).get("select") or {}).get("name") or DEFAULT_TOPIC
    reaction = _plain_text(props.get(PROP_REACTION, {}).get("rich_text", []))
    sketches = []
    for f in props.get(PROP_SKETCH, {}).get("files", []):
        url = (f.get("file") or f.get("external") or {}).get("url")
        if url:
            sketches.append((f.get("name", "sketch"), url))
    return PaperRow(
        page_id=page["id"],
        title=title,
        link=link,
        topic=topic,
        reaction=reaction,
        sketches=sketches,
    )


def query_pending(notion: Client, datasource_id: str) -> list:
    """All rows not yet landed — including rows where Status was never set."""
    pending_filter = {
        "or": [
            {"property": PROP_STATUS, "select": {"does_not_equal": STATUS_LANDED}},
            {"property": PROP_STATUS, "select": {"is_empty": True}},
        ]
    }
    pages: list = []
    cursor = None
    while True:
        response = notion.data_sources.query(
            data_source_id=datasource_id,
            filter=pending_filter,
            start_cursor=cursor,
        )
        pages.extend(response.get("results", []))
        if not response.get("has_more"):
            return pages
        cursor = response.get("next_cursor")


def mark_landed(notion, page_id: str, landed_url: str) -> None:
    notion.pages.update(
        page_id=page_id,
        properties={
            PROP_STATUS: {"select": {"name": STATUS_LANDED}},
            PROP_LANDED_URL: {"url": landed_url},
        },
    )


def download_file(url: str, dest: Path) -> None:
    """Notion file URLs expire in ~1h — callers must download immediately."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    response = requests.get(url, timeout=60)
    response.raise_for_status()
    dest.write_bytes(response.content)


def land_row(row: PaperRow, repo_dir: Path, site_url: str, today: str) -> str | None:
    """Append the row to its topic note. Returns the live URL, or None if skipped."""
    topic_slug = slugify(row.topic) or DEFAULT_TOPIC
    note_path = repo_dir / "content" / "posts" / f"{topic_slug}.mdx"
    arxiv_id = parse_arxiv_id(row.link)

    if note_path.exists():
        content = note_path.read_text()
        if already_landed(content, row, arxiv_id):
            print(f"skip (already landed): {row.title}")
            return None
    else:
        content = topic_note_template(row.topic, today)

    sketch_base = arxiv_id or slugify(row.title)[:40] or "sketch"
    sketch_rel_paths = []
    for i, (name, url) in enumerate(row.sketches, start=1):
        ext = Path(name).suffix.lower() or ".png"
        rel = f"/field-sketches/{topic_slug}/{sketch_base}-{i}{ext}"
        download_file(url, repo_dir / "public" / rel.lstrip("/"))
        sketch_rel_paths.append(rel)

    block = build_paper_block(row, arxiv_id, sketch_rel_paths)
    note_path.parent.mkdir(parents=True, exist_ok=True)
    note_path.write_text(content.rstrip("\n") + "\n\n" + block + "\n")
    return f"{site_url}/posts/{topic_slug}"


def run_git(args: list, cwd: Path | None) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True)


def derive_remote() -> str:
    if GARDEN_REMOTE:
        return GARDEN_REMOTE
    result = subprocess.run(
        ["git", "-C", str(SERVE_DIR), "remote", "get-url", "origin"],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def ensure_clone(bridge_dir: Path, remote: str) -> None:
    if (bridge_dir / ".git").exists():
        run_git(["fetch", "origin"], cwd=bridge_dir)
        run_git(["checkout", "-f", "master"], cwd=bridge_dir)
        run_git(["reset", "--hard", "origin/master"], cwd=bridge_dir)
    else:
        bridge_dir.parent.mkdir(parents=True, exist_ok=True)
        run_git(["clone", "--branch", "master", remote, str(bridge_dir)], cwd=None)


def main() -> int:
    if not NOTION_API_KEY or not NOTION_DATASOURCE_ID:
        print("NOTION_TOKEN / NOTION_PAPER_INBOX_DATASOURCE_ID not configured; nothing to do")
        return 0

    notion = Client(auth=NOTION_API_KEY)
    pages = query_pending(notion, NOTION_DATASOURCE_ID)
    if not pages:
        print("paper inbox empty")
        return 0

    ensure_clone(BRIDGE_DIR, derive_remote())
    today = date.today().isoformat()

    landed: list = []  # (row, landed_url)
    failed: list = []
    for page in pages:
        row = extract_row(page)
        try:
            landed_url = land_row(row, BRIDGE_DIR, SITE_URL, today)
        except Exception as exc:  # noqa: BLE001 — one bad row must not block the rest
            print(f"failed to land {row.title!r}: {exc}")
            failed.append(row.title)
            continue
        if landed_url is None:
            # Content already present (e.g. retry after a failed push that
            # landed on a later run) — safe to mark landed now.
            landed.append((row, f"{SITE_URL}/posts/{slugify(row.topic) or DEFAULT_TOPIC}"))
            continue
        run_git(["add", "-A"], cwd=BRIDGE_DIR)
        run_git(["commit", "-m", f"log: sighting — {row.title}"], cwd=BRIDGE_DIR)
        landed.append((row, landed_url))
        print(f"landed: {row.title} -> {landed_url}")

    if landed:
        run_git(["push", "origin", "master"], cwd=BRIDGE_DIR)
        for row, landed_url in landed:
            try:
                mark_landed(notion, row.page_id, landed_url)
            except Exception as exc:  # noqa: BLE001
                print(f"failed to mark landed {row.title!r}: {exc}")
                failed.append(row.title)

    print(f"done: {len(landed)} landed, {len(failed)} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
