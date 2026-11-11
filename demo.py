"""Terminal demo of the core logic, no WhatsApp or LLM: python demo.py

Asks the questions, shows the recommended scheme, runs the pre-flight check
and decodes a portal status. Answer y/n or a number.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from core import apply_answer, assist, next_best_question  # noqa: E402
from core.fields import load_fields  # noqa: E402


def ask(q, lang):
    # "choice" questions (state, category, ...) list their options in fields.yaml
    choices = q.get("choices") or list(load_fields()[q["fields"][0]].get("values") or [])
    while True:
        prompt = f"\n{q['text_' + lang]}\n"
        if q["type"] == "choice":
            prompt += "  " + ", ".join(f"{i}={c}" for i, c in enumerate(choices, 1)) + "\n"
        raw = input(prompt + "> ").strip().lower()
        if q["type"] == "number":
            if raw.isdigit():
                return int(raw)
        elif q["type"] == "choice":
            if raw.isdigit() and 1 <= int(raw) <= len(choices):
                return choices[int(raw) - 1]
            if raw.replace(" ", "_") in choices:
                return raw.replace(" ", "_")
            print("Please type one of the options or its number.")
            continue
        elif raw in ("y", "yes", "ho", "haan", "ha", "1"):
            return True
        elif raw in ("n", "no", "nahi", "nahin", "2"):
            return False
        print("Please answer y / n (or a number for age).")


def main():
    lang = (input("Language? en / hi / mr [en]: ").strip() or "en")
    lang = lang if lang in ("en", "hi", "mr") else "en"
    profile = {}
    while (q := next_best_question(profile)) is not None:
        profile = apply_answer(profile, q, ask(q, lang))

    out = assist(profile)
    print("\n=== Results ===")
    for r in out["results"]:
        print(f"- {r['why']}")
    print(f"\nStart with: {out['start_with']}   Then: {out['queue']}")

    target = out["start_with"]
    if not target:
        return
    print(f"\n=== Pre-flight for {target} ===")
    records = {}
    for key in ("aadhaar", "land_record", "bank_passbook", "bpl_card", "ration_card"):
        name = input(f"Name exactly as printed on {key} (Enter to skip): ").strip()
        if name:
            records[key] = {"name": name}
    status = input("Portal status, if you already applied (Enter to skip): ").strip() or None
    out = assist(profile, records=records, status=status, scheme_id=target)
    print(json.dumps({k: out[k] for k in ("preflight", "checklist", "status") if k in out},
                     indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
