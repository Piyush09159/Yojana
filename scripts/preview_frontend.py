"""Local proxy simulator for built Vercel output; not a production server.

python scripts/preview_frontend.py --backend http://127.0.0.1:8000 --port 8080
Tests static routes, cookies, failure handling and unbuffered SSE. Real Vercel
edge behavior must still be smoke-tested after deployment.
"""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import mimetypes
from urllib.parse import urlsplit
import httpx

ROOT = Path(__file__).resolve().parents[1] / 'frontend/.vercel/output/static'
DROP = {'host', 'connection', 'transfer-encoding', 'content-encoding', 'content-length'}


def serve(backend, port):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.0'

        def log_message(self, *args):
            pass  # Cookie/request bodies and webhook paths are never logged.

        def handle_request(self):
            route = urlsplit(self.path).path
            if route.startswith('/api/') or route in ('/docs', '/openapi.json'):
                body = self.rfile.read(int(self.headers.get('Content-Length', '0')))
                headers = {k: v for k, v in self.headers.items() if k.lower() not in DROP}
                try:
                    with httpx.Client(timeout=40) as client:
                        with client.stream(self.command, backend + self.path, content=body, headers=headers) as response:
                            self.send_response(response.status_code)
                            for k, v in response.headers.multi_items():
                                if k.lower() not in DROP:
                                    self.send_header(k, v)
                            self.end_headers()
                            for chunk in response.iter_bytes():
                                self.wfile.write(chunk)
                                self.wfile.flush()
                except (httpx.HTTPError, BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    self.close_connection = True
                return
            if route.rstrip('/') == '/chat':
                self.send_response(307)
                self.send_header('Location', '/app')
                self.end_headers()
                return
            target = {'/': 'index.html', '/app': 'app.html', '/legacy': 'legacy.html','/voice':'voice.html'}.get(route, route.lstrip('/'))
            path = (ROOT / target).resolve()
            if not path.is_relative_to(ROOT.resolve()) or not path.is_file():
                self.send_error(404)
                return
            data = path.read_bytes()
            self.send_response(200)
            self.send_header('Content-Type', mimetypes.guess_type(str(path))[0] or 'application/octet-stream')
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-cache')
            self.end_headers()
            self.wfile.write(data)
        do_GET = do_POST = do_DELETE = do_PATCH = handle_request

    print(f'Local Vercel proxy simulation at http://127.0.0.1:{port}', flush=True)
    ThreadingHTTPServer(('127.0.0.1', port), Handler).serve_forever()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--backend', required=True)
    parser.add_argument('--port', type=int, default=8080)
    args = parser.parse_args()
    serve(args.backend.rstrip('/'), args.port)
