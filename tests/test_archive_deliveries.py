"""Tests for the shared archive naming helpers and the audio_archive /
manifest deliveries. All filesystem work happens in tmp_path via
LOCAL_ARCHIVE_DIR; the Voice Memos library is never touched."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import subprocess  # noqa: E402

from deliveries import recording_stem, slug  # noqa: E402
from deliveries import archive_git, audio_archive, manifest, viewer  # noqa: E402


def _note(tmp_path, title="2026-07-26 01:34 银发大基建中控平台合作", audio=None):
    return {
        "title": title,
        "timestamp": "2026-07-26T01:34:21",
        "audio_path": str(audio or tmp_path / "20260726 013421-44BB9B24.m4a"),
        "category": "工作商务",
    }


@pytest.fixture
def archive(tmp_path, monkeypatch):
    root = tmp_path / "data"
    root.mkdir()
    monkeypatch.setenv("LOCAL_ARCHIVE_DIR", str(root))
    return root


def test_slug_idempotent_and_fallback():
    assert slug("银发大基建 中控平台/合作?") == slug(slug("银发大基建 中控平台/合作?"))
    assert slug("???") == "note"
    assert len(slug("x" * 200)) == 60


def test_recording_stem_strips_leading_timestamp(archive, tmp_path):
    stem = recording_stem(_note(tmp_path))
    assert stem == "013421-银发大基建中控平台合作"
    # A title that is ONLY the timestamp falls back to slugging the full title.
    stem = recording_stem(_note(tmp_path, title="2026-07-26 01:34 Voice Note"))
    assert stem == "013421-Voice-Note"


def test_audio_archive_copies_and_skips(archive, tmp_path, capsys):
    src = tmp_path / "20260726 013421-44BB9B24.m4a"
    src.write_bytes(b"AUDio" * 100)
    note = _note(tmp_path, audio=src)

    assert audio_archive.deliver(note)
    dest = archive / "2026-07-26" / "013421-银发大基建中控平台合作.m4a"
    assert dest.read_bytes() == src.read_bytes()

    # Second run: size matches -> no re-copy.
    assert audio_archive.deliver(note)
    assert "up-to-date" in capsys.readouterr().out

    # Truncated copy (crashed run) -> size mismatch -> re-copied.
    dest.write_bytes(b"AUD")
    assert audio_archive.deliver(note)
    assert dest.read_bytes() == src.read_bytes()


def test_audio_archive_replaces_stale_title_copy(archive, tmp_path):
    src = tmp_path / "20260726 013421-44BB9B24.m4a"
    src.write_bytes(b"x" * 64)
    (archive / "2026-07-26").mkdir()
    stale = archive / "2026-07-26" / "013421-旧标题.m4a"
    stale.write_bytes(b"x" * 64)

    assert audio_archive.deliver(_note(tmp_path, audio=src))
    assert not stale.exists()
    assert (archive / "2026-07-26" / "013421-银发大基建中控平台合作.m4a").exists()


def test_audio_archive_missing_source_returns_false(archive, tmp_path):
    assert audio_archive.deliver(_note(tmp_path)) is False


def test_clean_category_whitelist():
    assert manifest.clean_category("工作商务") == "工作商务"
    assert manifest.clean_category(" 亲密关系 ") == "亲密关系"
    for bad in ("Work", "", None, "工作", 3):
        assert manifest.clean_category(bad) == "其他"


def test_manifest_deliver_and_views(archive, tmp_path):
    src = tmp_path / "20260726 013421-44BB9B24.m4a"
    src.write_bytes(b"x" * 64)
    note = _note(tmp_path, audio=src)
    day = archive / "2026-07-26"
    day.mkdir()
    (day / "013421-银发大基建中控平台合作.md").write_text("# t", encoding="utf-8")
    audio_archive.deliver(note)

    assert manifest.deliver(note)
    m = manifest.load()
    entry = m["2026-07-26 013421"]
    assert entry["original"] == "20260726 013421-44BB9B24.m4a"
    assert entry["category"] == "工作商务"
    assert entry["note"] == "2026-07-26/013421-银发大基建中控平台合作.md"
    assert entry["audio"] == "2026-07-26/013421-银发大基建中控平台合作.m4a"

    links = list((archive / "by-topic" / "工作商务").iterdir())
    assert sorted(p.suffix for p in links) == [".m4a", ".md"]
    for link in links:
        assert link.is_symlink() and link.resolve().exists()

    # Recategorize -> views move, old category dir disappears.
    note["category"] = "生活日常"
    manifest.deliver(note)
    assert not (archive / "by-topic" / "工作商务").exists()
    assert (archive / "by-topic" / "生活日常").is_dir()


def test_viewer_build_embeds_entries(archive, tmp_path):
    src = tmp_path / "20260726 013421-44BB9B24.m4a"
    src.write_bytes(b"x" * 64)
    note = _note(tmp_path, audio=src)
    day = archive / "2026-07-26"
    day.mkdir()
    (day / "013421-银发大基建中控平台合作.md").write_text(
        "# 2026-07-26 01:34 银发大基建中控平台合作\n\n**File:** `20260726 013421-44BB9B24.m4a`\n\n"
        "## Summary\n\nEnglish summary.\n\n中文摘要。\n\n"
        "## Key Points\n\n- point one\n- 要点一\n\n"
        "## Action Items\n\n- [ ] follow up\n\n"
        "---\n\n## Transcript\n\n```\n[00:00:01 - 00:00:05] SPEAKER_1: 你好\n```\n",
        encoding="utf-8",
    )
    audio_archive.deliver(note)
    manifest.deliver(note)

    dest = viewer.build()
    html = dest.read_text(encoding="utf-8")
    assert dest == archive / "index.html"
    assert "__PAYLOAD__" not in html
    payload = html.split('type="application/json">', 1)[1].split("</script>", 1)[0]
    import json
    data = json.loads(payload.replace("<\\/", "</"))
    (entry,) = data["entries"]
    assert entry["ai_title"] == "银发大基建中控平台合作"
    assert entry["summary_zh"] == "中文摘要。"
    assert entry["key_points"] == ["要点一"]
    assert entry["todos"] == ["follow up"]
    assert entry["duration"] == 5
    assert entry["audio"].endswith(".m4a")


def test_archive_git_noop_without_repo(archive, tmp_path, capsys):
    assert archive_git.deliver(_note(tmp_path)) is True
    assert "not a git repo" in capsys.readouterr().out


def test_archive_git_bootstraps_gitignore_and_commits(archive, tmp_path):
    src = tmp_path / "20260726 013421-44BB9B24.m4a"
    src.write_bytes(b"x" * 64)
    note = _note(tmp_path, audio=src)
    day = archive / "2026-07-26"
    day.mkdir()
    (day / "013421-银发大基建中控平台合作.md").write_text("# t", encoding="utf-8")
    audio_archive.deliver(note)

    def git(*args):
        return subprocess.run(
            ["git", "-C", str(archive), "-c", "core.quotepath=false", *args],
            capture_output=True, text=True,
        )

    git("init", "-q")
    git("config", "user.email", "t@t")
    git("config", "user.name", "t")
    assert archive_git.deliver(note) is True
    tracked = git("ls-files").stdout
    assert ".gitignore" in tracked
    assert "013421-银发大基建中控平台合作.md" in tracked
    assert ".m4a" not in tracked  # audio must NEVER enter git history
    # Second run with no changes: no new commit, still True.
    assert archive_git.deliver(note) is True
    assert git("rev-list", "--count", "HEAD").stdout.strip() == "1"


def test_manifest_preserves_user_speakers_on_reprocess(archive, tmp_path):
    note = _note(tmp_path)
    day = archive / "2026-07-26"
    day.mkdir()
    (day / "013421-银发大基建中控平台合作.md").write_text("# t", encoding="utf-8")
    manifest.deliver(note)
    m = manifest.load()
    m["2026-07-26 013421"]["speakers"] = {"SPEAKER_1": "AX"}
    manifest.save(m)

    manifest.deliver(note)  # reprocess must not wipe user-authored tags
    assert manifest.load()["2026-07-26 013421"]["speakers"] == {"SPEAKER_1": "AX"}


def test_apply_speakers_rewrites_and_reverts(archive, tmp_path):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "ops"))
    from apply_speakers import apply_keys

    note = _note(tmp_path)
    day = archive / "2026-07-26"
    day.mkdir()
    md = day / "013421-银发大基建中控平台合作.md"
    md.write_text(
        "# t\n\n## Transcript\n\n```\n"
        "[00:00:01 - 00:00:02] SPEAKER_1: 你好\n"
        "[00:00:03 - 00:00:04] SPEAKER_2: 嗯\n```\n",
        encoding="utf-8",
    )
    manifest.deliver(note)
    m = manifest.load()
    key = "2026-07-26 013421"
    m[key]["speakers"] = {"SPEAKER_1": "妍子"}
    manifest.save(m)

    assert apply_keys([key], rebuild_viewer=False) == 1
    text = md.read_text(encoding="utf-8")
    assert "] 妍子: 你好" in text and "SPEAKER_2: 嗯" in text
    assert manifest.load()[key]["speakers_applied"] == {"SPEAKER_1": "妍子"}
    # Idempotent second run, then rename, then clear back to the raw slot.
    assert apply_keys([key], rebuild_viewer=False) == 0
    m = manifest.load()
    m[key]["speakers"] = {"SPEAKER_1": "AX"}
    manifest.save(m)
    apply_keys([key], rebuild_viewer=False)
    assert "] AX: 你好" in md.read_text(encoding="utf-8")
    m = manifest.load()
    m[key]["speakers"] = {}
    manifest.save(m)
    apply_keys([key], rebuild_viewer=False)
    assert "] SPEAKER_1: 你好" in md.read_text(encoding="utf-8")
    assert "speakers_applied" not in manifest.load()[key]


def test_manifest_missing_audio_is_null(archive, tmp_path):
    note = _note(tmp_path)  # audio_path doesn't exist, no copy made
    day = archive / "2026-07-26"
    day.mkdir()
    (day / "013421-银发大基建中控平台合作.md").write_text("# t", encoding="utf-8")
    manifest.deliver(note)
    assert manifest.load()["2026-07-26 013421"]["audio"] is None
