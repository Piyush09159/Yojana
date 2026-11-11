"""python -m pdf_service  ->  uvicorn on PDF_HOST:PORT (Render sets PORT; else PDF_PORT, default 8002)."""
import os

import uvicorn

from .app import create_app


def main():
    port = int(os.getenv('PORT') or os.getenv('PDF_PORT') or '8002')
    uvicorn.run(create_app(), host=os.getenv('PDF_HOST', '0.0.0.0'), port=port,
                workers=1, access_log=False, log_level='warning', proxy_headers=False,
                limit_concurrency=int(os.getenv('PDF_MAX_CONNECTIONS', '16')))


if __name__ == '__main__':
    main()
