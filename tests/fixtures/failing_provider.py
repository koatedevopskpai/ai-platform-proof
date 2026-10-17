"""Isolated HTTP 503 provider simulator. Logs metadata only; no request bodies."""
from http.server import BaseHTTPRequestHandler, HTTPServer
import json

calls = 0


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        global calls
        length = int(self.headers.get('content-length', '0'))
        self.rfile.read(length)
        calls += 1
        self.send_response(503)
        self.send_header('content-type', 'application/json')
        self.end_headers()
        self.wfile.write(b'{"error":"controlled provider outage"}')

    def do_GET(self):
        self.send_response(200)
        self.send_header('content-type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps({'failed_provider_calls': calls}).encode())

    def log_message(self, format, *args):
        pass


HTTPServer(('0.0.0.0', 8090), Handler).serve_forever()
