from PySide6.QtCore import QThread, Signal
import json
from http.server import BaseHTTPRequestHandler, HTTPServer

class ServerThread(QThread):
    url_received = Signal(str)

    def __init__(self, port=12345):
        super().__init__()
        self.port = port
        self.server = None

    def run(self):
        class Handler(BaseHTTPRequestHandler):
            def do_POST(inner_self):
                content_length = int(inner_self.headers['Content-Length'])
                post_data = inner_self.rfile.read(content_length)
                try:
                    data = json.loads(post_data.decode('utf-8'))
                    if 'url' in data:
                        self.url_received.emit(data['url'])
                        inner_self.send_response(200)
                        inner_self.end_headers()
                        inner_self.wfile.write(b'OK')
                        return
                except json.JSONDecodeError:
                    pass
                inner_self.send_response(400)
                inner_self.end_headers()

            def log_message(inner_self, format, *args):
                pass  # Disable logging

        self.server = HTTPServer(('localhost', self.port), Handler)
        self.server.serve_forever()

    def stop(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()
