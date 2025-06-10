from PySide6.QtCore import QThread, Signal
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
import os
from urllib.parse import unquote, urlparse
import re

class ServerThread(QThread):
    # Changed signal to emit a dictionary to receive both url and filename
    url_received = Signal(str)

    def __init__(self, port=12345):
        super().__init__()
        self.port = port
        self.server = None

    def run(self):
        # Capture self (ServerThread instance) for use in the nested Handler class
        outer_self = self

        class Handler(BaseHTTPRequestHandler):
            # Handler for POST requests (receiving URLs and filenames from the extension)
            def do_POST(inner_self):
                content_length = int(inner_self.headers['Content-Length'])
                post_data = inner_self.rfile.read(content_length)
                try:
                    data = json.loads(post_data.decode('utf-8'))
                    # Expecting 'url' and 'filename'
                    if 'url' in data:
                        # Emit the whole data dict (which should contain url and filename)
                        outer_self.url_received.emit(data['url'])
                        inner_self.send_response(200)
                        inner_self.end_headers()
                        inner_self.wfile.write(b'OK')
                        print(f"Received POST request for URL: {data.get('url')} (Filename: {data.get('filename')}), sent 200 OK")
                        return
                except json.JSONDecodeError:
                    print("JSON Decode Error in POST data")
                    pass # Fall through to 400 if JSON is invalid
                except Exception as e:
                    print(f"Error handling POST request: {e}")
                    pass
                inner_self.send_response(400)
                inner_self.end_headers()
                print("Received POST request, sent 400 Bad Request")


            # Handler for HEAD requests (for the extension's liveness check)
            def do_HEAD(inner_self):
                inner_self.send_response(200) # Send 200 OK status
                inner_self.send_header('Content-type', 'text/html') # A common content type, or could be application/json
                inner_self.end_headers() # End headers, no body for HEAD requests
                print("Received HEAD request, sent 200 OK")

            # Disable default logging to keep the console clean
            def log_message(inner_self, format, *args):
                pass

        try:
            self.server = HTTPServer(('localhost', self.port), Handler)
            print(f"Python server listening on http://localhost:{self.port}")
            self.server.serve_forever()
        except Exception as e:
            print(f"Error starting Python server: {e}")

    def stop(self):
        if self.server:
            print("Shutting down Python server...")
            self.server.shutdown() # Shuts down the server's request handling loop
            self.server.server_close() # Closes the server socket
            print("Python server stopped.")

