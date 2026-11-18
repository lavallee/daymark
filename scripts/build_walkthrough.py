"""Rebuild the offline walkthrough from a synthetic workspace using Daymark's public verbs."""

import argparse
from html import escape
from pathlib import Path

from daymark import Store, Tasks
from daymark.render import agenda_text, listing

PAGE = Path(__file__).resolve().parents[1] / "docs/index.html"
BEGIN = "<!-- daymark:walkthrough -->"
END = "<!-- /daymark:walkthrough -->"


def situations(store):
    """Render three real reads of the same afternoon, with the same commitments."""
    tasks = Tasks(store, "studio", actor="coordinator", timezone="UTC", now="2030-05-01T12:00:00Z")
    tasks.add("Send the print files", id="files", due="today", minutes=45, move="person")
    tasks.add("Approve the cover", id="cover", minutes=5, move="answer")
    tasks.add("Check the room list", id="room", minutes=10, move="person")
    head = store.head("studio")
    short = tasks.advise(profile="plain", settings=["time-fit.minutes=10"])
    options = [
        ("all", "Everything ready", tasks.ready(), "daymark ready", []),
        (
            "quick",
            "I have ten minutes",
            short.picks,
            "daymark advise --profile plain --set time-fit.minutes=10",
            short.notes,
        ),
        (
            "decide",
            "I can make a decision",
            tasks.ready(move="answer"),
            "daymark ready --move answer",
            [],
        ),
    ]
    assert head == store.head("studio") and tasks.profile()["version"] == 0
    out = [
        '<section aria-labelledby="situation"><h2 id="situation">What fits this afternoon?</h2>',
        ("<p>You have print files due today, a cover to approve, and a room list to check. "
        "An agent can answer each request from the same three commitments.</p>"),
        '<fieldset class="situations"><legend>Choose what to ask your agent</legend>',
    ]
    for i, (key, label, *_rest) in enumerate(options):
        out.append(
            f'<input type="radio" name="situation" id="choose-{key}"'
            f' aria-controls="result-{key}"{" checked" if i == 0 else ""}>'
            f'<label for="choose-{key}">{escape(label)}</label>'
        )
    for key, label, items, command, notes in options:
        out.append(
            f'<div class="situation-result" id="result-{key}" role="region" aria-label="{escape(label)}"><ul>'
        )
        for item in items:
            due = " · Due today" if item["deadline"] else ""
            out.append(
                f"<li>{escape(item['title'])} <span>{item['estimate_minutes']} min{due}</span></li>"
            )
        out.append("</ul>")
        for note in notes:
            names = ", ".join(tasks.get(id)["title"] for id in note.ids)
            out.append(f"<p>{escape(note.text)} {escape(names)}</p>")
        out.append(
            f"<details><summary>See the command</summary><pre><code>{escape(command)}</code></pre></details></div>"
        )
    out.extend(
        [
            "</fieldset>",
            ("<p>These choices apply to this answer. If the person asks to keep a routine, "
            "the agent can save it for a later session.</p>"),
            "</section>",
        ]
    )
    return "\n".join(out)


def walkthrough():
    store = Store(":memory:")
    try:
        tasks = Tasks(store, actor="editor", timezone="UTC", now="2030-05-01T12:00:00Z")
        tasks.add("Pay venue deposit", id="deposit", due="2030-05-08", move="person")
        tasks.add("Draft invitation", id="draft", plan="tomorrow", move="agent")
        tasks.add("Approve invitation", id="approve", after=["draft"], move="person")
        tasks.add("Design a poster", id="poster", proposed=True)
        afternoon = situations(store)
        now = tasks.now()
        scenes = [
            (
                "What can the agent do?",
                "The draft is planned for tomorrow. The agent can start it today.",
                "daymark ready --move agent",
                listing(tasks.ready(move="agent"), now),
            ),
            (
                "What needs the person?",
                "The deposit has a hard cutoff. Approval will appear when the draft is complete.",
                "daymark ready --move person",
                listing(tasks.ready(move="person"), now),
            ),
            (
                "Where did the poster idea go?",
                "The agenda keeps the suggestion visible until the person decides.",
                "daymark agenda",
                agenda_text(tasks.agenda(), now),
            ),
        ]
        tasks.done("draft", note="Draft is in the event folder.")
        scenes.append(
            (
                "What changes when the draft is done?",
                "Completing the draft releases the approval. The person can review it now.",
                'daymark done draft --note "Draft is in the event folder."\n$ daymark ready --move person',
                listing(tasks.ready(move="person"), now),
            )
        )
    finally:
        store.close()
    out = [
        BEGIN,
        '<section aria-labelledby="walkthrough"><h2 id="walkthrough">Plan an event</h2>',
        (
            "<p>It is May 1. The venue needs a deposit by May 8. The invitation needs a draft "
            "and an approval. Someone has suggested a poster. Explore the same workspace "
            "from four points of view.</p>"
        ),
    ]
    for i, (question, explanation, command, output) in enumerate(scenes):
        out.extend(
            [
                f'<details name="event-example"{" open" if i == 0 else ""}>',
                f"<summary>{escape(question)}</summary>",
                f"<p>{escape(explanation)}</p>",
                f"<pre><code>$ {escape(command)}\n{escape(output)}</code></pre></details>",
            ]
        )
    return "\n".join([*out, "</section>", afternoon, END])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    original = PAGE.read_text()
    before, section = original.split(BEGIN)
    _, after = section.split(END)
    new = before + walkthrough() + after
    if args.check and original != new:
        raise SystemExit("Walkthrough has drifted. Run uv run python scripts/build_walkthrough.py")
    if not args.check and original != new:
        PAGE.write_text(new)
