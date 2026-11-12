"""WAV validation and silence detection without numpy, so the API contract is testable anywhere."""
import array
import io
import math
import sys
import wave

RATE = 16000
MAX_SECONDS = 15.0
MAX_BYTES = 44 + int(MAX_SECONDS * RATE * 2) + 4096        # header + 15 s PCM16 + slack
# Near-digital silence only. Real FLEURS speech goes down to -65 dBFS RMS (peak 154/32767) and is still
# transcribed, so loudness is not a speech test; the model's VAD handles quiet noise.
SILENCE_PEAK = 32                                           # about -60 dBFS peak


class AudioError(ValueError):
    def __init__(self, code, status=422):
        super().__init__(code)
        self.code, self.status = code, status


def read_pcm16(raw: bytes) -> array.array:
    """Validate a mono 16 kHz PCM16 WAV of at most 15 s and return its samples."""
    if len(raw) > MAX_BYTES:
        raise AudioError('too_large', 413)
    try:
        with wave.open(io.BytesIO(raw), 'rb') as wav:
            if wav.getnchannels() != 1 or wav.getsampwidth() != 2 or wav.getframerate() != RATE:
                raise AudioError('requires_mono_16khz_pcm16_wav')
            frames = wav.getnframes()
            if frames == 0:
                raise AudioError('empty_audio')
            if frames > MAX_SECONDS * RATE:
                raise AudioError('too_long')
            data = wav.readframes(frames)
    except (wave.Error, EOFError):
        raise AudioError('invalid_wav') from None
    if len(data) != frames * 2:
        raise AudioError('truncated_wav')
    samples = array.array('h')
    samples.frombytes(data)
    if sys.byteorder == 'big':
        samples.byteswap()
    return samples


def seconds(samples) -> float:
    return len(samples) / RATE


def dbfs(samples) -> float:
    if not samples:
        return -math.inf
    mean_square = sum(s * s for s in samples) / len(samples)
    return -math.inf if mean_square == 0 else 10 * math.log10(mean_square / 32768.0 ** 2)


def is_silent(samples) -> bool:
    return not samples or max(max(samples), -min(samples)) <= SILENCE_PEAK
