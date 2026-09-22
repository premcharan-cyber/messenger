"""
Data layer for the messenger, backed by a JSON file.
Standard library only (passwords hashed with hashlib.scrypt).
"""

import hashlib
import json
import os
import re
import secrets
import time

DB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
DB_FILE = os.path.join(DB_DIR, "db.json")

_USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{3,20}$")


class Store:
    def __init__(self):
        self.users = []
        self.messages = []
        self.sessions = {}  # token -> {"userId": int, "createdAt": float}
        self._last_user_id = 0
        self._last_message_id = 0

    # ------------------------------- persistence ----------------------------
    def load(self):
        if not os.path.exists(DB_FILE):
            return
        try:
            with open(DB_FILE, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            self.users = data.get("users", [])
            self.messages = data.get("messages", [])
            self.sessions = data.get("sessions", {})
            self._last_user_id = data.get("lastIds", {}).get("user", 0)
            self._last_message_id = data.get("lastIds", {}).get("message", 0)
        except (OSError, ValueError) as exc:
            print(f"[store] Could not read db.json, starting fresh: {exc}")

    def save(self):
        os.makedirs(DB_DIR, exist_ok=True)
        tmp = DB_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(
                {
                    "users": self.users,
                    "messages": self.messages,
                    "sessions": self.sessions,
                    "lastIds": {
                        "user": self._last_user_id,
                        "message": self._last_message_id,
                    },
                },
                fh,
                indent=2,
            )
        os.replace(tmp, DB_FILE)

    def _next_id(self, kind):
        if kind == "user":
            self._last_user_id += 1
            return self._last_user_id
        self._last_message_id += 1
        return self._last_message_id

    # ---------------------------------- users -------------------------------
    @staticmethod
    def public_user(user):
        if not user:
            return None
        return {
            "id": user["id"],
            "username": user["username"],
            "displayName": user["displayName"],
        }

    def find_user_by_username(self, username):
        key = str(username or "").strip().lower()
        for u in self.users:
            if u["username"].lower() == key:
                return u
        return None

    def find_user_by_id(self, user_id):
        try:
            uid = int(user_id)
        except (TypeError, ValueError):
            return None
        for u in self.users:
            if u["id"] == uid:
                return u
        return None

    @staticmethod
    def _hash_password(password, salt=None):
        if salt is None:
            salt = secrets.token_hex(16)
        digest = hashlib.scrypt(
            password.encode("utf-8"), salt=salt.encode("utf-8"),
            n=2 ** 14, r=8, p=1,
        )
        return f"{salt}${digest.hex()}"

    @staticmethod
    def _verify_password(password, stored):
        try:
            salt, _ = stored.split("$", 1)
        except ValueError:
            return False
        return secrets.compare_digest(Store._hash_password(password, salt), stored)

    def create_user(self, username, display_name, password):
        username = str(username or "").strip()
        if not _USERNAME_RE.match(username):
            raise ValueError(
                "Username must be 3-20 chars: letters, numbers, underscore."
            )
        if self.find_user_by_username(username):
            raise ValueError("That username is already taken.")
        if not password or len(password) < 6:
            raise ValueError("Password must be at least 6 characters.")

        user = {
            "id": self._next_id("user"),
            "username": username,
            "displayName": (str(display_name or "").strip() or username)[:40],
            "passwordHash": self._hash_password(password),
            "createdAt": time.time(),
        }
        self.users.append(user)
        self.save()
        return user

    def verify_user(self, username, password):
        user = self.find_user_by_username(username)
        if not user:
            return None
        return user if self._verify_password(password, user["passwordHash"]) else None

    # -------------------------------- sessions ------------------------------
    def create_session(self, user_id):
        token = secrets.token_urlsafe(32)
        self.sessions[token] = {"userId": user_id, "createdAt": time.time()}
        self.save()
        return token

    def get_session_user(self, token):
        record = self.sessions.get(token)
        if not record:
            return None
        return self.find_user_by_id(record["userId"])

    def destroy_session(self, token):
        if token in self.sessions:
            del self.sessions[token]
            self.save()

    # -------------------------------- messages ------------------------------
    @staticmethod
    def conversation_id(a, b):
        return "-".join(str(x) for x in sorted((int(a), int(b))))

    def save_message(self, sender_id, recipient_id, text):
        clean = str(text or "").strip()
        if not clean:
            raise ValueError("Message cannot be empty.")
        if len(clean) > 2000:
            raise ValueError("Message is too long.")

        message = {
            "id": self._next_id("message"),
            "conversationId": self.conversation_id(sender_id, recipient_id),
            "from": int(sender_id),
            "to": int(recipient_id),
            "text": clean,
            "at": int(time.time() * 1000),
            "read": False,
        }
        self.messages.append(message)
        self.save()
        return message

    def get_conversation(self, a, b, limit=200):
        cid = self.conversation_id(a, b)
        msgs = [m for m in self.messages if m["conversationId"] == cid]
        return msgs[-limit:]

    def get_conversations_for(self, user_id):
        me = int(user_id)
        result = []
        for other in self.users:
            if other["id"] == me:
                continue
            cid = self.conversation_id(me, other["id"])
            msgs = [m for m in self.messages if m["conversationId"] == cid]
            last = msgs[-1] if msgs else None
            unread = sum(1 for m in msgs if m["to"] == me and not m.get("read"))
            result.append(
                {
                    "user": self.public_user(other),
                    "conversationId": cid,
                    "lastMessage": (
                        {"text": last["text"], "at": last["at"], "from": last["from"]}
                        if last
                        else None
                    ),
                    "unread": unread,
                }
            )
        result.sort(
            key=lambda c: c["lastMessage"]["at"] if c["lastMessage"] else 0,
            reverse=True,
        )
        return result

    def mark_conversation_read(self, reader_id, other_id):
        me = int(reader_id)
        cid = self.conversation_id(me, other_id)
        changed = False
        for m in self.messages:
            if m["conversationId"] == cid and m["to"] == me and not m.get("read"):
                m["read"] = True
                changed = True
        if changed:
            self.save()
