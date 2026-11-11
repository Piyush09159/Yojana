"""Write synthetic example payloads for the PDF service from the real engine and catalog.

    python scripts/make_pdf_examples.py              # -> pdf_service/examples/guide_{en,hi,mr}.json

The people are invented (no real citizen data). Scheme content comes from the catalog through
the same code the backend uses (web/pdf_client.guide_from_cards), so the examples show exactly
what a real guide would contain. Render them to PDF inside the image:

    docker run --rm -v "$PWD/pdf_service/examples:/app/pdf_service/examples" yojana-pdf python -m pdf_service.examples
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
# The bundled SQLite catalog, never a server. Read from a temporary copy: opening data/schemes.db
# applies the schema and WAL mode, which would change the committed file.
os.environ['SCHEME_DATABASE_URL'] = ''
_COPY = Path(tempfile.mkdtemp(prefix='pdf-examples-')) / 'schemes.db'
shutil.copy(ROOT / 'data' / 'schemes.db', _COPY)
os.environ['SCHEME_DB'] = str(_COPY)

from core.assessment import relevant_catalog          # noqa: E402
from discovery.catalog import catalog_for             # noqa: E402
from saathi.shortlist import build_shortlist          # noqa: E402
from web.pdf_client import guide_from_cards, shortlist_cards  # noqa: E402

OUT = ROOT / 'pdf_service' / 'examples'
DAY = date(2026, 11, 11).isoformat()
PEOPLE = {   # synthetic: invented names and facts
    'en': ('Ravi Kumar (sample)', {'age': 62, 'gender': 'male', 'state': 'maharashtra', 'district': 'beed',
                                   'occupation': 'farmer', 'owns_cultivable_land': True, 'is_bpl': True,
                                   'annual_family_income_inr': 80000}),
    'hi': ('सुनीता देवी (नमूना)', {'age': 45, 'gender': 'female', 'marital_status': 'widowed', 'state': 'maharashtra',
                                  'district': 'pune', 'annual_family_income_inr': 60000, 'is_bpl': True}),
    'mr': ('गणेश पाटील (नमुना)', {'age': 66, 'gender': 'male', 'state': 'maharashtra', 'district': 'jalgaon',
                                 'is_bpl': True, 'occupation': 'agricultural_labourer'}),
}


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    for language, (name, profile) in PEOPLE.items():
        catalog = relevant_catalog(profile, catalog_for(profile))
        cards = shortlist_cards(build_shortlist({**profile, '_asked': []}, catalog))
        guide = guide_from_cards(cards, catalog, language=language, recipient_name=name, reference=f'SAMPLE-{language.upper()}')
        guide['generated_on'] = DAY
        path = OUT / f'guide_{language}.json'
        path.write_text(json.dumps(guide, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print(path.relative_to(ROOT), len(guide['schemes']), 'schemes:', ', '.join(s['id'] for s in guide['schemes']))


if __name__ == '__main__':
    main()
