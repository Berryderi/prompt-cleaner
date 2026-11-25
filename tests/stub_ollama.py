"""A local stdlib http.server stand-in for Ollama (/api/ps and /api/chat). No real model involved."""
from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class StubOllama:
    def __init__(self, loaded: list[str] | None = None, chat_delay_s: float = 0.0,
                 reply=lambda text: text):
        self.loaded = loaded or []
        self.chat_delay_s = chat_delay_s
        self.reply = reply
        self.requests: list[str] = []  # "GET /api/ps", "POST /api/chat", ...
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # keep test output quiet
                pass

            def _send(self, body: dict):
                data = json.dumps(body).encode("utf-8")
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                stub.requests.append(f"GET {self.path}")
                if self.path == "/api/ps":
                    self._send({"models": [{"name": n, "model": n} for n in stub.loaded]})
                else:
                    self.send_error(404)

            def do_POST(self):
                stub.requests.append(f"POST {self.path}")
                body = json.loads(self.rfile.read(int(self.headers["content-length"])))
                if self.path != "/api/chat":
                    self.send_error(404)
                    return
                time.sleep(stub.chat_delay_s)
                user = body["messages"][-1]["content"].split("TEXT:\n", 1)[-1]
                try:
                    self._send({"message": {"content": stub.reply(user)}, "done_reason": "stop",
                                "prompt_eval_count": 100})
                except OSError:
                    pass  # client gave up (timeout test)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def chat_calls(self) -> int:
        return self.requests.count("POST /api/chat")

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()
