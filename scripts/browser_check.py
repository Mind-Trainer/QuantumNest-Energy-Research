"""Exercise the JavaScript interface in Chromium with FastAPI as the HTTP handler."""
from pathlib import Path
import argparse

from fastapi.testclient import TestClient
from playwright.sync_api import expect, sync_playwright

from backend.app import app


def main(output, chromium):
    output.mkdir(exist_ok=True, parents=True)
    with TestClient(app) as client, sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=chromium, args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": 1440, "height": 1000}, accept_downloads=True)
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))

        def handle(route):
            req = route.request
            url = req.url.removeprefix("http://quantumnest.test")
            response = client.request(req.method, url, content=req.post_data_buffer,
                                      headers={"content-type": req.headers.get("content-type", "")})
            route.fulfill(status=response.status_code, headers=dict(response.headers), body=response.content)

        page.route("http://quantumnest.test/**", handle)
        page.goto("http://quantumnest.test")
        expect(page.locator("#schedule-status")).to_have_text("optimal")
        expect(page.locator("#schedule-table")).to_contain_text("Essential refrigeration")
        page.screenshot(path=output / "dispatch.png")
        with page.expect_download() as result:
            page.get_by_role("button", name="Download schedule").click()
        assert result.value.suggested_filename == "quantumnest-schedule.csv"
        assert "Washing machine" in result.value.path().read_text(encoding="utf-8")

        page.get_by_role("link", name="Configuration").click()
        row = page.locator("#appliance-editor tbody tr").nth(2)
        row.get_by_role("spinbutton", name="Preferred", exact=True).fill("21")
        page.get_by_role("button", name="Apply changes").click()
        expect(page.locator("#config-status")).to_contain_text("Configuration applied")
        page.get_by_role("link", name="Dispatch").click()
        page.get_by_role("button", name="Reoptimize remaining hours").click()
        expect(page.locator("#status")).to_contain_text("Revised plan")
        expect(page.locator("#changes")).to_contain_text("Essential load stays")
        assert page.locator("#changes tbody tr").count() == 7

        page.get_by_role("link", name="Quantum lab").click()
        page.get_by_label("Noise", exact=True).select_option("combined")
        page.get_by_role("button", name="Run Qiskit simulation").click()
        expect(page.locator("#q-status")).to_contain_text("Qiskit Aer")
        expect(page.locator("#circuit")).to_contain_text("Rz")
        page.locator("#matrix button").first.click()
        expect(page.locator("#cell-note")).to_contain_text("Washing machine")
        page.screenshot(path=output / "quantum-lab.png")
        page.get_by_role("link", name="Experiment results").click()
        expect(page.locator("#study-table")).to_contain_text("hybrid")
        page.get_by_label("Study", exact=True).select_option("noise")
        expect(page.locator("#study-table")).to_contain_text("combined")
        page.screenshot(path=output / "experiment-results.png")
        assert not errors, errors
        browser.close()
    print("Chromium integration passed: dispatch, CSV, configuration, rolling update, quantum, study explorer")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("docs/screenshots"))
    parser.add_argument("--chromium")
    args = parser.parse_args()
    main(args.output, args.chromium)
