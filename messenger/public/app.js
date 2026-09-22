/* ------------------------------- state -------------------------------- */
const state = {
  me: null,
  peer: null,          // currently open conversation's user
  conversations: [],   // [{ user, conversationId, lastMessage, unread }]
  online: new Set(),
  search: "",
  socket: null,
  reconnectDelay: 1000,
};

/* ------------------------------ helpers ------------------------------- */
const $ = (id) => document.getElementById(id);

async function api(url, options = {}) {
  const res = await fetch(url, {
    headers: { "Content-Type": "application/json" },
    credentials: "same-origin",
    ...options,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `Request failed (${res.status})`);
  return data;
}

const initials = (name) =>
  String(name || "?")
    .trim()
    .split(/\s+/)
    .slice(0, 2)
    .map((p) => p[0])
    .join("")
    .toUpperCase();

function formatTime(ts) {
  return new Date(ts).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
  });
}

function formatDay(ts) {
  const d = new Date(ts);
  const today = new Date();
  const yest = new Date();
  yest.setDate(today.getDate() - 1);
  const same = (a, b) => a.toDateString() === b.toDateString();
  if (same(d, today)) return "Today";
  if (same(d, yest)) return "Yesterday";
  return d.toLocaleDateString([], { day: "numeric", month: "short", year: "numeric" });
}

/* --------------------------- auth screen ------------------------------ */
let mode = "login";

function setMode(next) {
  mode = next;
  const isLogin = mode === "login";
  $("tabLogin").classList.toggle("active", isLogin);
  $("tabRegister").classList.toggle("active", !isLogin);
  $("displayNameField").style.display = isLogin ? "none" : "block";
  $("authSubmit").textContent = isLogin ? "Log in" : "Create account";
  $("authError").textContent = "";
}

$("tabLogin").addEventListener("click", () => setMode("login"));
$("tabRegister").addEventListener("click", () => setMode("register"));

$("authForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const username = $("username").value.trim();
  const password = $("password").value;
  const displayName = $("displayName").value.trim();

  try {
    const body =
      mode === "login"
        ? { username, password }
        : { username, password, displayName };
    const { user } = await api(`/api/${mode}`, {
      method: "POST",
      body: JSON.stringify(body),
    });
    await enterApp(user);
  } catch (err) {
    $("authError").textContent = err.message;
  }
});

$("logoutBtn").addEventListener("click", async () => {
  await api("/api/logout", { method: "POST" });
  location.reload();
});

/* --------------------------- app bootstrap ---------------------------- */
async function enterApp(user) {
  state.me = user;
  $("authScreen").style.display = "none";
  $("app").style.display = "grid";
  $("meName").textContent = user.displayName;
  $("meAvatar").textContent = initials(user.displayName);

  await loadConversations();
  connectSocket();
}

async function loadConversations() {
  const { conversations } = await api("/api/conversations");
  state.conversations = conversations;
  renderConversations();
}

/* --------------------------- conversations UI ------------------------- */
function renderConversations() {
  const list = $("conversationList");
  list.innerHTML = "";

  const q = state.search.toLowerCase();
  const items = state.conversations.filter(
    (c) =>
      !q ||
      c.user.displayName.toLowerCase().includes(q) ||
      c.user.username.toLowerCase().includes(q)
  );

  if (items.length === 0) {
    const li = document.createElement("li");
    li.className = "muted";
    li.style.padding = "16px";
    li.textContent = q ? "No people match your search." : "No other users yet.";
    list.appendChild(li);
    return;
  }

  items.forEach((c) => {
    const li = document.createElement("li");
    li.className = "conversation";
    if (state.peer && state.peer.id === c.user.id) li.classList.add("active");

    const avatar = document.createElement("div");
    avatar.className = "avatar sm";
    avatar.textContent = initials(c.user.displayName);

    if (state.online.has(c.user.id)) {
      const dot = document.createElement("span");
      dot.className = "dot";
      li.appendChild(dot);
    }

    const body = document.createElement("div");
    body.className = "convo-body";

    const top = document.createElement("div");
    top.className = "convo-top";
    const name = document.createElement("span");
    name.className = "convo-name";
    name.textContent = c.user.displayName;
    const time = document.createElement("span");
    time.className = "convo-time";
    time.textContent = c.lastMessage ? formatTime(c.lastMessage.at) : "";
    top.append(name, time);

    const preview = document.createElement("div");
    preview.className = "convo-preview";
    preview.textContent = c.lastMessage ? c.lastMessage.text : "No messages yet";

    body.append(top, preview);
    li.append(avatar, body);

    if (c.unread > 0) {
      const badge = document.createElement("span");
      badge.className = "badge";
      badge.textContent = c.unread;
      li.appendChild(badge);
    }

    li.addEventListener("click", () => openConversation(c.user));
    list.appendChild(li);
  });
}

$("searchInput").addEventListener("input", (e) => {
  state.search = e.target.value.trim();
  renderConversations();
});

/* ---------------------------- open a chat ----------------------------- */
async function openConversation(user) {
  state.peer = user;
  $("emptyState").style.display = "none";
  $("chatPanel").style.display = "flex";
  $("peerName").textContent = user.displayName;
  $("peerAvatar").textContent = initials(user.displayName);
  updatePeerStatus();
  renderConversations();

  const { messages } = await api(`/api/messages/${user.id}`);
  renderMessages(messages);

  sendRaw({ type: "mark_read", userId: user.id });

  // Clear the unread badge locally.
  const convo = state.conversations.find((c) => c.user.id === user.id);
  if (convo) convo.unread = 0;
  renderConversations();

  $("messageInput").focus();
}

function renderMessages(messages) {
  const box = $("messages");
  box.innerHTML = "";
  let lastDay = "";

  messages.forEach((m) => {
    const day = formatDay(m.at);
    if (day !== lastDay) {
      const sep = document.createElement("div");
      sep.className = "day-sep";
      sep.textContent = day;
      box.appendChild(sep);
      lastDay = day;
    }
    box.appendChild(messageEl(m));
  });

  box.scrollTop = box.scrollHeight;
}

function messageEl(m) {
  const row = document.createElement("div");
  row.className = "msg-row " + (m.from === state.me.id ? "mine" : "theirs");

  const bubble = document.createElement("div");
  bubble.className = "bubble";
  bubble.textContent = m.text;

  const time = document.createElement("span");
  time.className = "msg-time";
  time.textContent = formatTime(m.at);
  bubble.appendChild(time);

  row.appendChild(bubble);
  return row;
}

function appendMessage(m) {
  const box = $("messages");
  // NOTE: ":last-of-type" matches by element type (div), not by class, so it
  // would miss the separator as soon as a bubble follows it. Find the last
  // separator explicitly instead.
  const seps = box.querySelectorAll(".day-sep");
  const last = seps[seps.length - 1];
  const day = formatDay(m.at);
  if (!last || last.textContent !== day) {
    const sep = document.createElement("div");
    sep.className = "day-sep";
    sep.textContent = day;
    box.appendChild(sep);
  }
  box.appendChild(messageEl(m));
  box.scrollTop = box.scrollHeight;
}

/* ----------------------------- composer ------------------------------- */
$("messageForm").addEventListener("submit", (e) => {
  e.preventDefault();
  const input = $("messageInput");
  const text = input.value.trim();
  if (!text || !state.peer) return;

  // Only clear the box once the send was actually accepted. If the socket is
  // down we keep the text, so the user's typing is never lost.
  if (!sendRaw({ type: "send_message", to: state.peer.id, text })) {
    showConnectionBanner();
    input.focus();
    return;
  }
  input.value = "";
  input.focus();
});

/* ------------------------------ sockets ------------------------------- */
function wsUrl() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  return `${proto}://${location.host}/ws`;
}

function connectSocket() {
  const socket = new WebSocket(wsUrl());
  state.socket = socket;

  socket.addEventListener("open", () => {
    state.reconnectDelay = 1000;
    hideConnectionBanner();
    if (state.peer) sendRaw({ type: "mark_read", userId: state.peer.id });
  });

  socket.addEventListener("message", (event) => {
    let data;
    try {
      data = JSON.parse(event.data);
    } catch {
      return;
    }

    if (data.type === "online") {
      state.online = new Set(data.userIds.map(Number));
      state.online.add(state.me.id);
      renderConversations();
      updatePeerStatus();
      return;
    }

    if (data.type === "message") handleIncoming(data.message);
    // Someone registered after we logged in — refresh so we can chat to them.
    if (data.type === "user_joined" && data.userId !== state.me.id) {
      loadConversations();
    }
    if (data.type === "error") console.warn("server error:", data.error);
  });

  socket.addEventListener("close", () => {
    // Tell the user why their next message won't send, then retry with backoff.
    // If the session died the reload below takes them back to the login form.
    state.socket = null;
    showConnectionBanner();
    setTimeout(() => {
      if (state.me) connectSocket();
    }, state.reconnectDelay);
    state.reconnectDelay = Math.min(state.reconnectDelay * 2, 10000);
  });
}

function sendRaw(payload) {
  if (state.socket && state.socket.readyState === WebSocket.OPEN) {
    state.socket.send(JSON.stringify(payload));
    return true;
  }
  return false;
}

function showConnectionBanner() {
  const banner = $("connBanner");
  banner.textContent = "Not connected \u2014 your message wasn't sent. Reconnecting\u2026";
  banner.style.display = "block";
}

function hideConnectionBanner() {
  $("connBanner").style.display = "none";
}

function handleIncoming(m) {
  const isFromPeer = state.peer && m.from === state.peer.id;
  const isToPeer = state.peer && m.to === state.peer.id;

  if (isFromPeer || isToPeer) {
    appendMessage(m);
    if (isFromPeer) sendRaw({ type: "mark_read", userId: state.peer.id });
  }

  // Keep the sidebar in sync.
  const otherId = m.from === state.me.id ? m.to : m.from;
  const convo = state.conversations.find((c) => c.user.id === otherId);
  if (!convo) {
    loadConversations();
    return;
  }

  convo.lastMessage = { text: m.text, at: m.at, from: m.from };
  const openWithPeer = isFromPeer && state.peer && otherId === state.peer.id;
  if (m.to === state.me.id && !openWithPeer) {
    convo.unread = (convo.unread || 0) + 1;
  }
  state.conversations.sort((a, b) => {
    const at = a.lastMessage ? a.lastMessage.at : 0;
    const bt = b.lastMessage ? b.lastMessage.at : 0;
    return bt - at;
  });
  renderConversations();
}

function updatePeerStatus() {
  if (!state.peer) return;
  const isOnline = state.online.has(state.peer.id);
  $("peerStatus").textContent = isOnline ? "online" : "offline";
  $("peerStatus").classList.toggle("online", isOnline);
}

/* ---------------------------- auto login ------------------------------ */
(async function init() {
  try {
    const { user } = await api("/api/me");
    await enterApp(user);
  } catch {
    setMode("login");
  }
})();
