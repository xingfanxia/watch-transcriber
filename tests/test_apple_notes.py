from pathlib import Path

from deliveries import apple_notes
from transcribe import format_note

RESULT = {
    "title": "Synthetic meeting",
    "category": "其他",
    "summary_en": "A fabricated summary.",
    "summary_zh": "一段合成摘要。",
    "key_points_en": ["One point"],
    "action_items": ["Follow up"],
    "transcript": "[00:00:00 - 00:00:01] SPEAKER_1: synthetic",
}
BLANK = "<div><br></div>"


def test_notes_body_adds_blank_lines_between_sections_and_paragraphs():
    html = format_note(Path("20260712 093015-1A2B3C4D.m4a"), RESULT)["html"]

    body = apple_notes.notes_body(html)

    for heading in ("Summary", "Key Points", "Action Items", "Transcript"):
        assert f"{BLANK}<h2>{heading}</h2>" in body
    assert f"<p>A fabricated summary.</p>\n{BLANK}<p>一段合成摘要。</p>" in body
    assert body.startswith("<h1>")  # no blank line above the title


def test_deliver_lets_notes_take_the_title_from_the_body(monkeypatch):
    scripts = []

    class Done:
        returncode = 0

    def fake_run(cmd, input, **_kwargs):
        scripts.append(input)
        return Done()

    monkeypatch.setattr(apple_notes.subprocess, "run", fake_run)
    note = format_note(Path("20260712 093015-1A2B3C4D.m4a"), RESULT)

    assert apple_notes.deliver(note)

    script = scripts[0]
    # Title comes from the body's <h1>; also setting `name` would show it twice.
    assert "make new note at targetFolder with properties {body:" in script
    assert f"{BLANK}<h2>Summary</h2>" in script
