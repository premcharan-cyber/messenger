"""
Messenger web server — Python standard library only (no pip installs needed).

  * Serves the static frontend from ./public
  * REST API for auth, conversations and message history
  * WebSocket endpoint (/ws) for real-time delivery

Run:   python server.py
Open:  http://localhost:3000
"""

import json
import os
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, unquote

from store import Store
from websocket import WebSocket, WebSocketClosed, is_websocket_request

PUBLIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "public")
PORT = int(os.environ.get("PORT", "3000"))

store = Store()

# userId -> set of live WebSocket connections
online = {}
online_lock = threading.Lock()

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".ico": "image/x-icon",
}


# --------------------------------------------------------------------------- #
#  Helpers
# --------------------------------------------------------------------------- #
def parse_cookies(header):
    cookies = {}
    if not header:
        return cookies
    for part in header.split(";"):
        if "=" in part:
            name, _, value = part.partition("=")
            cookies[name.strip()] = unquote(value.strip())
    return cookies


def send_json(handler, status, payload, extra_headers=None):
    body = json.dumps(payload).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    for key, value in (extra_headers or {}).items():
        handler.send_header(key, value)
    handler.end_headers()
    handler.wfile.write(body)


def broadcast_online():
    ids = sorted(online.keys())
    payload = json.dumps({"type": "online", "userIds": ids})
    with online_lock:
        for connections in online.values():
            for ws in list(connections):
                try:
                    ws.send(payload)
                except OSError:
                    pass

def broadcast_user_joined(user_id):
    """Tell every connected client that a new account exists, so their
    sidebar can pick it up without needing a page reload."""
    payload = json.dumps({"type": "user_joined", "userId": int(user_id)})
    with online_lock:
        for connections in online.values():
            for ws in list(connections):
                try:
                    ws.send(payload)
                except OSError:
                    pass


def push_to_user(user_id, payload):
    message = json.dumps(payload)
    sent = False
    with online_lock:
        for ws in list(online.get(int(user_id), ())):
            try:
                ws.send(message)
                sent = True
            except OSError:
                pass
    return sent


# --------------------------------------------------------------------------- #
#  Request handler
# --------------------------------------------------------------------------- #
class MessengerHandler(BaseHTTPRequestHandler):
    server_version = "Messenger/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # quieter logs
        pass

    # ------------------------------ utilities ------------------------------ #
    def current_user(self):
        token = parse_cookies(self.headers.get("Cookie", "")).get("sid")
        return store.get_session_user(token) if token else None

    def read_json_body(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
        except ValueError:
            return {}
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return {}

    # -------------------------------- routing ------------------------------ #
    def do_GET(self):
        if is_websocket_request_raw(self.headers):
            return self.handle_websocket()
        path = urlparse(self.path).path
        if path.startswith("/api/"):
            return self.handle_api_get(path)
        return self.serve_static(path)

    def do_POST(self):
        path = urlparse(self.path).path
        return self.handle_api_post(path)

    # --------------------------------- HTTP -------------------------------- #
    def serve_static(self, path):
        if path in ("/", ""):
            path = "/index.html"
        safe = os.path.normpath(path.lstrip("/\\"))
        if safe.startswith(".."):
            return send_json(self, 400, {"error": "Bad path."})

        full = os.path.join(PUBLIC_DIR, safe)
        if not os.path.isfile(full):
            return send_json(self, 404, {"error": "Not found."})

        ext = os.path.splitext(full)[1].lower()
        with open(full, "rb") as fh:
            body = fh.read()
        self.send_response(200)
        self.send_header(
            "Content-Type", CONTENT_TYPES.get(ext, "application/octet-stream")
        )
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def handle_api_get(self, path):
        user = self.current_user()
        if path == "/api/me":
            if not user:
                return send_json(self, 401, {"error": "Not logged in."})
            return send_json(self, 200, {"user": store.public_user(user)})

        if path == "/api/conversations":
            if not user:
                return send_json(self, 401, {"error": "Not logged in."})
            return send_json(
                self, 200, {"conversations": store.get_conversations_for(user["id"])}
            )

        match = re.match(r"^/api/messages/(\d+)$", path)
        if match:
            if not user:
                return send_json(self, 401, {"error": "Not logged in."})
            other = store.find_user_by_id(int(match.group(1)))
            if not other:
                return send_json(self, 404, {"error": "User not found."})
            messages = store.get_conversation(user["id"], other["id"])
            store.mark_conversation_read(user["id"], other["id"])
            return send_json(
                self,
                200,
                {"user": store.public_user(other), "messages": messages},
            )

        return send_json(self, 404, {"error": "Not found."})

    def handle_api_post(self, path):
        if path == "/api/register":
            data = self.read_json_body()
            try:
                user = store.create_user(
                    data.get("username"), data.get("displayName"), data.get("password")
                )
            except ValueError as exc:
                return send_json(self, 400, {"error": str(exc)})
            token = store.create_session(user["id"])
            # Let everyone already online know there is a new person to chat to.
            broadcast_user_joined(user["id"])
            return send_json(
                self,
                200,
                {"user": store.public_user(user)},
                {"Set-Cookie": f"sid={token}; HttpOnly; Path=/; SameSite=Lax"},
            )

        if path == "/api/login":
            data = self.read_json_body()
            user = store.verify_user(data.get("username"), data.get("password"))
            if not user:
                return send_json(self, 401, {"error": "Invalid username or password."})
            token = store.create_session(user["id"])
            return send_json(
                self,
                200,
                {"user": store.public_user(user)},
                {"Set-Cookie": f"sid={token}; HttpOnly; Path=/; SameSite=Lax"},
            )

        if path == "/api/logout":
            token = parse_cookies(self.headers.get("Cookie", "")).get("sid")
            if token:
                store.destroy_session(token)
            return send_json(
                self,
                200,
                {"ok": True},
                {"Set-Cookie": "sid=; HttpOnly; Path=/; Max-Age=0"},
            )

        return send_json(self, 404, {"error": "Not found."})

    # ------------------------------ WebSocket ------------------------------ #
    def handle_websocket(self):
        user = self.current_user()
        if not user:
            return send_json(self, 401, {"error": "Not logged in."})

        try:
            ws = WebSocket.accept(self.headers.as_string(), self.connection)
        except WebSocketClosed:
            return

        uid = user["id"]
        with online_lock:
            online.setdefault(uid, set()).add(ws)
        self.close_connection = True  # we manage the socket ourselves now
        broadcast_online()

        try:
            self.websocket_loop(ws, uid)
        finally:
            with online_lock:
                connections = online.get(uid)
                if connections:
                    connections.discard(ws)
                    if not connections:
                        online.pop(uid, None)
            try:
                ws.close()
            except OSError:
                pass
            broadcast_online()

    def websocket_loop(self, ws, uid):
        while True:
            try:
                raw = ws.recv()
            except (WebSocketClosed, OSError):
                break
            if raw is None:
                break
            try:
                event = json.loads(raw)
            except ValueError:
                continue

            kind = event.get("type")

            if kind == "send_message":
                recipient = store.find_user_by_id(event.get("to"))
                if not recipient:
                    ws.send(json.dumps({"type": "error", "error": "Recipient not found."}))
                    continue
                try:
                    message = store.save_message(uid, recipient["id"], event.get("text"))
                except ValueError as exc:
                    ws.send(json.dumps({"type": "error", "error": str(exc)}))
                    continue
                # Deliver to the recipient and echo to every tab of the sender.
                push_to_user(recipient["id"], {"type": "message", "message": message})
                push_to_user(uid, {"type": "message", "message": message})

            elif kind == "mark_read":
                other = store.find_user_by_id(event.get("userId"))
                if not other:
                    continue
                store.mark_conversation_read(uid, other["id"])
                push_to_user(
                    other["id"],
                    {"type": "read", "by": uid, "conversationWith": uid},
                )

            elif kind == "ping":
                ws.send(json.dumps({"type": "pong", "at": int(time.time() * 1000)}))


def is_websocket_request_raw(headers):
    return is_websocket_request(headers.as_string())


# --------------------------------------------------------------------------- #
#  Entry point
# --------------------------------------------------------------------------- #
def main():
    store.load()
    server = ThreadingHTTPServer(("0.0.0.0", PORT), MessengerHandler)
    server.daemon_threads = True
    print(f"Messenger running at http://localhost:{PORT}")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
