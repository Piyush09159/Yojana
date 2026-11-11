# Browser voice session (`/voice`)

Code: `web/static/voice.{html,js,css}`, `web/voice.py`, `web/speech.py`.
Tests: `tests/test_multilingual.py` (API), `tests/test_voice_page.py` (static checks), `tests/test_citizen_v25.py`
(handoff, consent, authorization). Real-browser smoke test: `scripts/voice_browser_smoke.py`.

This is a **browser voice session, not a phone call.** It is half duplex: the microphone is closed while Saathi
speaks, so Saathi never transcribes its own voice. We do **not** claim hands-free real-time duplex conversation; that
has not been verified on real mobile browsers.

## Audit: what already worked before this change (2026-11-11)

| Area | Before | Status |
|---|---|---|
| WhatsApp handoff | one-use token in the URL fragment, redeemed by POST, 10-minute expiry, removed from history | kept |
| Case continuity | the same case as WhatsApp; `/start` resumes an active call | kept |
| Turn idempotency | `request_id` per turn; a repeat returns the cached reply, different text gets 409 | kept; the page now reuses ids on retry and refresh |
| Stale speech | `/tts` speaks only the latest assistant reply; an older `request_id` gets 409 | kept |
| Interruption | an "Interrupt / Speak now" tap stops audio and opens the mic | kept |
| Summary consent | an explicit checkbox; outbox with cancel/retry; "accepted by WAHA" is not reported as delivered | kept |
| Speech input | browser `SpeechRecognition` by default (Chrome sends audio to its vendor); server recording only as fallback, stopped by hand, 15 s cap | **changed** |
| Microphone errors | one generic "permission denied or unsupported" message | **improved** |
| Language | per turn only; changing the selector did not reach the case | **improved** |
| Network loss, refresh | the turn failed and the text went back into the box; after a refresh only the case id was kept | **improved** |
| Autoplay blocked (mobile) | fell back to device voice silently | **improved** |
| STT/TTS settings | TTS meant "Sarvam whenever a key exists" | **separated** |

## What changed

1. **Microphone permission.**
   - The Permissions API is checked first.
   - Each failure gets its own message: denied (with a hint about site settings), no microphone, microphone busy (another
     app or call), insecure page, unsupported browser.
   - Typing is always offered and keeps the same session.
2. **Automatic start/end detection (VAD)** on the server-recording path.
   - The page calibrates on the first 0.4 s of background noise.
   - It stops 1.3 s after speech ends, gives up after 8 s of silence ("I did not hear anything"), and caps at 15 s.
   - A level bar shows input. The browser's echo cancellation, noise suppression and auto-gain are requested.
3. **Interruption and stale responses.**
   - Every turn has an epoch and an AbortController. A reply that arrives after a newer turn, a language change or the
     end of the session is dropped.
   - Interrupt cancels playback synchronously within the tap.
4. **Language changes during a session.** Changing the selector calls `POST /api/voice/{cid}/language`.
   - Facts, question progress and the case are kept. The pending question comes back in the new language and is spoken.
   - `auto` clears the preference.
   - A spoken or typed "मराठीत बोला" does the same through the conversation engine.
5. **Clear states** (`body[data-state]`): `idle`, `listening`, `transcribing`, `thinking`, `speaking`, `reconnecting`,
   `ended`, each with a plain-language help line.
6. **Request ids.**
   - Generated with `crypto.randomUUID()`, falling back to `getRandomValues` on older Safari.
   - Reused on every retry, so the server answers retries from its cache.
7. **Refresh and session recovery.** `sessionStorage` (this tab only; never `localStorage`) keeps the case id, the
   call id and the one unsent turn.
   - After a refresh, **Continue saved case** reloads the transcript and language, and resends the unsent turn with
     its original request id.
   - Storage is cleared on 403, 404 and 410.
8. **Network outage, STT failure, TTS failure.**
   - **Offline:** a banner and the `reconnecting` state. The turn is retried up to 3 times (1.5 s, 3 s, 6 s), then
     again on the `online` event.
   - **STT errors** each get their own message:
     - `no_speech`: nothing was heard.
     - `speech_busy`: the STT queue is full.
     - `speech_timeout`: recognition took too long.
     - provider down: speech recognition is unavailable.
     Typed input stays available for all of them.
   - **TTS errors:** device voice, then text.
9. **Typed fallback** posts to the same `/turn` endpoint with the same case and history.
10. **Mobile.**
    - Audio is unlocked on the first tap, and the `<audio>` element is `playsinline`.
    - If autoplay is blocked (`NotAllowedError`), the **Replay last reply** button is highlighted instead of the
      reply being lost.
    - Capture stops when the page is hidden.
    - The recorder format is chosen per browser: webm/opus on Chrome and Android, mp4 on iOS Safari.
11. **Secure handoff.**
    - The one-use link from WhatsApp opens the page, which starts the session itself.
    - The token is removed from the URL before any request, sent only by POST, and never kept in storage.
    - The page opens in the language chosen in WhatsApp (`redeem` returns it).
12. **Consent.** The WhatsApp summary checkbox is unticked by default and nothing is queued without it. Unticking
    cancels a queued summary.
13. **Separately configured STT and TTS.**
    - `BROWSER_STT_MODE`:
      - `server`: record on the page and transcribe with the server STT provider. This is the default when
        `STT_PROVIDER=local` is configured, so recordings stay on our servers.
      - `device`: the browser's own recognition.
      - `auto`: the previous behaviour.
    - `TTS_PROVIDER`: `sarvam` / `browser` / `off`. Unset keeps the previous behaviour: Sarvam when a key exists,
      else the device voice.
14. **Uncertain numbers.** `/api/voice/stt` returns `needs_confirmation` under the same rule as WhatsApp (see
    `WHATSAPP_MENUS.md`). The page then shows the words in the text box ("Check what I heard") and waits for the
    citizen to tap Send instead of submitting automatically.
15. **Hands-free option.** An opt-in checkbox re-opens the microphone after each spoken reply. VAD ends each turn, and
    it stops after 8 s of silence. Off by default.

## Flow

```
WhatsApp "2 / Browser voice call" ──► one-use link (#handoff=…, 10 min)
   │
/voice ── POST /api/voice/redeem ──► session cookie + case id + WhatsApp language
   │     POST /api/voice/start   ──► call id (resumes an active call)
   ▼
 idle ──Listen──► listening (VAD) ──► transcribing (POST /api/voice/stt, local STT)
   ▲                                     │ needs_confirmation → text box, citizen taps Send
   │                                     ▼
   │                                thinking (POST /api/voice/{cid}/turn, request_id)
   │                                     │ offline → reconnecting → same request_id
   │                                     ▼
   └──────────────── speaking (POST /api/voice/tts | device voice | text)
 language selector ──► POST /api/voice/{cid}/language (case kept, question re-asked)
 assessment complete / End ──► ended (summary; WhatsApp copy only with the consent tick)
```

## Verified

- **Pytest** (API, mocked HTTP): language change, idempotency and authorization; handoff language and one-use;
  STT/TTS capability split; TTS off reported with a fallback; `needs_confirmation`; STT timeout mapping; turn retry
  without a second model call.
- **Real browser, 2026-11-11.** Headless Google Chrome 157 on Windows, with a fake microphone fed from the Hindi
  FLEURS clip, the real local `stt_service` (Whisper small, int8, 4 threads) and the API on SQLite with a fake reader
  model (`scripts/voice_browser_smoke.py`). Results:

  | Check | Result |
  |---|---|
  | Microphone denied | "Microphone permission was denied. Allow it in your browser's site settings…"; a typed Hindi turn still answered |
  | Spoken turn, states | idle → listening → transcribing → thinking → speaking; VAD ended the turn on its own; 17.1 s from tapping Listen to the reply (STT 9.0 s) |
  | Language change mid-session | Marathi confirmation plus the pending question in Marathi |
  | Offline turn | `reconnecting` while offline; delivered with the same request id after reconnecting |
  | Refresh | Continue saved case restored all 5 turns and the Marathi selection |
  | 390 px phone viewport | no horizontal scroll; controls visible |
  | Page errors | none |

- **Not verified:**
  - real Android Chrome and iOS Safari on devices: microphone prompts, autoplay, the iOS recorder format;
  - Sarvam TTS (not called: no paid API use);
  - a real WhatsApp handoff link on a phone.

## Mobile-browser acceptance checklist

Run on **Android Chrome** and **iPhone Safari**, on mobile data and on Wi-Fi.

1. In WhatsApp choose Browser voice call and open the link. The page opens in your WhatsApp language and starts by
   itself. Opening the link a second time says it has expired.
2. Tap Listen.
   - The OS microphone prompt appears. Deny it: a clear message appears and typing works.
   - Re-allow it in site settings and tap Listen again.
3. Speak one sentence in Hindi and stop talking. Recording stops on its own after about 1–2 s. You see Transcribing,
   then Thinking, then hear the reply.
4. Say your age in Marathi. The words appear in the text box with "Check what I heard". Correct them if needed and
   tap Send.
5. Tap Interrupt while Saathi speaks. Audio stops at once and the microphone opens. The old reply never resumes.
6. Change the language selector to English mid-question. The same question is asked in English.
7. Turn on airplane mode, type a message and tap Send: Reconnecting. Turn airplane mode off: the reply arrives once,
   with no duplicate message.
8. Refresh the page and tap Continue saved case. The transcript and language come back.
9. Lock the phone while listening. Capture stops; tap Listen to continue.
10. On iOS, if no audio plays after a reply, Replay last reply is highlighted. One tap plays it.
11. Turn on Hands-free. After each reply the microphone reopens. Stay silent for 8 s: it stops and says nothing was
    heard.
12. End the session with the consent box unticked: "No WhatsApp summary requested". Tick it and send: "accepted by
    WAHA", with delivery not claimed.

## EasyPanel

Nothing is required: unset variables keep the previous behaviour, except that recordings now go to local STT
whenever `STT_PROVIDER=local` is configured.

Optional settings on yojana-api:

- `BROWSER_STT_MODE=server|device|auto`
- `TTS_PROVIDER=sarvam|browser|off`, for example `browser` to stop Sarvam TTS spending.
