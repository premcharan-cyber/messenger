"""End-to-end check for the messenger server (no external deps).

Covers: static files, register/login/session cookies, conversations,
message history, and live delivery over a raw WebSocket connection.
Run the server first, then:  python verify.py
"""
import base64
import hashlib
import json
import os
import random
import socket
import struct
import sys
import urllib.error
import urllib.request

BASE = f"http://127.0.0.1:{os.environ.get('PORT', '3000')}"


# ------------------------------- HTTP helpers ------------------------------- #
def request(method, path, body=None, cookie=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if cookie:
        req.add_header("Cookie", cookie)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            raw = resp.read()
            set_cookie = resp.headers.get("Set-Cookie")
            try:
                payload = json.loads(raw)
            except ValueError:
                payload = raw.decode(errors="replace")
            return resp.status, payload, set_cookie
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}"), exc.headers.get("Set-Cookie")


def cookie_of(set_cookie):
    return set_cookie.split(";", 1)[0] if set_cookie else None


# ----------------------------- WebSocket client ----------------------------- #
class WSClient:
    def __init__(self, host, port, path, cookie):
        self.sock = socket.create_connection((host, port), timeout=5)
        key = base64.b64encode(os.urandom(16)).decode()
        handshake = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            f"Cookie: {cookie}\r\n\r\n"
        )
        self.sock.sendall(handshake.encode())
        resp = b""
        while b"\r\n\r\n" not in resp:
            resp += self.sock.recv(4096)
        assert b"101" in resp.split(b"\r\n", 1)[0], f"handshake failed: {resp[:120]!r}"

    def send(self, text):
        payload = text.encode()
        mask = os.urandom(4)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        n = len(payload)
        if n < 126:
            header = bytes([0x81, 0x80 | n])
        elif n < 65536:
            header = bytes([0x81, 0x80 | 126]) + struct.pack("!H", n)
        else:
            header = bytes([0x81, 0x80 | 127]) + struct.pack("!Q", n)
        self.sock.sendall(header + mask + masked)

    def recv(self):
        head = self.sock.recv(2)
        if len(head) < 2:
            return None
        length = head[1] & 0x7F
        if length == 126:
            length = struct.unpack("!H", self.sock.recv(2))[0]
        elif length == 127:
            length = struct.unpack("!Q", self.sock.recv(8))[0]
        data = b""
        while len(data) < length:
            chunk = self.sock.recv(length - len(data))
            if not chunk:
                break
            data += chunk
        return data.decode()

    def read_event(self, kind, tries=6):
        for _ in range(tries):
            raw = self.recv()
            if raw is None:
                return None
            event = json.loads(raw)
            if event.get("type") == kind:
                return event
        return None

    def close(self):
        self.sock.close()


# ---------------------------------- tests ----------------------------------- #
def main():
    passed, failed = 0, 0

    def check(name, condition, detail=""):
        nonlocal passed, failed
        if condition:
            passed += 1
            print(f"  PASS  {name}")
        else:
            failed += 1
            print(f"  FAIL  {name} {detail}")

    print("Messenger verification\n")

    # 1. static files
    for path, needle in [("/", "Messenger"), ("/style.css", ".app"), ("/app.js", "WebSocket")]:
        status, body, _ = request("GET", path)
        check(f"static {path}", status == 200 and needle in body)

    # 2. register two users
    suffix = random.randint(10000, 99999)
    users = {}
    for who in ("alice", "bob"):
        username = f"{who}{suffix}"
        status, payload, sc = request(
            "POST", "/api/register",
            {"username": username, "displayName": who.title(), "password": "secret123"},
        )
        check(f"register {who}", status == 200 and payload.get("user"), payload)
        users[who] = {"cookie": cookie_of(sc), "user": payload.get("user")}

    # 3. duplicate username rejected
    status, payload, _ = request(
        "POST", "/api/register",
        {"username": f"alice{suffix}", "password": "secret123"},
    )
    check("duplicate username rejected", status == 400, payload)

    # 4. wrong password rejected
    status, payload, _ = request(
        "POST", "/api/login", {"username": f"alice{suffix}", "password": "wrong"}
    )
    check("bad password rejected", status == 401, payload)

    # 5. session persistence via /api/me
    status, payload, _ = request("GET", "/api/me", cookie=users["alice"]["cookie"])
    check("session /api/me", status == 200 and payload["user"]["username"] == f"alice{suffix}")

    # 6. unauthenticated access blocked
    status, payload, _ = request("GET", "/api/conversations")
    check("anonymous blocked", status == 401, payload)

    alice, bob = users["alice"], users["bob"]
    alice_id, bob_id = alice["user"]["id"], bob["user"]["id"]

    # 7. WebSocket handshake for both users
    ws_a = WSClient("127.0.0.1", int(os.environ.get("PORT", "3000")), "/ws", alice["cookie"])
    ws_b = WSClient("127.0.0.1", int(os.environ.get("PORT", "3000")), "/ws", bob["cookie"])
    check("websocket upgrade accepted", True)
    check("presence broadcast", ws_a.read_event("online") is not None)

    # 8. real-time delivery alice -> bob
    ws_a.send(json.dumps({"type": "send_message", "to": bob_id, "text": "hello bob"}))
    echo = ws_a.read_event("message")
    delivered = ws_b.read_event("message")
    check("sender echo", echo is not None and echo["message"]["text"] == "hello bob")
    check("recipient delivery", delivered is not None and delivered["message"]["text"] == "hello bob")
    check("delivery direction", delivered and delivered["message"]["from"] == alice_id)

    # 9. bob replies -> alice
    ws_b.send(json.dumps({"type": "send_message", "to": alice_id, "text": "hi alice"}))
    check("reply delivery", (ws_a.read_event("message") or {}).get("message", {}).get("text") == "hi alice")

    # 10. history stored
    status, payload, _ = request("GET", f"/api/messages/{bob_id}", cookie=alice["cookie"])
    texts = [m["text"] for m in payload.get("messages", [])]
    check("history persisted", status == 200 and texts == ["hello bob", "hi alice"], texts)

    # 11. empty message rejected over socket
    ws_a.send(json.dumps({"type": "send_message", "to": bob_id, "text": "   "}))
    check("empty message rejected", ws_a.read_event("error") is not None)

    # 12. unread count + mark read
    ws_a.send(json.dumps({"type": "send_message", "to": bob_id, "text": "third"}))
    ws_b.read_event("message")
    ws_a.read_event("message")
    status, payload, _ = request("GET", "/api/conversations", cookie=bob["cookie"])
    convo = next(c for c in payload["conversations"] if c["user"]["id"] == alice_id)
    check("unread badge counted", convo["unread"] == 2, convo)
    request("GET", f"/api/messages/{alice_id}", cookie=bob["cookie"])  # marks read
    status, payload, _ = request("GET", "/api/conversations", cookie=bob["cookie"])
    convo = next(c for c in payload["conversations"] if c["user"]["id"] == alice_id)
    check("unread cleared after read", convo["unread"] == 0, convo)

    # 13. logout invalidates session
    request("POST", "/api/logout", cookie=alice["cookie"])
    status, _, _ = request("GET", "/api/me", cookie=alice["cookie"])
    check("logout clears session", status == 401, status)

    ws_a.close()
    ws_b.close()

    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
