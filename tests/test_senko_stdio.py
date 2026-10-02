import contextlib
import sys
import threading
import time

import pytest

import transcribe

senko = pytest.importorskip("senko")


def test_senko_no_longer_swaps_stdio(monkeypatch):
    import senko.diarizer

    monkeypatch.setattr(senko.diarizer, "suppress_stdout_stderr",
                        senko.diarizer.suppress_stdout_stderr)
    transcribe._disable_senko_stdio_swap()
    assert senko.diarizer.suppress_stdout_stderr is contextlib.nullcontext


def test_background_senko_leaves_main_thread_stderr_alone():
    transcribe._disable_senko_stdio_swap()
    real = sys.stderr
    swapped = []
    worker = threading.Thread(
        target=lambda: senko.Diarizer(device="auto", warmup=True, quiet=True))
    worker.start()
    while worker.is_alive():
        if sys.stderr is not real:
            swapped.append(sys.stderr)
        time.sleep(0.001)
    worker.join()
    assert not swapped
