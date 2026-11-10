"""Scheme Saathi web app: FastAPI APIs, V2.2 dashboard and legacy form.

    python -m web            # http://localhost:8000

Endpoints
  GET  /                            public landing page
  GET  /app                         V2.2 citizen dashboard
  GET  /chat                        compatibility redirect to /app
  GET  /legacy                      original form interface
  GET  /api/catalog                 catalog and Rescue workflow availability
  GET  /api/form                    form fields (from fields.yaml) + Maharashtra districts
  POST /api/sessions                {profile, lang, web} -> {id}; starts the investigation
  GET  /api/sessions/{id}/events    Server-Sent Events (?after=N to resume)
  POST /api/sessions/{id}/answer    {field, value} | {text} | {skip: true}
  POST /api/sessions/{id}/ask       {text}  free question about the results
  GET  /api/sessions/{id}           snapshot
  GET  /api/schemes/{scheme_id}     rules with source quotes (transparency)
"""
from __future__ import annotations

import json
import os
import time
import threading
from pathlib import Path
from typing import Any, Callable

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from core.awareness import awareness
from core.fields import load_fields, validate_profile
from discovery.catalog import load_catalog
from ingest.source_map import seed
from llm import LLMError, get_llm

from .session import WebSession
from .conversation_api import router as conversation_router
from .waha_bridge import router as waha_router
from .rescue_api import router as rescue_router
from .cases import router as cases_router
from .voice import router as voice_router
from .frontend import decorate
from . import case_store, runtime
from .access import owner, issue, require_memory
from .conversation_api import _safe_message

STATIC = Path(__file__).resolve().parent / "static"
FORM_FIELDS = ["state", "district", "age", "gender", "marital_status", "social_category", "occupation",
               "annual_family_income_inr", "ration_card_type", "disability_percent", "num_children",
               "has_girl_child_under_10", "is_student", "owns_cultivable_land", "is_minority"]


class NewSession(BaseModel):
    profile: dict[str, Any]
    lang: str = "en"
    web: bool = True


class Answer(BaseModel):
    field: str | None = None
    value: Any = None
    text: str | None = None
    skip: bool = False


class Ask(BaseModel):
    text: str


def _try_llm(role: str):
    try:
        return get_llm(role)
    except LLMError:
        return None


def create_app(planner=None, extractor=None, agent=None, discoverer_factory: Callable | None = None,
               catalog_loader: Callable[[], dict] = load_catalog, budget=None) -> FastAPI:
    app = FastAPI(title="Scheme Saathi", lifespan=runtime.lifespan)
    runtime.install(app)
    app.include_router(conversation_router)
    app.include_router(waha_router)
    app.include_router(rescue_router)
    app.include_router(cases_router)
    app.include_router(voice_router)
    sessions: dict[str, WebSession] = {}
    session_cache_lock = threading.RLock()
    llms: dict[str, Any] = {"planner": planner, "extractor": extractor, "agent": agent}

    def llm(role: str):
        if llms[role] is None:
            llms[role] = _try_llm(role)
        return llms[role]

    def discoverer():
        if discoverer_factory is not None:
            return discoverer_factory()
        try:
            from discovery.discover import Discoverer
            return Discoverer(llm=llm("extractor"))
        except Exception:
            return None                                    # no search key: catalog only

    def persist(s):
        if not case_store.enabled():
            return
        state = {'profile': s.profile, 'lang': s.lang, 'pending': s.pending,
                 'state': s.state, 'situation': s.situation, 'shortlist': s.shortlist,
                 'events': s.events[-200:], 'created': s.created}
        with case_store.transaction(s.id) as tx:
            if getattr(s, 'persisted', False) and not tx.execute('SELECT id FROM case_sessions WHERE id=?', (s.id,)).fetchone():
                return  # An owner deletion must not be undone by a late worker event.
            tx.save(s.id, state, getattr(s, 'owner_token', None), kind='legacy')
            if s.events:
                latest = s.events[-1]
                if latest['kind'] in ('question', 'reply'):
                    tx.message(s.id, 'assistant', latest['data'].get('text') or latest['data'].get('question', ''))
            s.persisted = True

    def get_session(sid: str, request: Request) -> WebSession:
        with session_cache_lock:
            return restore_session(sid, request)

    def restore_session(sid: str, request: Request) -> WebSession:
        row = None
        if case_store.enabled():
            with case_store.transaction(sid) as tx:
                row = tx.session(sid, owner(request))
                if row['kind'] != 'legacy':
                    raise HTTPException(404, 'Legacy session not found')
        s = sessions.get(sid)
        if s is None and row:
            state = row['state']
            s = WebSession(state['profile'], state['lang'], planner=llm('planner'), extractor=llm('extractor'),
                           agent=llm('agent'), catalog_loader=catalog_loader, budget=budget)
            s.id, s.profile, s.pending = sid, state['profile'], state.get('pending')
            s.state, s.events = state.get('state', 'done'), state.get('events', [])
            s.next_seq = max((event['seq'] for event in s.events), default=-1) + 1
            s.persisted = True
            s.shortlist, s.situation = state.get('shortlist'), state.get('situation', '')
            s.owner_token, s.owner_hash = owner(request), row['owner_hash']
            s.created, s.on_change = state.get('created', time.time()), persist
            sessions[sid] = s
            if s.state in ('starting', 'investigating'):
                # Never replay costly discovery automatically after a process restart.
                s.state = 'done'
                s.emit('notice', {'text': 'Investigation interrupted by a restart. Saved facts remain available; start a new investigation if needed.'})
                s.emit('done')
        if s is None:
            raise HTTPException(404, "session not found")
        if not row:
            require_memory({'owner_hash': getattr(s, 'owner_hash', '')}, owner(request))
        return s

    @app.get("/")
    def index():
        return HTMLResponse(decorate((STATIC/'landing.html').read_text(encoding='utf-8')),headers={'Cache-Control':'no-cache'})

    @app.get("/app")
    def dashboard():
        html = (STATIC / 'chat.html').read_text(encoding='utf-8')
        html = html.replace('</body>', '<script src="/static/deployment.js" defer></script></body>')
        return HTMLResponse(decorate(html, dashboard=True), headers={"Cache-Control": "no-cache"})

    @app.get('/voice')
    def voice_page():
        return FileResponse(STATIC / 'voice.html', headers={'Cache-Control':'no-store','Referrer-Policy':'no-referrer'})

    @app.get("/chat")
    def chat_redirect():
        return RedirectResponse("/app", status_code=307)

    @app.get("/legacy")
    def legacy():
        return FileResponse(STATIC / "index.html")

    @app.get("/api/catalog")
    def catalog():
        return [{"id": sid, "name": s["name"], "dependencies": [{"id": d["id"], "label": d["label"]} for d in (s.get("dependencies") or [])],
                 "rescue_supported": bool(
            s.get("actions") and s.get("dependencies") is not None
            and (s.get("origin") != "discovered" or s.get("verified", False)))}
            for sid, s in catalog_loader().items()]

    @app.get("/api/form")
    def form():
        fields = load_fields()
        mh = seed("mh")
        return {
            "fields": [{"name": n, "type": fields[n]["type"], "values": fields[n].get("values", []),
                        "min": fields[n].get("min"), "max": fields[n].get("max"),
                        "description": fields[n]["description"]} for n in FORM_FIELDS],
            "districts": {"maharashtra": sorted(mh["districts"])},
        }

    @app.post("/api/sessions")
    def new_session(body: NewSession, request: Request, response: Response):
        raw = {k: v for k, v in body.profile.items() if k in FORM_FIELDS and v not in (None, "")}
        profile, errors = validate_profile(raw)
        if errors:
            raise HTTPException(422, {"errors": errors})
        s = WebSession(profile, body.lang, planner=llm("planner"), extractor=llm("extractor"), agent=llm("agent"),
                       discoverer=discoverer() if body.web else None, catalog_loader=catalog_loader, budget=budget)
        s.owner_token = issue(request, response)
        s.owner_hash = case_store.digest(s.owner_token)
        s.on_change = persist if case_store.enabled() else None
        if len(sessions) >= 100:
            # Never evict a running investigation. Finished sessions can be reloaded from PostgreSQL.
            for identifier, cached in list(sessions.items()):
                if cached.state == 'done':
                    sessions.pop(identifier)
                    break
            else:
                raise HTTPException(503, 'Investigation capacity reached')
        sessions[s.id] = s
        persist(s)
        s.start()
        return {"id": s.id, "profile": s.public_profile()}

    @app.get("/api/sessions/{sid}/events")
    def events(sid: str, request: Request, after: int = 0, once: bool = False):
        s = get_session(sid, request)
        try:
            after = max(0, after, int(request.headers.get('last-event-id', '-1')) + 1)
        except ValueError:
            raise HTTPException(400, 'Invalid event cursor')
        if once:                                             # plain JSON, for tests and debugging
            return [event for event in s.events if event['seq'] >= after]

        def stream():
            n = after
            deadline = time.monotonic() + max(1, min(int(os.getenv('SSE_WINDOW_SECONDS', '25')), 90))
            yield ': connected\nretry: 1000\n\n'
            while time.monotonic() < deadline:
                batch = s.wait_events(n, timeout=1)
                if not batch:
                    yield ": keep-alive\n\n"
                    continue
                for ev in batch:
                    yield f"id: {ev['seq']}\ndata: {json.dumps(ev, ensure_ascii=False, default=str)}\n\n"
                    n = ev["seq"] + 1
        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})

    @app.post("/api/sessions/{sid}/answer")
    def answer(sid: str, body: Answer, request: Request):
        if body.text:
            _safe_message(body.text)
        out = get_session(sid, request).answer(body.field, body.value, body.text, body.skip)
        if "error" in out:
            raise HTTPException(422, out["error"])
        if case_store.enabled():
            with case_store.transaction(sid) as tx:
                tx.message(sid, 'user', body.text or ('Skipped question' if body.skip else json.dumps(out['stored'])))
        return out

    @app.post("/api/sessions/{sid}/ask")
    def ask(sid: str, body: Ask, request: Request):
        _safe_message(body.text)
        get_session(sid, request).ask(body.text)
        if case_store.enabled():
            with case_store.transaction(sid) as tx:
                tx.message(sid, 'user', body.text)
        return {"ok": True}

    @app.get("/api/sessions/{sid}")
    def snapshot(sid: str, request: Request):
        return get_session(sid, request).snapshot()

    @app.delete('/api/sessions/{sid}')
    def delete_session(sid: str, request: Request):
        session = get_session(sid, request)
        if session.state in ('starting', 'investigating'):
            raise HTTPException(409, 'Wait for the investigation to finish before deleting it')
        sessions.pop(sid, None)
        if case_store.enabled():
            with case_store.transaction(sid) as tx:
                tx.execute('DELETE FROM case_sessions WHERE id=?', (sid,))
        return {'deleted': True}

    @app.get("/api/schemes/{scheme_id}")
    def scheme(scheme_id: str):
        s = catalog_loader().get(scheme_id)
        if s is None:
            raise HTTPException(404, "scheme not found")
        rules = (s["eligibility"].get("all") or []) + (s["eligibility"].get("any") or [])
        return {"id": scheme_id, "name": s["name"], "source_url": s["source_url"],
                "source_site": s.get("source_site", ""), "verified": s.get("verified", s.get("origin") != "discovered"),
                "awareness": awareness(s), "agency": s.get("agency_name") or s.get("ministry") or "",
                "benefit": s.get("benefit_summary", ""), "how_to_apply": s.get("how_to_apply", ""),
                "rules": [{"rule": r.get("reason", ""), "quote": r.get("source_quote", "")} for r in rules],
                "check_yourself": s.get("unmapped_criteria", []),
                "documents": [d["name"] for d in s.get("documents") or []]}

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app


app = create_app()
