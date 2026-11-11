"""WER/CER for benchmarks and the opt-in real-audio tests (case/punctuation-insensitive, NFC)."""
import re
import unicodedata


def normalise(text):
    text = unicodedata.normalize('NFC', str(text).lower())
    text = ''.join(' ' if unicodedata.category(c)[0] in 'PS' else c for c in text)
    return re.sub(r'\s+', ' ', text).strip()


def edit_distance(a, b):
    row = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        prev, row[0] = row[0], i
        for j, y in enumerate(b, 1):
            prev, row[j] = row[j], min(row[j] + 1, row[j - 1] + 1, prev + (x != y))
    return row[-1]


def counts(reference, hypothesis):
    """((word errors, reference words), (char errors, reference chars))."""
    r, h = normalise(reference), normalise(hypothesis)
    words = edit_distance(r.split(), h.split()), max(1, len(r.split()))
    chars = edit_distance(r.replace(' ', ''), h.replace(' ', '')), max(1, len(r.replace(' ', '')))
    return words, chars


def wer(reference, hypothesis):
    (e, n), _ = counts(reference, hypothesis)
    return e / n


def cer(reference, hypothesis):
    _, (e, n) = counts(reference, hypothesis)
    return e / n
