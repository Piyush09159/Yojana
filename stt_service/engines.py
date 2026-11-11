"""STT engines behind one small interface. Heavy imports happen only inside load().

Engine contract:
    engine.load()                       once, at startup (never per request)
    engine.transcribe(samples, hint)    samples: array('h') PCM16 @16 kHz; hint: 'en'|'hi'|'mr'|None
        -> {'text': str, 'language': 'en'|'hi'|'mr', 'language_probability': float|None}

The output is always in the spoken language's own script. Nothing is translated.
"""
from __future__ import annotations

import math
import os
from pathlib import Path

LANGUAGES = ('en', 'hi', 'mr')


class EngineUnavailable(RuntimeError):
    """The model could not be loaded (missing weights, missing package, out of memory)."""


def choose_language(probs: dict, hint: str | None) -> str:
    """Pick en/hi/mr from detection probabilities, using the chat's language as a tie-breaker.

    * Detection is limited to en/hi/mr, so Hindi audio that Whisper labels as Urdu ('ur') still maps to hi.
    * Whisper often confuses Hindi and Marathi (same script). When detection says hi or mr and the
      chat's stored language is the other one, trust the chat's language.
    * When detection is unsure (best allowed probability below 0.5), use the hint.
    """
    best = max(LANGUAGES, key=lambda code: probs.get(code, 0.0))
    if hint not in LANGUAGES:
        return best
    if hint in ('hi', 'mr') and best in ('hi', 'mr'):
        return hint
    if probs.get(best, 0.0) < 0.5:
        return hint
    return best


class FasterWhisperEngine:
    """faster-whisper (CTranslate2) on CPU. Default: multilingual `small`, int8, beam 1."""

    name = 'faster-whisper'

    def __init__(self, model_dir: str, *, model: str = 'small', compute_type: str = 'int8',
                 beam_size: int = 1, cpu_threads: int = 2, allow_download: bool = False):
        self.model_dir, self.model = model_dir, model
        self.compute_type, self.beam_size, self.cpu_threads = compute_type, beam_size, cpu_threads
        self.allow_download = allow_download
        self._model = None

    def load(self):
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise EngineUnavailable('faster_whisper_not_installed') from exc
        path = Path(self.model_dir) / f'faster-whisper-{self.model}'
        source = str(path) if path.exists() else (self.model if self.allow_download else None)
        if source is None:
            raise EngineUnavailable('model_weights_missing')
        try:
            self._model = WhisperModel(source, device='cpu', compute_type=self.compute_type,
                                       cpu_threads=self.cpu_threads, num_workers=1,
                                       local_files_only=not self.allow_download)
        except Exception as exc:                         # corrupt weights, OOM, bad compute type
            raise EngineUnavailable(type(exc).__name__) from exc

    def transcribe(self, samples, hint=None):
        import numpy as np
        audio = np.frombuffer(samples.tobytes(), dtype='<i2').astype(np.float32) / 32768.0
        _, _, all_probs = self._model.detect_language(audio, vad_filter=True)
        language = choose_language(dict(all_probs or []), hint)
        segments, info = self._model.transcribe(
            audio, language=language, task='transcribe', beam_size=self.beam_size,
            # temperature 0 only: no fallback re-decoding loops (one Marathi clip took 53 s with the
            # default 0.0..1.0 schedule and still produced invented text). Bounded, deterministic.
            temperature=0.0, vad_filter=True, condition_on_previous_text=False, without_timestamps=True)
        segments = list(segments)
        text = ' '.join(segment.text.strip() for segment in segments).strip()
        return {'text': text, 'language': language,
                'language_probability': round(float(dict(all_probs or []).get(language, 0.0)), 3),
                'confidence': confidence(segments)}


def confidence(segments):
    """Mean per-token probability of the decoded text, exp(avg_logprob) weighted by token count,
    0..1, or None when nothing was decoded. A rough signal used only to ask the citizen to confirm
    a number before it is used; not a calibrated accuracy."""
    total = weight = 0.0
    for segment in segments:
        n = max(1, len(getattr(segment, 'tokens', None) or []))
        logprob = getattr(segment, 'avg_logprob', None)
        if logprob is None:
            continue
        total += float(logprob) * n
        weight += n
    if not weight:
        return None
    return round(math.exp(total / weight), 3)


class IndicConformerEngine:
    """Adapter for ai4bharat/indic-conformer-600m-multilingual (not the default; see docs/LOCAL_STT.md).

    Constraints checked 2026-11-11: MIT licence, but gated (accept terms with an HF account), loads
    custom code (trust_remote_code), needs torch + torchaudio, 22 Indian languages and **no English**.
    English notes therefore go to `fallback` (a FasterWhisperEngine). Enable only after benchmarking
    Hindi/Marathi on this server, with weights pre-downloaded at a pinned revision into model_dir.
    """

    name = 'indic-conformer'
    REVISION = '2a77d305038dfc502fd9464e7d028b90381976cb'

    def __init__(self, model_dir: str, *, fallback: FasterWhisperEngine, decoding: str = 'ctc'):
        self.model_dir, self.fallback, self.decoding = model_dir, fallback, decoding
        self._model = None

    def load(self):
        path = Path(self.model_dir) / 'indic-conformer-600m-multilingual'
        if not path.exists():
            raise EngineUnavailable('model_weights_missing')
        try:
            import torch  # noqa: F401
            from transformers import AutoModel
        except ImportError as exc:
            raise EngineUnavailable('torch_transformers_not_installed') from exc
        self.fallback.load()                                     # language detection + English
        try:
            self._model = AutoModel.from_pretrained(str(path), trust_remote_code=True, local_files_only=True)
        except Exception as exc:
            raise EngineUnavailable(type(exc).__name__) from exc

    def transcribe(self, samples, hint=None):
        import numpy as np
        import torch
        audio = np.frombuffer(samples.tobytes(), dtype='<i2').astype(np.float32) / 32768.0
        _, _, all_probs = self.fallback._model.detect_language(audio, vad_filter=True)
        language = choose_language(dict(all_probs or []), hint)
        if language == 'en':
            return self.fallback.transcribe(samples, 'en')
        with torch.inference_mode():
            text = self._model(torch.from_numpy(audio).unsqueeze(0), language, self.decoding)
        return {'text': str(text).strip(), 'language': language,
                'language_probability': round(float(dict(all_probs or []).get(language, 0.0)), 3)}


def build_engine(env=os.environ):
    model_dir = env.get('STT_MODEL_DIR', '/models')
    whisper = FasterWhisperEngine(
        model_dir, model=env.get('LOCAL_STT_MODEL', 'small'),
        compute_type=env.get('LOCAL_STT_COMPUTE_TYPE', 'int8'),
        beam_size=int(env.get('LOCAL_STT_BEAM_SIZE', '1')),
        cpu_threads=int(env.get('STT_CPU_THREADS', '2')),
        allow_download=env.get('STT_ALLOW_DOWNLOAD', 'false').lower() == 'true')
    if env.get('LOCAL_STT_ENGINE', 'faster-whisper') == 'indic-conformer':
        return IndicConformerEngine(model_dir, fallback=whisper)
    return whisper
