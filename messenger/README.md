# Messenger

A small real-time messenger website (like a mini-Messenger / WhatsApp Web).

- **Backend:** Python 3 — **standard library only, no pip installs needed**
- **Frontend:** plain HTML / CSS / JavaScript (no build step)
- **Real-time:** WebSockets implemented from scratch (RFC 6455) in `websocket.py`
- **Storage:** a JSON file (`data/db.json`) — swap for a real DB in production

## Features

- Sign up / log in / log out (passwords hashed with `hashlib.scrypt`)
- Live conversation list with unread badges
- Real-time message delivery over WebSockets
- Online / offline presence indicators
- Message history with day separators and timestamps
- User search

## Getting started

```bash
cd messenger
python server.py
```

Then open <http://localhost:3000>.

> On this Windows machine the interpreter lives at
> `C:\Users\premc\AppData\Local\Programs\Python\Python315\python.exe`.
> If `python` isn't on your PATH, call it with the full path.

To test the chat:
1. Open <http://localhost:3000> in a **normal** window and sign up as user A.
2. Open <http://localhost:3000> in an **incognito / private** window and sign
   up as user B.
3. Select each other in the sidebar and send messages — they arrive instantly.

## Project structure

```
messenger/
├── server.py          # HTTP server, REST API, WebSocket endpoint
├── store.py           # JSON-file data layer (users, sessions, messages)
├── websocket.py       # minimal RFC 6455 WebSocket implementation
├── data/db.json       # created automatically on first run
├── README.md
└── public/
    ├── index.html     # login + chat markup
    ├── style.css      # dark messenger UI
    └── app.js         # frontend logic and WebSocket handling
```

## HTTP API

| Method | Path                    | Purpose                        |
|--------|-------------------------|--------------------------------|
| POST   | `/api/register`         | Create account, start session  |
| POST   | `/api/login`            | Log in                         |
| POST   | `/api/logout`           | End session                    |
| GET    | `/api/me`               | Current user (for auto-login)  |
| GET    | `/api/conversations`    | Sidebar list + unread counts   |
| GET    | `/api/messages/:userId` | History with a user            |

Sessions use an `HttpOnly` cookie (`sid`).

## WebSocket protocol (`/ws`)

The client sends JSON; the server replies with JSON.

**Client → server**

```json
{ "type": "send_message", "to": 2, "text": "hey!" }
{ "type": "mark_read", "userId": 2 }
```

**Server → client**

```json
{ "type": "online", "userIds": [1, 2] }
{ "type": "message", "message": { "id": 5, "from": 1, "to": 2, "text": "hey!", "at": 1699999, "read": false } }
{ "type": "read", "by": 2, "conversationWith": 2 }
{ "type": "error", "error": "Message cannot be empty." }
```

## Tests
Two suites are included:

```bash
python verify.py     # 20 backend checks: auth, REST, WebSocket, unread, logout
```

`verify.py` talks to the real server over HTTP and a raw WebSocket, so run the
server first. It covers registration, duplicate/bad-password rejection, session
handling, live delivery in both directions, history persistence, empty-message
rejection, unread counts, and logout.

`qa_real_users.mjs` drives the actual UI in a headless browser as **two real
users typing to each other** (no scripted/bot conversation): sign up, pick a
contact, type, send, receive, presence, and the connection warning.

```bash
node <browser-automation-skill>/browser.mjs http://localhost:3000 --script ./qa_real_users.mjs
```

`qa.mjs` is a lighter browser pass (sign up, chat, presence, reload).

## Notes / next steps

- **Security:** terminate with HTTPS, add rate limiting, and set a strict
  Content-Security-Policy. `scrypt` is used so passwords are never stored in
  plain text.
- **Scaling:** the session/online maps are in-process. For multiple instances,
  move state to Redis and use a shared message bus.
- **Extras you could add:** group chats, image/file attachments, typing
  indicators, message deletion, read-receipt UI, push notifications.
