"""python -m pdf_service.examples  ->  render every examples/*.json payload to examples/*.pdf,
plus guides_batch.pdf with all of them (needs WeasyPrint: run inside the image)."""
import json
from pathlib import Path

from pdf_service.models import Guide
from pdf_service.render import WeasyRenderer

HERE = Path(__file__).parent


def main():
    renderer, guides = WeasyRenderer(), []
    for path in sorted(HERE.glob('*.json')):
        guide = Guide.model_validate(json.loads(path.read_text(encoding='utf-8')))
        guides.append(guide)
        pdf = renderer.render([guide])
        path.with_suffix('.pdf').write_bytes(pdf)
        print(path.with_suffix('.pdf').name, len(pdf), 'bytes')
    if len(guides) > 1:
        pdf = renderer.render(guides)
        (HERE / 'guides_batch.pdf').write_bytes(pdf)
        print('guides_batch.pdf', len(pdf), 'bytes')


if __name__ == '__main__':
    main()
