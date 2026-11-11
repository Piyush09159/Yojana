"""python -m stt_service  ->  uvicorn on STT_HOST:STT_PORT (default 0.0.0.0:8001), one process."""
import os

import uvicorn

from .app import create_app


def main():
    uvicorn.run(create_app(), host=os.getenv('STT_HOST', '0.0.0.0'), port=int(os.getenv('STT_PORT', '8001')),
                workers=1, access_log=False, log_level='warning', proxy_headers=False,
                limit_concurrency=int(os.getenv('STT_MAX_CONNECTIONS', '8')))


if __name__ == '__main__':
    main()
