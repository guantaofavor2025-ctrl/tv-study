#!/usr/bin/env python3
"""
TV Study - Combined static file server + AI proxy (with streaming)
Run:  python3 server.py [port]
Then open: http://localhost:8080/index.html
"""

import http.server
import socketserver
import json
import urllib.request
import urllib.error
import os
import sys
import time

# ===== CONFIG =====
AI_ENDPOINT = "https://www.nxaihub.com/v1/chat/completions"
AI_API_KEY = "sk-SoVyNtOx9hXwZVSmFJ32athJNX0kakwM9qL4IJL2XklzJKDs"
AI_MODEL = "autorouter"
MAX_TOKENS = 800                              # ~300 Chinese characters
REQUEST_TIMEOUT = 20

WWW_ROOT = os.path.dirname(os.path.abspath(__file__))

SYSTEM_PROMPT = (
    "你是专业英文翻译助手。用中文简要解释用户提供的英文文本，"
    "格式：\n"
    "1) **中文翻译** — 一句话概括含义\n"
    "2) **关键词** — 格式：**单词** /音标/ — 释义\n"
    "3) **文化背景** — 有则简述，无则省略\n\n"
    "简洁，不超过200字。务必为每个关键词标注IPA音标。"
)


class TVStudyHandler(http.server.SimpleHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=WWW_ROOT, **kwargs)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.end_headers()

    def do_POST(self):
        if self.path == "/api/ai":
            self._handle_ai_proxy()
            return
        if self.path == "/api/ai/stream":
            self._handle_ai_stream()
            return
        if self.path == "/api/ping":
            self._send_json({"ok": True, "proxy": True})
            return
        super().do_POST()

    def do_GET(self):
        if self.path == "/api/ping":
            self._send_json({"ok": True, "proxy": True})
            return
        super().do_GET()

    def _handle_ai_stream(self):
        """Relay streaming SSE from DashScope to browser."""
        try:
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length)
            payload = json.loads(body)
            user_text = payload.get("text", "")
            if not user_text:
                self._send_json({"error": "No text provided"}, status=400)
                return

            messages = [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"请解释以下英文文本的中文含义：\n\n{user_text}"}
            ]

            req_body = json.dumps({
                "model": AI_MODEL,
                "messages": messages,
                "max_tokens": MAX_TOKENS,
                "stream": True
            }).encode("utf-8")

            req = urllib.request.Request(
                AI_ENDPOINT,
                data=req_body,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {AI_API_KEY}",
                },
                method="POST"
            )

            # SSE response headers
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("X-Accel-Buffering", "no")  # disable nginx buffering
            self.end_headers()

            resp = urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT)
            buffer = ""
            while True:
                chunk = resp.read(4096)
                if not chunk:
                    break
                buffer += chunk.decode("utf-8")
                # Forward complete SSE lines
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    line = line.strip()
                    if line:
                        self.wfile.write((line + "\n").encode("utf-8"))
                        self.wfile.flush()

            # Flush any remaining buffer
            if buffer.strip():
                self.wfile.write((buffer.strip() + "\n").encode("utf-8"))
                self.wfile.flush()

        except urllib.error.HTTPError as e:
            try:
                err_body = e.read().decode("utf-8")[:500]
            except Exception:
                err_body = str(e)
            self.wfile.write(f'data: [ERROR] API错误 ({e.code}): {err_body}\n\n'.encode("utf-8"))
            self.wfile.flush()
        except Exception as e:
            self.wfile.write(f'data: [ERROR] {str(e)}\n\n'.encode("utf-8"))
            self.wfile.flush()

    def _handle_ai_proxy(self):
        """Non-streaming fallback (kept for compatibility)."""
        try:
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length)
            payload = json.loads(body)
            user_text = payload.get("text", "")
            if not user_text:
                self._send_json({"error": "No text provided"}, status=400)
                return

            messages = [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"请解释以下英文文本的中文含义：\n\n{user_text}"}
            ]

            req_body = json.dumps({
                "model": AI_MODEL,
                "messages": messages,
                "max_tokens": MAX_TOKENS,
            }).encode("utf-8")

            req = urllib.request.Request(
                AI_ENDPOINT,
                data=req_body,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {AI_API_KEY}",
                },
                method="POST"
            )

            resp = urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT)
            result = json.loads(resp.read().decode("utf-8"))
            ai_text = result["choices"][0]["message"]["content"]
            self._send_json({"text": ai_text})

        except urllib.error.HTTPError as e:
            try:
                err_body = e.read().decode("utf-8")[:500]
            except Exception:
                err_body = str(e)
            self._send_json({"error": f"API错误 ({e.code}): {err_body}"}, status=500)
        except Exception as e:
            self._send_json({"error": str(e)}, status=500)

    def _send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        print(f"[{self.log_date_time_string()}] {args[0]}")


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8080
    os.chdir(WWW_ROOT)

    socketserver.TCPServer.allow_reuse_address = True
    httpd = socketserver.TCPServer(("", port), TVStudyHandler)
    try:
        print(f"✓ TV Study server running at http://localhost:{port}")
        print(f"  Open: http://localhost:{port}/index.html")
        print(f"  AI proxy: http://localhost:{port}/api/ai")
        print(f"  AI stream: http://localhost:{port}/api/ai/stream")
        print(f"  Model: {AI_MODEL}  |  max_tokens: {MAX_TOKENS}")
        print(f"  Ctrl+C to stop")
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n✓ Server stopped")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
