from datetime import date

from personal_telegram_bot.tpot.topics import (
    build_completed_todos_filter,
    build_topics,
    parse_completed_todo,
    parse_waka_projects,
)


def test_waka_projects_become_ranked_topics_above_threshold():
    summary = {
        "data": [
            {
                "projects": [
                    {"name": "small", "total_seconds": 30 * 60},
                    {"name": "tpot-taste", "total_seconds": 190 * 60, "languages": [{"name": "Python"}]},
                    {"name": "dotfiles", "total_seconds": 80 * 60},
                ]
            }
        ]
    }

    topics = build_topics(parse_waka_projects(summary, min_minutes=45), completed_todos=[])

    assert [topic.source for topic in topics] == ["waka:tpot-taste", "waka:dotfiles"]
    assert topics[0].text == "working on tpot-taste: Python"
    assert topics[0].provenance == "3.2h on tpot-taste"
    assert topics[1].provenance == "1.3h on dotfiles"


def test_completed_todos_are_done_only_and_rank_after_waka():
    todo = {"id": "abc", "url": "https://notion.so/abc", "properties": {"Name": {"title": [{"plain_text": "write deployment doc"}]}}}
    topics = build_topics(
        waka_projects=[{"name": "tpot", "minutes": 70, "detail": None}],
        completed_todos=[parse_completed_todo(todo)],
    )

    assert [topic.source for topic in topics] == ["waka:tpot", "todo:abc"]
    assert topics[1].text == "completed: write deployment doc"
    assert topics[1].provenance == "✅ write deployment doc"


def test_topic_cap_is_three():
    waka = [
        {"name": "a", "minutes": 100, "detail": None},
        {"name": "b", "minutes": 90, "detail": None},
        {"name": "c", "minutes": 80, "detail": None},
        {"name": "d", "minutes": 70, "detail": None},
    ]

    assert [topic.source for topic in build_topics(waka, completed_todos=[])] == [
        "waka:a",
        "waka:b",
        "waka:c",
    ]


def test_completed_todo_filter_matches_done_only_for_the_day():
    filt = build_completed_todos_filter(date(2026, 6, 28))

    assert {"property": "Status", "status": {"equals": "Done"}} in filt["and"]
    assert "Delegated" not in str(filt)
    assert "DNF" not in str(filt)
    assert {"property": "Date", "date": {"on_or_before": "2026-06-28"}} in filt["and"]
    assert {"property": "Date", "date": {"on_or_after": "2026-06-28"}} in filt["and"]
