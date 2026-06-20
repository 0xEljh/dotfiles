"""Write a Notion "day page" from independent contributions.

The Time Accounting day page (one row per calendar date) is a shared canvas:
several signals write to it — desktop ActivityWatch, sleep, and later phone and
location. Each signal is a *contributor* returning a `Contribution`. The writer
ensures the page exists ONCE, then applies every contribution independently, so
one empty or failing signal never blocks the others. That independence is what
lets sleep (and any non-desktop signal) log on a day with no computer activity —
the blind spot that motivated the restructure.

Pure stdlib; the AW-specific helpers (page lookup, block rendering) are injected
by the caller so this module stays free of `aw_notion_sync` imports.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

# When two contributors propose a select value for the same hour, the higher
# priority wins; an hour already filled on the page is never overwritten. Work
# classification (you were demonstrably active) outranks the sleep overlay.
PRIORITY_WORK = 20
PRIORITY_BIO = 10


@dataclass
class Contribution:
    # hour (0-23) -> (select value, priority)
    hour_tags: dict[int, tuple[str, int]] = field(default_factory=dict)
    # page number-property name -> value (each applied best-effort, in isolation)
    number_props: dict[str, float] = field(default_factory=dict)
    # Notion blocks appended to the page body, in contributor order
    blocks: list = field(default_factory=list)

    @classmethod
    def empty(cls) -> "Contribution":
        return cls()


def _hour_prop(hour: int) -> str:
    return f"{hour:02d}:00"


def merge_hour_tags(
    select_updates: dict,
    chosen: dict,
    hour_tags: dict,
    existing_props: dict,
) -> None:
    """Merge one contributor's hour_tags into `select_updates`, respecting
    priority (higher wins) and never overwriting an hour already set on the page.
    `chosen` carries the running winner per hour across contributors, so the
    result is independent of contributor order."""
    for hour, (value, priority) in hour_tags.items():
        prop_name = _hour_prop(hour)
        current = (existing_props.get(prop_name) or {}).get("select")
        if current and current.get("name"):
            continue  # respect a value already on the page (manual or prior run)
        prev = chosen.get(hour)
        if prev is not None and priority <= prev[1]:
            continue
        chosen[hour] = (value, priority)
        select_updates[prop_name] = {"select": {"name": value}}


def write_day_page(
    notion,
    date_str: str,
    contributors: list[Callable[[str, dict], Optional[Contribution]]],
    *,
    ensure_page: Callable[[object, str], str],
    replace_blocks: Callable[[object, str, list], None],
    retrieve_props: Optional[Callable[[object, str], dict]] = None,
) -> str:
    """Ensure the day page exists, run each contributor independently, and apply
    the merged result. Returns the page id. A contributor that raises or returns
    None is skipped — the rest still apply."""
    page_id = ensure_page(notion, date_str)

    if retrieve_props is not None:
        existing_props = retrieve_props(notion, page_id)
    else:
        try:
            existing_props = notion.pages.retrieve(page_id=page_id).get("properties", {})
        except Exception as exc:
            print(f"[day-page] could not retrieve properties for {date_str}: {exc}")
            existing_props = {}

    select_updates: dict = {}
    chosen: dict = {}
    number_props: dict = {}
    blocks: list = []

    for contribute in contributors:
        name = getattr(contribute, "__name__", repr(contribute))
        try:
            contribution = contribute(date_str, existing_props)
        except Exception as exc:
            print(
                f"[day-page] contributor {name} failed for {date_str}: "
                f"{type(exc).__name__}: {exc}"
            )
            continue
        if contribution is None:
            continue
        merge_hour_tags(select_updates, chosen, contribution.hour_tags, existing_props)
        number_props.update(contribution.number_props)  # later contributor wins on dup name
        blocks.extend(contribution.blocks)

    if select_updates:
        notion.pages.update(page_id=page_id, properties=select_updates)
        print(f"[day-page] set {len(select_updates)} hourly tags: {sorted(select_updates)}")

    # Number properties one at a time: a property that does not exist in the DB
    # must not sink the others — nor the hourly tags already applied above.
    for prop_name, value in number_props.items():
        try:
            notion.pages.update(page_id=page_id, properties={prop_name: {"number": value}})
            print(f"[day-page] set {prop_name} = {value}")
        except Exception as exc:
            print(
                f"[day-page] skipped {prop_name!r} (add a number property by that "
                f"name to the database): {str(exc)[:120]}"
            )

    # Render blocks only when a contributor produced some. An empty list means
    # "nothing to render" (e.g. a no-AW day), NOT "clear the page".
    if blocks:
        replace_blocks(notion, page_id, blocks)

    return page_id
