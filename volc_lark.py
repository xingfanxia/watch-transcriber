#!/usr/bin/env python3
"""妙记 (Volcano Engine Lark Minutes ASR, resource volc.lark.minutes) provider.

Why this exists
---------------
妙记 diarizes far better than chunk-stitched Gemini/OpenAI or the raw Doubao
auc models. Verified 2026-06-17 across 5 real recordings — 妙记 returned the
exact speaker count on every 2-person conversation (2/2/2/2) while 极速版
(auc_turbo) and 2.0 (seedasr) over-counted (3-5), and it handled a 3.45h file
in a single call.

Pipeline
--------
    audio  --ffmpeg-->  16kHz mono mp3  --upload-->  TOS  --presign-->  URL
                                                                          |
                          speaker-labeled transcript  <--妙记 submit/poll/get

妙记 requires a publicly-fetchable FileURL (no base64 / no direct upload). We
host the audio in Volcano TOS: 妙记 fetches it intra-China (fast); the only
slow leg is the cross-border upload from a US host, mitigated by compression
+ parallel multipart upload.

Output format matches the rest of the pipeline:
    [HH:MM:SS - HH:MM:SS] SPEAKER_<id>: content

Credentials (in watch-transcriber/.env):
    VOLC_API_KEY        妙记 x-api-key auth
    VOLC_TOS_ACCESS_KEY / VOLC_TOS_SECRET_KEY / VOLC_TOS_BUCKET /
    VOLC_TOS_REGION / VOLC_TOS_ENDPOINT
"""

import ipaddress
import os
import shutil
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse

import requests

# --- 妙记 API ---------------------------------------------------------------
_SUBMIT = "https://openspeech.bytedance.com/api/v3/auc/lark/submit"
_QUERY = "https://openspeech.bytedance.com/api/v3/auc/lark/query"
_RESOURCE_ID = "volc.lark.minutes"

# --- tunables (env-overridable) --------------------------------------------
# Audio is downmixed to 16kHz mono before upload. 32kbps mono matches the
# bitrate the provider choice was validated on; it is plenty for speech ASR
# and keeps the cross-border upload small. Lower it (e.g. "16k") to trade a
# little quality for faster upload.
_MP3_BITRATE = os.environ.get("LARK_MP3_BITRATE", "32k")
_SOURCE_LANG = os.environ.get("LARK_SOURCE_LANG", "zh_cn")  # zh_cn handles zh+en mixed
_POLL_INTERVAL = int(os.environ.get("LARK_POLL_INTERVAL", "15"))
_POLL_TIMEOUT = int(os.environ.get("LARK_POLL_TIMEOUT", "5400"))  # 90 min ceiling
# Parallel multipart upload: TOS requires part_size >= 5MiB. Cross-border
# throttling is per-connection, so concurrency multiplies throughput
# (measured ~34KB/s single-stream -> ~116KB/s at 4x).
_PART_SIZE = 5 * 1024 * 1024
_UPLOAD_TASKS = int(os.environ.get("LARK_UPLOAD_TASKS", "8"))


class LarkError(RuntimeError):
    """妙记 / TOS failure — raised loud with context, never swallowed."""


def _autoload_env() -> None:
    """Populate VOLC_* from the sibling .env if not already in os.environ.
    No-op when the host already loaded .env (e.g. transcribe.py at import);
    makes standalone (`python volc_lark.py`) and skill use work too."""
    if os.environ.get("VOLC_API_KEY") and os.environ.get("VOLC_TOS_ACCESS_KEY"):
        return
    env_path = Path(__file__).parent / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())


_autoload_env()


def _env(key: str) -> str:
    val = os.environ.get(key)
    if not val:
        raise LarkError(
            f"{key} not set — 妙记 provider needs VOLC_API_KEY + VOLC_TOS_* in .env"
        )
    return val


def _fmt_hms(ms) -> str:
    s = int((ms or 0) / 1000)
    return f"{s // 3600:02d}:{(s % 3600) // 60:02d}:{s % 60:02d}"


def _to_mp3(src: Path) -> Path:
    """Downmix to 16kHz mono mp3 in a temp file. Minimises cross-border upload
    bytes and gives 妙记 the sample rate ASR wants."""
    tmpdir = Path(tempfile.mkdtemp(prefix="lark_"))
    dst = tmpdir / (src.stem.replace(" ", "_") + ".mp3")
    proc = subprocess.run(
        ["ffmpeg", "-y", "-i", str(src), "-ac", "1", "-ar", "16000",
         "-b:a", _MP3_BITRATE, str(dst)],
        capture_output=True, text=True,
    )
    if proc.returncode != 0 or not dst.exists():
        shutil.rmtree(tmpdir, ignore_errors=True)  # don't leak the temp dir on failure
        raise LarkError(f"ffmpeg conversion failed for {src.name}: {proc.stderr[-400:]}")
    return dst


def _tos_client():
    try:
        import tos
    except ImportError as e:
        raise LarkError("tos SDK not installed in this venv — `pip install tos`") from e
    try:
        return tos.TosClientV2(
            _env("VOLC_TOS_ACCESS_KEY"), _env("VOLC_TOS_SECRET_KEY"),
            _env("VOLC_TOS_ENDPOINT"), _env("VOLC_TOS_REGION"),
        )
    except LarkError:
        raise  # missing-cred errors already carry context
    except Exception as e:
        raise LarkError(f"failed to initialize TOS client: {e}") from e


def _tos_upload(client, mp3: Path) -> str:
    """Upload mp3, return the object key. Parallel multipart for files >5MiB,
    single-stream below (TOS multipart minimum part size is 5MiB)."""
    bucket = _env("VOLC_TOS_BUCKET")
    key = f"transcribe/{mp3.stem}-{uuid.uuid4().hex[:8]}.mp3"
    size_mb = mp3.stat().st_size / 1048576
    t0 = time.time()
    if mp3.stat().st_size > _PART_SIZE:
        client.upload_file(bucket, key, str(mp3), task_num=_UPLOAD_TASKS, part_size=_PART_SIZE)
    else:
        client.put_object_from_file(bucket, key, str(mp3))
    print(f"  [妙记] uploaded {size_mb:.1f}MB to TOS in {time.time()-t0:.0f}s", flush=True)
    return key


def _tos_presign(client, key: str) -> str:
    import tos
    pre = client.pre_signed_url(
        tos.HttpMethodType.Http_Method_Get, _env("VOLC_TOS_BUCKET"), key, expires=7200,
    )
    return pre.signed_url


def _tos_delete(client, key: str) -> None:
    try:
        client.delete_object(_env("VOLC_TOS_BUCKET"), key)
    except Exception as e:  # cleanup is best-effort; don't fail the transcription over it
        print(f"  [妙记] WARN: failed to delete TOS object {key}: {e}", flush=True)


def _headers(req_id: str) -> dict:
    return {
        "x-api-key": _env("VOLC_API_KEY"),
        "X-Api-Resource-Id": _RESOURCE_ID,
        "X-Api-Request-Id": req_id,
        "X-Api-Sequence": "-1",
        "Content-Type": "application/json",
    }


def _guard_fetch_url(url: str) -> None:
    """SSRF guard for the transcript URL (which comes from the 妙记 API response,
    not our own code). Require https and reject loopback / private / link-local
    hosts so a compromised or spoofed API response can't redirect us at an
    internal endpoint (e.g. a cloud metadata service)."""
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise LarkError(f"妙记 transcript URL is not https (scheme={parsed.scheme!r})")
    host = parsed.hostname or ""
    if not host or host == "localhost" or host.endswith(".local"):
        raise LarkError(f"妙记 transcript URL host not allowed: {host!r}")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return  # a hostname (resolves to ByteDance/TOS CDN) — accept
    if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
        raise LarkError(f"妙记 transcript URL points to a non-public IP: {host}")


def _submit_poll(url: str, num_speakers: int) -> list:
    """Submit a 妙记 job for the given FileURL, poll to completion, return the
    list of speaker-labeled sentences. Raises LarkError loud on any failure."""
    req_id = str(uuid.uuid4())
    body = {
        "Input": {"Offline": {"FileURL": url, "FileType": "audio"}},
        "Params": {
            "AllActivate": False,
            "SourceLang": _SOURCE_LANG,
            "AudioTranscriptionEnable": True,
            "AudioTranscriptionParams": {
                "SpeakerIdentification": True,
                "NumberOfSpeaker": num_speakers,  # 0 = auto
                "NeedWordTimeSeries": False,
            },
            # 妙记 rejects the request unless at least one additional feature is
            # enabled ("additional features empty"); summary is the cheapest.
            "SummarizationEnabled": True,
            "SummarizationParams": {"Types": ["summary"]},
        },
    }
    rs = requests.post(_SUBMIT, json=body, headers=_headers(req_id), timeout=60)
    code = rs.headers.get("X-Api-Status-Code")
    if code != "20000000":
        raise LarkError(
            f"妙记 submit failed: code={code} msg={rs.headers.get('X-Api-Message')} body={rs.text[:300]}"
        )
    task_id = (rs.json().get("Data") or {}).get("TaskID")
    if not task_id:
        raise LarkError(f"妙记 submit returned no TaskID: {rs.text[:300]}")

    deadline = time.time() + _POLL_TIMEOUT
    while True:
        if time.time() > deadline:
            raise LarkError(f"妙记 poll timed out after {_POLL_TIMEOUT}s (TaskID={task_id})")
        rq = requests.post(_QUERY, json={"TaskID": task_id}, headers=_headers(req_id), timeout=60)
        if rq.status_code != 200:
            raise LarkError(f"妙记 query HTTP {rq.status_code}: {rq.text[:300]}")
        try:
            data = rq.json().get("Data") or {}
        except ValueError as e:
            raise LarkError(f"妙记 query returned non-JSON (HTTP {rq.status_code}): {rq.text[:300]}") from e
        status = data.get("Status")
        if status == "success":
            break
        if status in ("running", None) and (data.get("ErrCode", 0) in (0, None)):
            time.sleep(_POLL_INTERVAL)
            continue
        raise LarkError(
            f"妙记 query failed: status={status} err={data.get('ErrCode')} {data.get('ErrMessage')}"
        )

    transcript_url = (data.get("Result") or {}).get("AudioTranscriptionFile")
    if not transcript_url:
        raise LarkError(f"妙记 success but no AudioTranscriptionFile (TaskID={task_id})")
    _guard_fetch_url(transcript_url)  # SSRF guard: URL came from the API response
    tr = requests.get(transcript_url, timeout=120)
    if tr.status_code != 200:
        raise LarkError(f"妙记 transcript download failed: HTTP {tr.status_code} {tr.text[:200]}")
    try:
        sentences = tr.json()
    except ValueError as e:
        raise LarkError(f"妙记 transcript was not valid JSON: {e}; body={tr.text[:300]}") from e
    if not isinstance(sentences, list):
        raise LarkError(f"妙记 transcript was not a sentence list (TaskID={task_id})")
    return sentences


def _format(sentences: list) -> str:
    """Render 妙记 sentences into the standard
    `[HH:MM:SS - HH:MM:SS] SPEAKER_<id>: content` transcript."""
    lines = []
    for s in sentences:
        spk = (s.get("speaker") or {}).get("id", "?")
        content = (s.get("content") or "").strip()
        if not content:
            continue
        lines.append(
            f"[{_fmt_hms(s.get('start_time'))} - {_fmt_hms(s.get('end_time'))}] "
            f"SPEAKER_{spk}: {content}"
        )
    return "\n".join(lines)


def lark_transcribe(audio_path: Path, num_speakers: int = 0) -> str:
    """Transcribe + diarize one audio file via 妙记.

    num_speakers: 0 = let 妙记 auto-detect (best for unknown counts);
                  N = hint the expected number of speakers.
    Returns the standard speaker-labeled transcript string.
    """
    mp3 = _to_mp3(audio_path)
    client = None
    key = None
    try:
        client = _tos_client()
        key = _tos_upload(client, mp3)
        url = _tos_presign(client, key)
        spk_desc = "auto" if num_speakers == 0 else str(num_speakers)
        print(f"  [妙记] transcribing (speakers={spk_desc})...", flush=True)
        t0 = time.time()
        sentences = _submit_poll(url, num_speakers)
        print(f"  [妙记] done in {time.time()-t0:.0f}s ({len(sentences)} sentences)", flush=True)
        return _format(sentences)
    finally:
        # Always runs — even if _tos_client() raised (client stays None).
        if client is not None and key:
            _tos_delete(client, key)
        shutil.rmtree(mp3.parent, ignore_errors=True)  # remove the temp dir + mp3


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="妙记 (Lark Minutes) transcription")
    ap.add_argument("audio")
    ap.add_argument("--speakers", type=int, default=0, help="0 = auto-detect")
    a = ap.parse_args()
    print(lark_transcribe(Path(a.audio), a.speakers))
