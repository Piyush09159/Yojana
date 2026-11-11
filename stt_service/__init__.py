"""Self-hosted speech-to-text microservice (private network only).

Runs as its own container so the model's memory never lives inside yojana-api or yojana-worker.
yojana-api sends 16 kHz mono PCM16 WAV bytes (already decoded by ffmpeg) to POST /transcribe.
Nothing is written to disk and transcripts are never logged.
"""
