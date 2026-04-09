#!/usr/bin/env python3
"""Focused Playwright validation for the Hermes update UI."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path

from playwright.sync_api import sync_playwright


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:5057/", help="Base URL for the Hermes web UI")
    parser.add_argument("--token", required=True, help="HERMES_WEBUI_TOKEN used for browser auth")
    parser.add_argument(
        "--screenshot-dir",
        default="",
        help="Directory to write screenshots into. Defaults to a temp directory.",
    )
    parser.add_argument("--headed", action="store_true", help="Launch Chromium headed instead of headless.")
    parser.add_argument("--slow-mo", type=int, default=0, help="Delay browser actions by this many milliseconds.")
    return parser.parse_args()


def expect(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def wait_for_update_state(page, timeout_ms: int = 15000) -> dict:
    deadline = time.time() + (timeout_ms / 1000)
    while time.time() < deadline:
        state = page.evaluate(
            """
            async () => {
                const token = window.localStorage.getItem('hermes_webui_token');
                const resp = await fetch('/api/hermes/update-status?refresh=1', {
                    headers: token ? { Authorization: 'Bearer ' + token } : {},
                });
                if (!resp.ok) return null;
                return await resp.json();
            }
            """
        )
        if state and state.get("installed_version", {}).get("display"):
            return state
        page.wait_for_timeout(200)
    raise AssertionError("Timed out waiting for Hermes update state to load")


def main() -> int:
    args = parse_args()
    shot_dir = Path(args.screenshot_dir) if args.screenshot_dir else Path(tempfile.mkdtemp(prefix="hermes-pw-update-"))
    shot_dir.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=not args.headed, slow_mo=max(0, args.slow_mo))
        context = browser.new_context(viewport={"width": 1440, "height": 1100})
        context.add_init_script(
            f"window.localStorage.setItem('hermes_webui_token', {json.dumps(args.token)});"
        )
        page = context.new_page()
        page.goto(args.url, wait_until="networkidle", timeout=30000)
        state = wait_for_update_state(page)
        page.evaluate(
            """
            (state) => {
                if (typeof HermesUpdate !== 'undefined') {
                    HermesUpdate.applyState(state);
                }
                if (typeof renderHermesUpdateCard !== 'undefined') {
                    const content = document.getElementById('content');
                    if (content) content.innerHTML = renderHermesUpdateCard(state);
                }
            }
            """,
            state,
        )

        page.screenshot(path=str(shot_dir / "update-home.png"), full_page=True)

        page.wait_for_selector("#hermes-update-card", timeout=10000)
        page.wait_for_function(
            """
            () => {
                const card = document.querySelector('#hermes-update-card');
                return !!card && (card.innerText || '').toLowerCase().includes('installed version');
            }
            """,
            timeout=10000,
        )
        card_text = page.locator("#hermes-update-card").inner_text(timeout=5000)
        expect("Hermes Updates" in card_text, "Update card did not render")
        expect("installed version" in card_text.lower(), "Update card is missing the installed version field")
        expect(
            state["installed_version"]["display"] in card_text,
            "Update card did not show the installed Hermes version",
        )

        status = state.get("status") or ""
        availability = state.get("availability_status") or ""
        if availability == "update_available" or status == "update_available":
            if state.get("update_scope") == "revision":
                banner_text = page.locator("#global-status-banner").inner_text(timeout=1000).lower()
                expect(not banner_text.strip(), "Revision-only updates should stay in the update card, not the global banner")
                expect("update type" in card_text.lower(), "Update card is missing the update scope field")
            else:
                banner_text = page.locator("#global-status-banner").inner_text(timeout=5000).lower()
                expect("update" in banner_text, "Global update banner did not render for an available update")
            if state.get("can_update"):
                page.evaluate("() => openHermesUpdateConfirm()")
                page.wait_for_selector("#modal-overlay.active", timeout=5000)
                expect(
                    page.locator("#confirm-hermes-update-btn").count() == 1,
                    "Update confirmation modal did not show the confirm action",
                )
                page.screenshot(path=str(shot_dir / "update-confirm.png"), full_page=True)
                page.click('#modal-footer .btn:not(.btn-primary)')
            else:
                expect("Manual Command" in card_text, "Manual update instructions were missing")

        page.locator("#hermes-update-card button", has_text="Check Now").first.click()
        page.wait_for_timeout(1000)
        refreshed = page.locator("#hermes-update-card").inner_text(timeout=5000)
        expect("Hermes Updates" in refreshed, "Update card disappeared after running Check Now")

        page.screenshot(path=str(shot_dir / "update-service.png"), full_page=True)
        browser.close()

    print(f"Update validation screenshots saved to {shot_dir}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AssertionError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
