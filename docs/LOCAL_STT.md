# Local speech-to-text (yojana-stt)

WhatsApp voice notes and the web voice input no longer need Sarvam. The path is:

```
WAHA --signed webhook--> yojana-api --authenticated download (WAHA_BASE_URL only)--> ffmpeg (16 kHz mono PCM16, <=15 s)
     --POST WAV bytes (private network)--> yojana-stt (faster-whisper small int8, CPU)
     --> existing conversation (Reader + deterministic engine) --> WAHA sendText
```

With `STT_PROVIDER=local`, Sarvam is never called for speech-to-text, even if the local service fails
or Sarvam keys are still set. Text-to-speech is unchanged and still uses Sarvam.

## Components

| File | Purpose |
|---|---|
| `stt_service/app.py` | `POST /transcribe`, `GET /health`, `GET /ready`. One model instance, `STT_CONCURRENCY=1`, bounded queue wait, body limit about 484 KB, optional bearer token. Logs status, timings, duration and language only. The response also carries `confidence` (exp of the mean token log-probability, 0..1), used only to read spoken numbers back before use ([WHATSAPP_MENUS.md](WHATSAPP_MENUS.md)). |
| `stt_service/audio.py` | Accepts mono 16 kHz PCM16 WAV of at most 15 s. Peak-based near-silence check. |
| `stt_service/engines.py` | `FasterWhisperEngine` (default), `IndicConformerEngine` (adapter, off), `choose_language()` (en/hi/mr only, chat-language tie-break) |
| `stt_service/download_model.py` | Pinned weight download (build time or volume init) |
| `Dockerfile.stt`, `requirements-stt.txt` | Separate image with pinned versions. Weights are baked in, so the container runs offline (`HF_HUB_OFFLINE=1`). |
| `web/speech.py` | `stt_provider()` returns `local`, `sarvam` or `off`. `_local_stt()` handles the HTTP call and maps errors. |
| `web/waha_media.py`, `web/waha_bridge.py` | Pass the chat's stored language as a hint (local only). Codes `LOCAL_STT_FAILED`, `VOICE_NO_SPEECH`, `STT_DISABLED`. |
| `scripts/bench_stt.py` | WER/CER, latency, real-time factor and peak RSS on a manifest of real clips |

### Language handling

Whisper detects the language over 99 languages. `choose_language` limits it to en/hi/mr:

* Hindi audio labelled `ur` (Urdu, a common Whisper confusion) becomes `hi`.
* If detection says hi or mr and the chat's stored language is the other one, the chat language wins.
  Whisper small often hears Marathi as Hindi.
* If detection is unsure (below 0.5), the chat language is used.
* Confident English is kept even in a Marathi chat.

Transcription uses `task=transcribe`, so the output is never translated. It is decoded at temperature 0
with no fallback, so latency stays bounded. Without that, one Marathi clip took 53 s and produced
invented English.

## API environment (yojana-api)

| Variable | Value |
|---|---|
| `STT_PROVIDER` | `local` |
| `LOCAL_STT_URL` | `http://<EasyPanel internal hostname of yojana-stt>:8001`. Copy the hostname from the service's **Internal hostname** (EasyPanel uses `<project>_<service>`, like the existing `yojana_waha`). |
| `LOCAL_STT_TOKEN` | Optional shared secret (32 or more random characters), set to the same value on both services |
| `LOCAL_STT_TIMEOUT_SECONDS` | `30` (CPU latency below; 15 s notes take longer on 2 threads) |
| `SARVAM_STT_ENABLED` | `false` (TTS keeps `SARVAM_API_KEY`) |

When `STT_PROVIDER` is unset, the old behaviour stays: Sarvam is used only when `SARVAM_STT_ENABLED=true`.
An unknown value means `off`, and production refuses to start with one.

## STT service environment (yojana-stt)

| Variable | Default | Note |
|---|---|---|
| `LOCAL_STT_MODEL` | `small` | `base` is the low-memory option (see the benchmark) |
| `LOCAL_STT_COMPUTE_TYPE` | `int8` | CPU only. No GPU dependencies. |
| `LOCAL_STT_BEAM_SIZE` | `1` | |
| `STT_CPU_THREADS` / `OMP_NUM_THREADS` | `2` | Match the CPU limit you give the service |
| `STT_CONCURRENCY` | `1` | One model, one note at a time |
| `STT_QUEUE_WAIT_SECONDS` | `20` | After this a waiting request gets `503 busy`, and the citizen gets "voice not available, please type" |
| `STT_MODEL_DIR` | `/models` | |
| `STT_ALLOW_DOWNLOAD` | `false` | Never download at runtime |
| `LOCAL_STT_TOKEN` | empty | Same value as on yojana-api |
| `LOCAL_STT_ENGINE` | `faster-whisper` | `indic-conformer` only after the steps below |

## EasyPanel deployment

1. **Create the service.** In the same project as yojana-api, add an App named `yojana-stt`:
   source = this GitHub repo and branch, build type **Dockerfile**, Dockerfile path **`Dockerfile.stt`**.
   The build downloads the pinned `small` weights (about 464 MB) once.
2. **No domain, no published port.** Leave Domains empty. The service listens on 8001 inside the
   project network only. Do not add a port mapping.
3. **Resources.** Memory limit 1.5 GB (the service process measured 418 MB idle and 558 MB peak with `small`;
   see the benchmark) and a CPU limit of 2–4. Set `STT_CPU_THREADS` to the CPU limit.
4. **Environment.** Set the yojana-stt variables above (defaults are fine). Add `LOCAL_STT_TOKEN` if you use one.
5. **Deploy and wait for readiness.** The container health check calls `/ready`, with a 90 s start period.
   Loading takes about 2 s on the test machine. Logs show `{"event": "model_loaded", ...}`. If you see
   `model_unavailable detail=model_weights_missing`, the build did not bake the weights.
6. **Check from the yojana-api console** (private network):
   ```sh
   python -c "import os,httpx;print(httpx.get(os.environ['LOCAL_STT_URL']+'/ready',timeout=5).json())"
   ```
   Expect `{'status': 'ready', 'engine': 'faster-whisper'}`.
7. **Switch yojana-api.** Set `STT_PROVIDER=local`, `LOCAL_STT_URL`, optional `LOCAL_STT_TOKEN`,
   `LOCAL_STT_TIMEOUT_SECONDS=30` and `SARVAM_STT_ENABLED=false`, then redeploy. Expect `VOICE_CONFIG_OK`
   in the logs, and `GET /api/integrations/waha/status` should show `voice_notes_ready: true`.
8. **Live test** (owner's handset). Send one Hindi, one Marathi and one English note of 5–10 s. Expect logs
   `WAHA_WEBHOOK_RECEIVED → VOICE_TRANSCRIPT_READY source=local → WAHA_REPLY_SENT result=replied`, and
   yojana-stt `{"event": "transcribe", "status": 200, ...}`. There should be no Sarvam STT calls.

### Persistent model volume (alternative to baking)

Build with `--build-arg STT_BAKE_MODELS=""`, mount a volume at `/models`, and run once from the
service console with network access:
`HF_HUB_OFFLINE=0 python -m stt_service.download_model small --dir /models`. Restarts then reuse the volume.

## Rollback

* **Voice only:** on yojana-api set `STT_PROVIDER=off` (citizens get "voice not available, please type")
  and redeploy. Or set `STT_PROVIDER=sarvam` once Sarvam has credits again.
* **Service:** stop yojana-stt. yojana-api answers each note with the same text fallback (`LOCAL_STT_FAILED
  detail=speech_provider_unavailable`). It never fails over to Sarvam.
* Nothing in this change touches Neon, Valkey, WAHA, the worker or the conversation.

## Logs

* yojana-stt: `{"event":"transcribe","status":200,"ms":6900,"seconds":5.46,"language":"mr","no_speech":false}`.
  It never contains text or audio.
* yojana-api: `VOICE_TRANSCRIPT_READY source=local`, `LOCAL_STT_FAILED status=504 detail=speech_timeout`,
  `VOICE_NO_SPEECH`, `STT_DISABLED`

## IndicConformer (`ai4bharat/indic-conformer-600m-multilingual`): adapter only

Checked on 2026-11-11 (pinned revision `2a77d305038d`):

* Licence: MIT. **Gated**: an HF account must accept the access conditions before download.
* **Custom code**: `AutoModel.from_pretrained(..., trust_remote_code=True)`. Review the remote code at
  the pinned revision before enabling it.
* Needs `torch` + `torchaudio` (+ `transformers`). That is a much larger image and roughly 2–3 GB RAM
  for 600M parameters (not measured here).
* Languages: 22 Indian languages including `hi` and `mr`; **no English**. The adapter sends English notes
  and language detection to the Whisper engine, so both models are loaded.
* Decoding: `ctc` or `rnnt`, 16 kHz mono input.

Not benchmarked here: the weights are gated and need the owner's HF account. To evaluate:
accept the terms, download the pinned revision into `/models/indic-conformer-600m-multilingual`, install
torch/torchaudio/transformers (CPU wheels), and run `scripts/bench_stt.py` (extend it to construct
`IndicConformerEngine`) on the same manifest. Switch `LOCAL_STT_ENGINE=indic-conformer` only if Hindi
and Marathi CER clearly improve and memory fits next to yojana-api, the worker and Valkey.

## Benchmark (2026-11-11, measured, not estimated)

**Setup:**
* Machine: Windows 11, 8 logical CPUs, 8 GB RAM. This is a developer machine, not the EasyPanel server,
  so the absolute times will differ there. Re-run `scripts/bench_stt.py` on the server.
* Software: faster-whisper 1.2.1, CTranslate2 4.9.0, CPU int8, VAD on, temperature 0, detection then
  transcription.

**Data:** 18 FLEURS dev clips (6 en, 6 hi, 6 mr; 4.1–14.4 s; human reference transcripts, CC-BY-4.0)
plus 2 language-switch clips (hi→en, mr→en; a FLEURS clip followed by an English clip, not natural
code-mixing). That is a small set: treat the numbers as indicative.

**Metrics:** WER/CER after lowercasing and removing punctuation (`stt_service/textmetrics.py`).

| Config | en WER / CER | hi WER / CER | mr WER / CER | mr language correct | Mean latency per note | Peak RSS* |
|---|---|---|---|---|---|---|
| **small, beam 1, 2 threads (default)** | 0.13 / 0.06 | 0.62 / 0.29 | 1.02 / 0.55 | 5/6 | 7–10 s | 658 MB |
| small, beam 1, 2 threads, chat-language hint | 0.13 / 0.06 | 0.62 / 0.29 | 1.01 / 0.56 | **6/6** | 8–10.5 s | 682 MB |
| small, beam 1, **4 threads** | 0.13 / 0.06 | 0.62 / 0.29 | 1.02 / 0.55 | 5/6 | **6.4–7.4 s** | 652 MB |
| small, beam 5, 2 threads | 0.13 / 0.06 | 0.59 / 0.28 | 0.96 / 0.67 | 5/6 | 7–12 s | 679 MB |
| base, beam 1, 2 threads | 0.11 / 0.06 | 0.97 / **0.95** | 1.24 / **1.21** | 6/6 | **~2 s** | 316 MB |

\* RSS of the benchmark process (model plus harness). For comparison, the real service process over HTTP
(uvicorn, `small`, 2 threads) used **418 MB idle and 558 MB peak** on 15 s notes. The model loads in about 2 s.

**Over real HTTP** (`python -m stt_service`, 2 threads):
* Single notes: 4.9 s note in 7.9 s, 6.4 s in 8.5 s, 5.5 s in 9.3 s, 14.4 s in 11.4 s, 14.2 s in 10.7 s.
* **Three notes sent at once** (`STT_CONCURRENCY=1`): they finished at 9.1 s, 17.9 s and 27.5 s.
  A fourth simultaneous note would pass the 30 s API timeout and get the "type instead" reply.
* Wrong token: 401.

**Findings:**
* **Not real-time on CPU.** Each note costs about 2 Whisper encoder passes over a padded 30 s window, so
  latency is 6–14 s almost regardless of note length.
* **English is good. Hindi is usable** (CER 0.29: mostly spelling and word-split errors; the meaning is
  usually recoverable by the Reader LLM). **Marathi is poor** (CER 0.55, WER ≈ 1.0: many words are wrong,
  some heard as Hindi). This model is not good enough for Marathi-first users. The chat-language hint
  fixes the language label, not the accuracy.
* **Mixed notes:** with one language per note, Whisper transcribes the first language and drops most
  of the English part.
* `base` is 4× faster and half the memory, but writes Hindi in Urdu script or Latin letters, even when
  told the language is Hindi. It is not usable for hi/mr.
* Beam 5 does not help (Marathi gets worse) and is slower. Keep beam 1.
* The default temperature schedule produced a 53 s run and invented English on one Marathi clip.
  Temperature 0 bounds it (worst case after the fix: 14.7 s).
* One run once stalled for 235 s on an English clip; reruns took 5.5–7.2 s. This was most likely a host
  stall, and it is the reason for the client timeout and the bounded queue.

**Recommendation:** deploy `small`/int8/beam 1 with **4 CPU threads if the server can spare them**. Before
relying on Marathi voice, benchmark IndicConformer (gated, see above) or a Marathi fine-tuned Whisper on the
same manifest.

Reproduce:
```sh
python -m stt_service.download_model small base --dir ./models
python scripts/bench_stt.py --audio-dir <fleurs dir with manifest.json> --model-dir ./models --model small --threads 4
STT_TEST_MODEL_DIR=./models python -m pytest -q tests/test_stt_real_audio.py
```

## Known limits

* **The WAHA webhook stays open during transcription** (8–15 s). If WAHA's webhook client times out
  and retries meanwhile, the retry gets HTTP 503 (`WAHA_WEBHOOK_IGNORED detail=in_flight_retry`) and the
  note is transcribed once. Check WAHA's webhook retry settings (`WHATSAPP_HOOK_RETRIES_*`); a few
  attempts with a delay of 2 s or more is enough.
* The in-flight guard and transcript cache are per process. yojana-api runs one uvicorn worker.
