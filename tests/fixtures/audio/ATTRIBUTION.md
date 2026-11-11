# Audio fixtures

Real speech, used by tests/test_stt_service.py (offline, fake engine) and
tests/test_stt_real_audio.py (opt-in, real faster-whisper model).

Source: FLEURS, Google (https://huggingface.co/datasets/google/fleurs), dev split, licence CC-BY-4.0.
Conneau et al., "FLEURS: Few-shot Learning Evaluation of Universal Representations of Speech", 2022.
Converted from 32-bit float to 16 kHz mono PCM16 WAV (the format the API's ffmpeg step produces).
References are the FLEURS human transcriptions. Read speech by volunteers, not citizen data.

mix_hi_en.wav is hi_3.wav followed by FLEURS en_us dev clip 3 (a language switch inside one note,
not natural Hinglish code-mixing).
