from pathlib import Path

import pytest

import transcribe
from deliveries import viewer
from transcribe import (
    build_summarize_prompt,
    format_note,
    language_name,
    parse_summary_languages,
)

AUDIO = Path("20260712 093015-1A2B3C4D.m4a")
RESULT = {
    "title": "Synthetic meeting",
    "category": "工作商务",
    "summary_en": "A fabricated summary.",
    "summary_zh": "一段合成摘要。",
    "summary_es": "Un resumen inventado.",
    "key_points_en": ["One point", "Two points"],
    "key_points_zh": ["一个要点"],
    "key_points_es": ["Un punto", "Dos puntos"],
    "action_items": ["Follow up"],
    "transcript": "[00:00:00 - 00:00:01] SPEAKER_1: synthetic",
}


@pytest.mark.parametrize("value, expected", [
    ("en,zh", ["en", "zh"]),
    (" EN , es ", ["en", "es"]),
    ("en,en,es", ["en", "es"]),
    ("en,not-a-code,zh", ["en", "zh"]),
    ("", ["en", "zh"]),
])
def test_parse_summary_languages(value, expected):
    assert parse_summary_languages(value) == expected


def test_default_prompt_still_asks_for_english_and_chinese():
    prompt = build_summarize_prompt(["en", "zh"])
    for key in ("summary_en", "summary_zh", "key_points_en", "key_points_zh", "action_items"):
        assert f'"{key}"' in prompt
    assert "in Chinese (中文)" in prompt


def test_prompt_follows_configured_languages():
    prompt = build_summarize_prompt(["en", "es"])
    assert '"summary_es"' in prompt and '"key_points_es"' in prompt
    assert "summary in Spanish" in prompt
    assert "summary_zh" not in prompt and "key_points_zh" not in prompt


def test_unknown_language_code_is_named_by_code():
    assert language_name("sw") == "the language with ISO 639-1 code 'sw'"


def test_english_only_note_has_no_other_languages(monkeypatch):
    monkeypatch.setattr(transcribe, "SUMMARY_LANGUAGES", ["en"])
    note = format_note(AUDIO, RESULT)
    assert note["summary"] == "A fabricated summary."
    for text in ("一段合成摘要。", "一个要点", "Un resumen inventado."):
        assert text not in note["markdown"] and text not in note["html"]
    assert "- One point\n- Two points" in note["markdown"]


def test_english_spanish_note_keeps_configured_order(monkeypatch):
    monkeypatch.setattr(transcribe, "SUMMARY_LANGUAGES", ["en", "es"])
    note = format_note(AUDIO, RESULT)
    md = note["markdown"]
    assert note["summary"] == "A fabricated summary.\n\nUn resumen inventado."
    assert md.index("A fabricated summary.") < md.index("Un resumen inventado.")
    assert "- One point\n- Two points\n\n- Un punto\n- Dos puntos" in md
    assert "一段合成摘要。" not in md
    assert "<p>Un resumen inventado.</p>" in note["html"]
    assert "<li>Dos puntos</li>" in note["html"]


def test_archive_page_shows_second_language_for_non_chinese_notes(monkeypatch, tmp_path):
    monkeypatch.setattr(transcribe, "SUMMARY_LANGUAGES", ["en", "es"])
    md = tmp_path / "093015-synthetic.md"
    md.write_text(format_note(AUDIO, RESULT)["markdown"], encoding="utf-8")

    parsed = viewer._parse_note(md)

    assert parsed["summary_en"] == "A fabricated summary."
    assert parsed["summary_zh"] == "Un resumen inventado."
    assert parsed["key_points"] == ["Un punto", "Dos puntos"]


def test_archive_page_unchanged_for_default_bilingual_notes(monkeypatch, tmp_path):
    monkeypatch.setattr(transcribe, "SUMMARY_LANGUAGES", ["en", "zh"])
    md = tmp_path / "093015-synthetic.md"
    md.write_text(format_note(AUDIO, RESULT)["markdown"], encoding="utf-8")

    parsed = viewer._parse_note(md)

    assert parsed["summary_en"] == "A fabricated summary."
    assert parsed["summary_zh"] == "一段合成摘要。"
    assert parsed["key_points"] == ["一个要点"]
