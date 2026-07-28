#!/usr/bin/env python3
"""Regenerate the README screenshots from the fabricated demo archive.

Usage:
  venv/bin/python3 scripts/demo/make_demo_data.py /tmp/echowall-demo
  WATCH_TRANSCRIBER_DATA=/tmp/echowall-demo <EchoWall.app>/Contents/MacOS/desktop &
  LOCAL_ARCHIVE_DIR=/tmp/echowall-demo venv/bin/python3 -m deliveries.viewer
  python3 scripts/demo/shoot_screenshots.py http://127.0.0.1:<port> docs/screenshots

Serve through the app (not file://) so the manager UI — speaker tagging,
attachment editor, delete — is enabled in the shots. Requires system python3
with playwright installed.
"""
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright


def main() -> None:
    base = sys.argv[1].rstrip("/")
    out = Path(sys.argv[2])
    out.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(
            viewport={"width": 1440, "height": 900},
            device_scale_factor=2,
            color_scheme="dark",  # headless defaults to light; the app is dark-first
        )
        page.goto(f"{base}/index.html")
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(800)

        page.click("text=咖啡机器人产品头脑风暴")
        page.wait_for_timeout(600)
        page.screenshot(path=str(out / "01-overview.png"))

        page.keyboard.press("3")
        page.wait_for_timeout(400)
        page.screenshot(path=str(out / "02-transcript.png"))

        page.keyboard.press("2")
        page.wait_for_timeout(400)
        page.screenshot(path=str(out / "03-attachments.png"))

        page.click("text=纳瓦尔宝典")
        page.wait_for_timeout(500)
        page.click("#detail .spk-chip")
        page.wait_for_timeout(400)
        page.screenshot(path=str(out / "04-speaker-tagging.png"))

        browser.close()
    print("done:", sorted(f.name for f in out.glob("*.png")))


if __name__ == "__main__":
    main()
