"""Benchmark the local STT engine on real audio: WER/CER, CPU latency, real-time factor, peak memory.

    python scripts/bench_stt.py --audio-dir <dir with *.wav + manifest.json> --model-dir /models \
        --model small --compute-type int8 --beam 1 --threads 2 [--json out.json]

manifest.json: [{"file": "hi_1.wav", "language": "hi", "reference": "...", "kind": "fleurs"}, ...]
Run one configuration per process so peak memory is per configuration. Needs requirements-stt.txt.
No network. Transcripts are printed only for the benchmark set (public FLEURS sentences).
"""
import argparse
import json
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stt_service import audio as wav                       # noqa: E402
from stt_service.engines import FasterWhisperEngine         # noqa: E402
from stt_service.textmetrics import counts as rates          # noqa: E402


class PeakRSS(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        import psutil
        self.proc, self.peak, self.stop = psutil.Process(), 0, threading.Event()

    def run(self):
        while not self.stop.is_set():
            self.peak = max(self.peak, self.proc.memory_info().rss)
            time.sleep(0.05)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--audio-dir', required=True)
    ap.add_argument('--model-dir', required=True)
    ap.add_argument('--model', default='small')
    ap.add_argument('--compute-type', default='int8')
    ap.add_argument('--beam', type=int, default=1)
    ap.add_argument('--threads', type=int, default=2)
    ap.add_argument('--hint', action='store_true', help='pass the true language as the chat hint')
    ap.add_argument('--json')
    args = ap.parse_args()

    monitor = PeakRSS()
    monitor.start()
    base_rss = monitor.proc.memory_info().rss
    engine = FasterWhisperEngine(args.model_dir, model=args.model, compute_type=args.compute_type,
                                 beam_size=args.beam, cpu_threads=args.threads)
    started = time.perf_counter()
    engine.load()
    load_s = time.perf_counter() - started
    loaded_rss = monitor.proc.memory_info().rss

    manifest = json.loads((Path(args.audio_dir) / 'manifest.json').read_text(encoding='utf-8'))
    rows, totals = [], {}
    for item in manifest:
        samples = wav.read_pcm16((Path(args.audio_dir) / item['file']).read_bytes())
        t = time.perf_counter()
        out = engine.transcribe(samples, item['language'] if args.hint else None)
        ms = (time.perf_counter() - t) * 1000
        (we, wn), (ce, cn) = rates(item['reference'], out['text'])
        key = item.get('kind', 'fleurs') + ':' + item['language']
        agg = totals.setdefault(key, [0, 0, 0, 0, 0, 0.0, 0.0])
        for i, v in enumerate((we, wn, ce, cn, int(out['language'] == item['language']), ms, wav.seconds(samples))):
            agg[i] += v
        rows.append({'file': item['file'], 'expected': item['language'], 'detected': out['language'],
                     'p': out['language_probability'], 'seconds': round(wav.seconds(samples), 2),
                     'ms': int(ms), 'wer': round(we / wn, 3), 'cer': round(ce / cn, 3), 'text': out['text']})
        print(json.dumps(rows[-1], ensure_ascii=False), flush=True)
    monitor.stop.set()
    summary = {
        'config': {k: getattr(args, k) for k in ('model', 'compute_type', 'beam', 'threads', 'hint')},
        'load_seconds': round(load_s, 2),
        'rss_mb': {'before_load': base_rss >> 20, 'after_load': loaded_rss >> 20, 'peak': monitor.peak >> 20},
        'by_language': {k: {'clips': sum(1 for r in manifest if (r.get('kind', 'fleurs') + ':' + r['language']) == k),
                            'wer': round(v[0] / v[1], 3), 'cer': round(v[2] / v[3], 3),
                            'language_correct': v[4], 'mean_ms': int(v[5] / max(1, sum(
                                1 for r in manifest if (r.get('kind', 'fleurs') + ':' + r['language']) == k))),
                            'rtf': round(v[5] / 1000 / v[6], 2)} for k, v in totals.items()},
    }
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    if args.json:
        Path(args.json).write_text(json.dumps({'summary': summary, 'rows': rows}, ensure_ascii=False, indent=1),
                                   encoding='utf-8')


if __name__ == '__main__':
    main()
