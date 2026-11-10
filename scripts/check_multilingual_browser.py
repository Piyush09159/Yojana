"""Explicitly mocked browser check against a development deployment.

Requires LLM_PROVIDER=mock and MOCK_LLM_FILE=tests/fixtures/deployment_mock.yaml
on the test backend, DATABASE_URL configured, and the local proxy simulator.
Never use this fixture in production. No hosted inference success is claimed.
"""
import argparse
import os
from playwright.sync_api import sync_playwright


def check(url):
    with sync_playwright() as playwright:
        executable = os.getenv('BROWSER_EXECUTABLE')
        browser = playwright.chromium.launch(headless=True, **({'executable_path': executable} if executable else {}))
        for lang, text in [('en', 'I am 62 in Maharashtra'), ('hi', 'मेरी उम्र 62 है'), ('mr', 'माझे वय 62 आहे')]:
            page = browser.new_page(viewport={'width': 390, 'height': 900})
            page.goto(url.rstrip('/') + '/app')
            page.locator('#lang').select_option(lang)
            page.locator('#message').fill(text)
            page.locator('#send').click()
            page.wait_for_function("document.querySelector('#stat-facts').textContent !== '0'")
            assert 'age: 62' in page.locator('#facts').inner_text()
            assert page.request.get(url.rstrip('/') + '/api/inference/status').json()['state'] == 'mocked'
            # Saved facts, messages and owner cookie survive an actual page reload.
            page.reload()
            page.wait_for_function("document.querySelector('#case-status').textContent.includes('restored')")
            assert 'age: 62' in page.locator('#facts').inner_text()
            assert text in page.locator('#chat').inner_text()
            page.locator('#reset').click()
            page.wait_for_function("document.querySelector('#stat-facts').textContent === '0'")
            assert not page.evaluate("sessionStorage.getItem('yojana-case-id')")
            print(f'{lang}: browser conversation, cookie authorization, persistence/reload and deletion passed (mocked extraction)')
            page.close()
        browser.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', required=True)
    check(parser.parse_args().url)
