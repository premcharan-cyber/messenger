"""
Minimal RFC 6455 WebSocket server implemented with only the Python
standard library. Enough for this messenger: text frames, ping/pong,
and close frames. No compression, no fragmentation beyond what we send.
"""

import base64
import hashlib
import struct
GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


class WebSocketClosed(Exception):
    pass


class WebSocket:
    def __init__(self, sock):
        self.sock = sock

    # ------------------------------ handshake ------------------------------
    @staticmethod
    def accept(headers, sock):
        key = None
        for line in headers.replace("\r\n", "\n").split("\n"):
            if line.lower().startswith("sec-websocket-key:"):
                key = line.split(":", 1)[1].strip()
                break
        if key is None:
            raise WebSocketClosed("missing Sec-WebSocket-Key")

        accept_val = base64.b64encode(
            hashlib.sha1((key + GUID).encode("ascii")).digest()
        ).decode("ascii")

        response = (
            "HTTP/1.1 101 Switching Protocols\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Accept: {accept_val}\r\n\r\n"
        )
        sock.sendall(response.encode("ascii"))
        return WebSocket(sock)

    # ------------------------------- receiving -----------------------------
    def _recv_exact(self, n):
        data = b""
        while len(data) < n:
            chunk = self.sock.recv(n - len(data))
            if not chunk:
                raise WebSocketClosed("connection closed")
            data += chunk
        return data

    def recv(self):
        """Return the next text payload, or None if the peer closed."""
        while True:
            try:
                head = self._recv_exact(2)
            except WebSocketClosed:
                return None

            fin = head[0] & 0x80
            opcode = head[0] & 0x0F
            masked = head[1] & 0x80
            length = head[1] & 0x7F

            if length == 126:
                length = struct.unpack("!H", self._recv_exact(2))[0]
            elif length == 127:
                length = struct.unpack("!Q", self._recv_exact(8))[0]

            mask = self._recv_exact(4) if masked else b"\x00\x00\x00\x00"
            payload = self._recv_exact(length) if length else b""

            if masked:
                payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))

            if opcode == 0x8:  # close
                return None
            if opcode == 0x9:  # ping -> pong
                self._send_frame(0xA, payload)
                continue
            if opcode == 0xA:  # pong
                continue

            # text (0x1) or continuation handled as raw bytes
            try:
                return payload.decode("utf-8")
            except UnicodeDecodeError:
                return ""

    # -------------------------------- sending ------------------------------
    def _send_frame(self, opcode, payload):
        if isinstance(payload, str):
            payload = payload.encode("utf-8")
        length = len(payload)
        header = bytearray()
        header.append(0x80 | opcode)  # FIN + opcode

        if length < 126:
            header.append(length)
        elif length < 65536:
            header.append(126)
            header += struct.pack("!H", length)
        else:
            header.append(127)
            header += struct.pack("!Q", length)

        # Server-to-client frames are not masked.
        self.sock.sendall(bytes(header) + payload)

    def send(self, text):
        self._send_frame(0x1, text)

    def close(self):
        try:
            self._send_frame(0x8, b"")
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass


def is_websocket_request(headers):
    headers_lower = headers.replace("\r\n", "\n").lower()
    return (
        "upgrade: websocket" in headers_lower
        and "sec-websocket-key" in headers_lower
    )
