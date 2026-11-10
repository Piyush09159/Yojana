"""Focused WAHA adapter tests; live WAHA network not needed."""
import os
from unittest.mock import patch
from fastapi.testclient import TestClient
from fastapi import FastAPI
from web.waha_bridge import router
from test_deployment_v24 import persistent

app = FastAPI()
app.include_router(router)
client = TestClient(app)
SECRET = "this-is-a-local-test-secret-long-enough"

def _post(payload, event="message", session="default", secret=SECRET):
    return client.post(f"/api/integrations/waha/{secret}", json={"event": event, "session": session, "payload": payload})

def test_disabled_by_default(monkeypatch):
    monkeypatch.delenv("YOJANA_WAHA_ENABLED", raising=False)
    assert _post({}).status_code == 503

def test_rejects_wrong_secret_and_ignores_group(monkeypatch, persistent):
    monkeypatch.setenv("YOJANA_WAHA_ENABLED", "true")
    monkeypatch.setenv("YOJANA_WAHA_WEBHOOK_SECRET", SECRET)
    monkeypatch.setenv("WAHA_BASE_URL", "http://localhost:3000")
    monkeypatch.setenv("WAHA_API_KEY", "test-key")
    assert _post({}, secret="wrong").status_code == 403
    assert _post({"from": "123@g.us", "body": "hello", "id": "group-test"}).json()["status"] == "ignored"

def test_no_unsolicited_response(monkeypatch, persistent):
    monkeypatch.setenv("YOJANA_WAHA_ENABLED", "true")
    monkeypatch.setenv("YOJANA_WAHA_WEBHOOK_SECRET", SECRET)
    monkeypatch.setenv("WAHA_BASE_URL", "http://localhost:3000")
    monkeypatch.setenv("WAHA_API_KEY", "test-key")
    assert _post({"from":"9123456789@c.us","body":"hello","id":"new-123"}).json()["status"] == "not_opted_in"
