"""Chat with Scheme Saathi in the terminal, through the same orchestrator WhatsApp and /app use.

    python -m saathi                          # chat
    python -m saathi "मेरी उम्र 62 है ..."      # start with a first message
    python -m saathi --worker                 # also run background jobs after each turn (gap lane:
                                              #   web search, page reading, database update, follow-up)
    python -m saathi --debug                  # show agents used, tokens, profile and good matches
    python -m saathi --classic                # the older chat loop (saathi/chat.py)

Type "restart" to start over, "quit" to exit. Each run is a new conversation unless --chat-id is given.
"""
from __future__ import annotations

import argparse
import sys
import uuid

QUIT = {"quit", "exit", "bye", "q", "बंद", "बाहेर"}
GREETING = ("Namaste! Tell me about yourself in English, Hindi or Marathi: age, state, district, work, "
            "family income, category. Never share Aadhaar, bank or phone numbers.")


def classic(args) -> int:
    from llm import LLMError

    from .chat import Saathi
    bot = Saathi(web=not args.no_web)
    print("\nSaathi> " + bot.greeting() + "\n")
    while True:
        try:
            text = input("You> ").replace("﻿", "").strip()
        except (EOFError, KeyboardInterrupt):
            return 0
        if text.lower() in QUIT:
            return 0
        try:
            print("\nSaathi> " + bot.handle(text) + "\n")
        except LLMError as e:
            print(f"\nSaathi> (Sorry, the language model failed: {e}.)\n")


def main() -> int:
    for stream in (sys.stdout, sys.stdin):
        try:
            stream.reconfigure(encoding="utf-8")     # Devanagari on Windows terminals
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(prog="python -m saathi")
    ap.add_argument("message", nargs="*", help="optional first message")
    ap.add_argument("--worker", action="store_true", help="run ready background jobs after each turn")
    ap.add_argument("--debug", action="store_true")
    ap.add_argument("--chat-id", default=None, help="continue a saved conversation")
    ap.add_argument("--classic", action="store_true", help="use the older chat loop")
    ap.add_argument("--no-web", action="store_true", help="with --classic: no web search")
    args = ap.parse_args()
    if args.classic:
        return classic(args)

    import os

    from store import db

    from .orchestrator import Orchestrator
    if args.worker:
        os.environ.setdefault("NOTIFY_DELAY", "0")     # show follow-ups right away in the terminal
    bot = Orchestrator()
    chat = args.chat_id or "cli-" + uuid.uuid4().hex[:10]
    print(f"\n[database: {db.backend_name()} | conversation: {chat}]")
    print("\nSaathi> " + GREETING + "\n")
    first = " ".join(args.message).strip()
    while True:
        if first:
            text, first = first, ""
            print(f"You> {text}")
        else:
            try:
                text = input("You> ").replace("﻿", "").strip()   # PowerShell pipes may add a BOM
            except (EOFError, KeyboardInterrupt):
                print()
                return 0
        if not text:
            continue
        if text.lower() in QUIT:
            return 0
        turn = bot.handle(chat, text)
        print("\nSaathi> " + turn.reply + "\n")
        if args.debug:
            used = ", ".join(f"{a['agent']}@{a['source']} {a['tokens']}tok {a['ms']}ms" for a in turn.agents) or "none (code)"
            print(f"   [agents: {used} | good matches: {turn.good} | gap job: {turn.gap_job} | "
                  f"degraded: {turn.degraded}]\n   [profile: {turn.profile}]\n")
        if args.worker:
            from worker.loop import run
            ran = run(once=True, out=lambda line: print("   [worker]" + line))
            if ran:
                print("   (background work done; reply anything to see follow-ups)\n")


if __name__ == "__main__":
    sys.exit(main())
