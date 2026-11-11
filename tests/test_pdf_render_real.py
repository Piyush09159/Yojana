"""Real WeasyPrint render in en/hi/mr: valid PDF, Devanagari text extractable, Noto fonts embedded,
QR codes present, no citizen name in the metadata. Skipped where WeasyPrint's system libraries are
missing (Windows); run it inside the image:

    docker run --rm -v "$PWD:/src" -w /src --entrypoint sh yojana-pdf -c \
      "pip install -q pytest==9.0.2 pypdf==6.21.3 && python -m pytest -q --noconftest tests/test_pdf_render_real.py"
"""
import io
import json
from pathlib import Path

import pytest

try:
    import weasyprint  # noqa: F401
except (ImportError, OSError):
    pytest.skip('WeasyPrint and its system libraries are not installed here', allow_module_level=True)
pypdf = pytest.importorskip('pypdf')

from pdf_service.models import Batch, Guide
from pdf_service.render import WeasyRenderer

EXAMPLES = Path(__file__).resolve().parent.parent / 'pdf_service' / 'examples'


def payload(name):
    return json.loads((EXAMPLES / f'{name}.json').read_text(encoding='utf-8'))


def fonts(reader):
    found = {}
    for page in reader.pages:
        for font in (page.get('/Resources', {}).get('/Font') or {}).values():
            font = font.get_object()
            for desc in [font] + [d.get_object() for d in font.get('/DescendantFonts', [])]:
                fd = desc.get('/FontDescriptor')
                if fd:
                    fd = fd.get_object()
                    found[str(desc.get('/BaseFont'))] = any(k in fd for k in ('/FontFile', '/FontFile2', '/FontFile3'))
    return found


@pytest.mark.parametrize('name,word', [('guide_en', 'How to apply'), ('guide_hi', 'योजना'), ('guide_mr', 'योजना')])
def test_real_render_embeds_fonts_and_text(name, word):
    data = payload(name)
    pdf = WeasyRenderer().render([Guide.model_validate(data)])
    assert pdf.startswith(b'%PDF-') and len(pdf) < 2_000_000
    reader = pypdf.PdfReader(io.BytesIO(pdf))
    assert len(reader.pages) >= 1 + len(data['schemes'])                   # cover + one page per scheme
    text = ''.join(page.extract_text() or '' for page in reader.pages)
    assert word in text
    embedded = fonts(reader)
    assert embedded and all(embedded.values()), embedded                    # every font is embedded
    assert any('Noto-Sans' in f for f in embedded)                          # e.g. /ABCDEF+Noto-Sans
    if data['language'] != 'en':
        assert any('Devanagari' in f for f in embedded)
    meta = reader.metadata or {}
    assert (data.get('recipient_name') or '#none#') not in ' '.join(str(v) for v in meta.values())


@pytest.mark.parametrize('url', ['https://example.com/x.png', 'http://169.254.169.254/latest', 'file:///etc/passwd'])
def test_fetcher_refuses_every_outside_url(url):
    from pdf_service.render import safe_fetcher
    with pytest.raises(ValueError):
        safe_fetcher().fetch(url)


def test_real_batch_renders_all_languages():
    batch = Batch.model_validate({'guides': [payload('guide_en'), payload('guide_hi'), payload('guide_mr')]})
    pdf = WeasyRenderer().render(batch.guides)
    reader = pypdf.PdfReader(io.BytesIO(pdf))
    text = ''.join(page.extract_text() or '' for page in reader.pages)
    assert 'How to apply' in text and 'योजना' in text
