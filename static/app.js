// Riddle-da-Fiddle test page.
// The server sends "room_state" whenever anything changes; render() redraws the page from it.

const socket = io();
let state = null;          // latest room_state from the server
let stateAt = 0;           // when it arrived (for countdowns)
let pickedType = "lower";  // guess controls
let pickedQty = 1;
let lastTurnKey = "";      // resets the guess controls each new turn
let deal = null;           // dealing animation in progress: { round, launched: {id: n}, shown: {id: n}, timers }
let dealtRound = null;     // last round we've shown (so a refresh mid-round doesn't replay the deal)

const $ = (id) => document.getElementById(id);
const TYPES = ["lower", "middle", "face"];
const TYPE_RANK = { lower: 0, middle: 1, face: 2 };

// ------------------------------------------------------------ saved seat (for reconnecting)

const saved = {
  get(key) { try { return JSON.parse(localStorage.getItem(key)); } catch { return null; } },
  set(key, value) { try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* private mode */ } },
  clear(key) { try { localStorage.removeItem(key); } catch { /* ignore */ } },
};

// ------------------------------------------------------------ helpers

function esc(text) {
  return String(text ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function toast(message) {
  const el = $("toast");
  el.textContent = message;
  el.classList.remove("hidden");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => el.classList.add("hidden"), 3500);
}

function seatOf(id) { return state?.seats.find((s) => s.id === id); }
function nameOf(id) { return seatOf(id)?.name ?? "?"; }
function cap(text) { return text.charAt(0).toUpperCase() + text.slice(1); }

function cardSrc(card) {
  if (card.suit === "joker") return `/static/cards/${card.rank === 1 ? "black" : "red"}_joker.png`;
  const rank = { 1: "ace", 11: "jack", 12: "queen", 13: "king" }[card.rank] ?? card.rank;
  return `/static/cards/${rank}_of_${card.suit}.png`;
}

function cardLabel(card) {
  if (card.suit === "joker") return "Joker";
  const rank = { 1: "Ace", 11: "Jack", 12: "Queen", 13: "King" }[card.rank] ?? card.rank;
  return `${rank} of ${card.suit}`;
}

function cardHtml(card, small = false, extraClass = "") {
  return `<img class="card${small ? " small" : ""}${extraClass ? " " + extraClass : ""}" src="${cardSrc(card)}" alt="${esc(cardLabel(card))}" title="${esc(cardLabel(card))}">`;
}

function cardTypes(card) {
  if (card.suit === "joker") return [];
  if (card.rank === 1) return TYPES;
  if (card.rank <= 6) return ["lower"];
  if (card.rank <= 10) return ["middle"];
  return ["face"];
}

function minLegalQty(type, previous) {
  if (!previous) return 1;
  return TYPE_RANK[type] > TYPE_RANK[previous.card_type] ? previous.quantity : previous.quantity + 1;
}

function guessText(g) { return `${g.quantity} × ${cap(g.card_type)}`; }

function send(event, data = {}) {
  socket.emit(event, data, (reply) => {
    if (reply && reply.ok && reply.token) saved.set("rdf-seat", { code: reply.code, token: reply.token });
  });
}

function act(action) { send("action", { action }); }

// ------------------------------------------------------------ socket events

socket.on("connect", () => {
  const seat = saved.get("rdf-seat");
  if (seat) {
    socket.emit("rejoin", seat, (reply) => {
      if (!reply.ok) { saved.clear("rdf-seat"); state = null; render(); }
    });
  }
});

socket.on("room_state", (incoming) => {
  const hadResults = Boolean(state?.results);
  state = incoming;
  stateAt = Date.now();
  for (const event of incoming.events) logEvent(event);
  const g = incoming.game;
  let newDeal = false;
  if (g && !incoming.results && g.round !== dealtRound) {
    // Animate a fresh deal: at the start of the game, or when the results screen closes.
    newDeal = g.phase === "guessing" && (hadResults || incoming.events.some((e) => e.type === "round_started"));
    dealtRound = g.round;
  }
  render();
  if (newDeal) startDeal();
});

socket.on("error_msg", (msg) => toast(msg.message));

socket.on("chat", (msg) => {
  const li = document.createElement("li");
  li.className = "chat";
  const who = document.createElement("b");
  who.textContent = `${msg.name}: `;
  li.append(who, document.createTextNode(msg.text));
  addLogLine(li);
  if (msg.player !== state?.you) chatBubble(msg);
});

// Pop-up for new chat, unless the log is already on screen (and not covered by the results).
function chatBubble(msg) {
  const rect = $("chat-form").getBoundingClientRect();   // "visible" = the chat box itself is on screen
  const logVisible = rect.top >= 0 && rect.bottom <= innerHeight && !state?.results;
  if (logVisible) return;
  const el = $("chat-bubble");
  el.textContent = `💬 ${msg.name}: ${msg.text}`;
  el.classList.remove("hidden");
  clearTimeout(chatBubble.timer);
  chatBubble.timer = setTimeout(() => el.classList.add("hidden"), 4000);
}

socket.on("removed", (msg) => {
  cancelDeal();
  saved.clear("rdf-seat");
  state = null;
  $("log").innerHTML = "";
  render();
  toast(msg.reason);
});

socket.on("disconnect", () => toast("Connection lost, reconnecting…"));

// ------------------------------------------------------------ log

function describe(e) {
  switch (e.type) {
    case "round_started": return `Round ${e.round}: ${nameOf(e.starting_player)} starts.`;
    case "guess_made": return `${nameOf(e.player)} guesses ${guessText(e)}.`;
    case "fiddle_called": return `${nameOf(e.player)} FIDDLES ${nameOf(e.guesser)}'s ${guessText(e.guess)}!`;
    case "choose_jokers": return `Jokers in play: waiting for ${e.waiting_for.map(nameOf).join(", ")}.`;
    case "jokers_chosen": return `${nameOf(e.player)} has chosen their jokers.`;
    case "round_result":
      if (e.kind === "fiddle") return `There were ${e.count} ${e.guess.card_type}. ${nameOf(e.winner)} wins the round; ${nameOf(e.loser)} loses a card.`;
      return e.correct
        ? `There were exactly ${e.count}! ${nameOf(e.caller)} riddled correctly` +
          (e.at_cap ? " but is already at the maximum of 8 cards." : " and gains a card.")
        : `There were ${e.count}, not ${e.guess.quantity}. ${nameOf(e.caller)} riddled wrong and loses 2 cards.`;
    case "player_eliminated": return `${nameOf(e.player)} is out!`;
    case "game_won": return e.early ? `${e.reason}: ${nameOf(e.player)} wins with the most cards.` : `🎻 ${nameOf(e.player)} wins the game!`;
    case "bot_took_over": return `A bot is playing for ${nameOf(e.player)} until they're back.`;
    case "player_returned": return `${nameOf(e.player)} is back!`;
    case "player_left": return `${e.name} left. A bot has their seat.`;
    case "player_kicked": return `${e.name} was removed by the host.`;
    case "back_to_lobby": return "Back to the lobby.";
    default: return null;
  }
}

function logEvent(event) {
  const text = describe(event);
  if (!text) return;
  const li = document.createElement("li");
  li.textContent = text;
  addLogLine(li);
}

function addLogLine(li) {
  const log = $("log");
  log.appendChild(li);
  while (log.children.length > 200) log.removeChild(log.firstChild);
  log.scrollTop = log.scrollHeight;
}

// ------------------------------------------------------------ render

function show(screen) {
  for (const name of ["home", "lobby", "game"]) $(`screen-${name}`).classList.toggle("hidden", name !== screen);
  $("leave-btn").classList.toggle("hidden", screen === "home");
  $("chat-panel").classList.toggle("hidden", screen === "home");
  $("chat-panel").classList.toggle("narrow", screen === "lobby");
}

function render() {
  if (!state?.game) cancelDeal();
  if (!state) return show("home");
  if (!state.game) { show("lobby"); return renderLobby(); }
  show("game");
  renderGame();
}

function badges(seat) {
  let html = "";
  if (seat.id === state.host) html += `<span class="badge host">Host</span>`;
  if (seat.id === state.you) html += `<span class="badge">You</span>`;
  if (seat.kind === "bot") html += `<span class="badge ${seat.level === "hacker" ? "hacker" : "bot"}">${esc(seat.level)} bot</span>`;
  if (seat.taken_over) html += `<span class="badge bot">bot playing</span>`;
  if (seat.kind === "human" && !seat.connected && !seat.taken_over) {
    const deadline = stateAt + (seat.seconds_until_bot ?? 0) * 1000;
    html += `<span class="badge warn">offline <span data-deadline="${deadline}"></span></span>`;
  }
  return html;
}

function renderLobby() {
  $("lobby-code").textContent = state.code;
  $("lobby-count").textContent = `(${state.seats.length}/${state.max_players})`;
  $("lobby-seats").innerHTML = state.seats.map((seat) => `
    <li>
      <span>${esc(seat.name)} ${badges(seat)}</span>
      ${state.is_host && seat.id !== state.you ? `<button class="btn ghost small" data-remove="${seat.id}">Remove</button>` : ""}
    </li>`).join("");
  $("host-controls").classList.toggle("hidden", !state.is_host);
  $("lobby-wait").classList.toggle("hidden", state.is_host);
  $("add-bot-btn").disabled = state.seats.length >= state.max_players;
  $("start-btn").disabled = state.seats.length < state.min_players;
  $("start-btn").textContent = state.seats.length < state.min_players ? "Need at least 2 players" : "Start game";
  tickCountdowns();
}

function renderGame() {
  const game = state.game;
  const byId = Object.fromEntries(game.players.map((p) => [p.id, p]));

  // players
  $("players").innerHTML = state.seats.map((seat) => {
    const p = byId[seat.id];
    if (!p) return "";
    const isCurrent = game.current_player === seat.id;
    const waitingJokers = game.waiting_for_jokers.includes(seat.id);
    const hostButtons = state.is_host && seat.id !== state.you && seat.kind === "human" && !p.eliminated && game.phase !== "game_over"
      ? (!seat.connected && !seat.taken_over ? `<button class="btn small" data-tobot="${seat.id}">Bot now</button> ` : "")
        + `<button class="btn ghost small" data-remove="${seat.id}">Kick</button>`
      : "";
    return `
      <div class="player${isCurrent ? " current" : ""}${p.eliminated ? " out" : ""}">
        <div class="name">${esc(seat.name)}</div>
        <div>${badges(seat)}</div>
        <div class="meta">${p.eliminated ? "Out" : `${p.card_count} card${p.card_count === 1 ? "" : "s"}`}${isCurrent ? " · their turn" : ""}${waitingJokers ? " · choosing jokers" : ""}</div>
        ${p.eliminated ? "" : `<div class="mini-backs" data-pid="${esc(seat.id)}">${Array.from({ length: Math.min(p.card_count, 20) },
          (_, i) => `<span class="mini-back${deal && i >= (deal.shown[seat.id] ?? 0) ? " pending" : ""}"></span>`).join("")}</div>`}
        ${hostButtons}
      </div>`;
  }).join("");

  // table centre
  $("round-label").textContent = `Round ${game.round} · ${game.cards_in_play} cards in play`;
  const g = game.current_guess;
  $("current-guess").textContent = g ? guessText(g) : "No guess yet";
  let status = "";
  if (game.phase === "guessing") {
    status = game.your_turn ? "Your turn!" : `Waiting for ${nameOf(game.current_player)}…`;
    if (g) status = `${nameOf(g.player_id)} guessed. ` + status;
    if (deal) status = "Dealing…";
  } else if (game.phase === "choosing_jokers") {
    status = `Waiting for ${game.waiting_for_jokers.map(nameOf).join(", ")} to choose jokers…`;
  }
  $("status-line").textContent = status;

  // guess controls
  const myTurn = game.phase === "guessing" && game.your_turn && !deal;
  const needsMe = myTurn || (game.phase === "choosing_jokers" && game.waiting_for_jokers.includes(state.you));
  if (needsMe && !renderGame.neededMe && rulesOpen()) toast("It's your turn! Close the rules to play.");
  renderGame.neededMe = needsMe;
  $("turn-controls").classList.toggle("hidden", !myTurn);
  if (myTurn) {
    const turnKey = `${game.round}-${g ? guessText(g) + g.player_id : "first"}`;
    if (turnKey !== lastTurnKey) {
      lastTurnKey = turnKey;
      pickedType = g ? g.card_type : "lower";
      pickedQty = minLegalQty(pickedType, g);
    }
    renderGuessControls();
    $("fiddle-btn").disabled = !game.can_fiddle_or_riddle;
    $("riddle-btn").disabled = !game.can_fiddle_or_riddle;
  }

  // joker chooser
  const chooseJokers = game.phase === "choosing_jokers" && game.waiting_for_jokers.includes(state.you);
  $("joker-chooser").classList.toggle("hidden", !chooseJokers);
  if (chooseJokers) {
    const buttons = Array.from({ length: game.your_jokers + 1 }, (_, n) => `<button class="btn" data-jokers="${n}">${n}</button>`).join("");
    $("joker-chooser").innerHTML = `<div>You have ${game.your_jokers} joker${game.your_jokers === 1 ? "" : "s"}. How many count towards ${esc(g.card_type)}?</div><div class="row">${buttons}</div>`;
  }

  // game over
  const over = game.phase === "game_over";
  $("game-over").classList.toggle("hidden", !over);
  if (over) {
    $("game-over").innerHTML = `🎻 ${esc(nameOf(game.winner))} wins!` +
      (state.is_host ? `<div><button class="btn primary" id="lobby-btn">Back to lobby</button></div>` : `<div class="muted">Waiting for the host…</div>`);
  }
  $("host-game-controls").classList.toggle("hidden", !state.is_host || over);

  // hand
  const hand = game.your_hand;
  $("hand").innerHTML = hand.length
    ? hand.map((c, i) => cardHtml(c, false, deal && i >= (deal.shown[state.you] ?? 0) ? "undealt" : "")).join("")
    : `<span class="muted">No cards</span>`;
  const counts = { lower: 0, middle: 0, face: 0 };
  for (const card of hand) for (const t of cardTypes(card)) counts[t]++;
  $("hand-summary").textContent = hand.length
    ? `· lower ${counts.lower}, middle ${counts.middle}, face ${counts.face}${game.your_jokers ? `, jokers ${game.your_jokers}` : ""}`
    : "";

  renderLastRound(game.last_round);
  renderResults();
  tickCountdowns();
}

function renderResults() {
  const r = state.game?.last_round;
  const res = state.results;
  $("results").classList.toggle("hidden", !(res && r));
  if (!(res && r)) return;
  const game = state.game;
  const type = r.guess.card_type;

  const verb = r.kind === "fiddle" ? "fiddled" : "riddled";
  const headline = `${esc(nameOf(r.caller))} ${verb} ${esc(nameOf(r.guesser))}'s guess of ${guessText(r.guess)}`;
  let verdict;
  if (r.kind === "fiddle") {
    const wasTrue = r.count >= r.guess.quantity;
    verdict = `There were <b>${r.count}</b> ${esc(type)}: the guess was <b>${wasTrue ? "true" : "false"}</b>. ${esc(nameOf(r.loser))} loses a card.`;
  } else if (r.correct) {
    verdict = `Exactly <b>${r.count}</b> ${esc(type)}: the riddle was <b>right</b>! ` +
      (r.at_cap ? `${esc(nameOf(r.caller))} is already at the 8-card maximum.` : `${esc(nameOf(r.caller))} gains a card.`);
  } else {
    verdict = `There were <b>${r.count}</b> ${esc(type)}, not ${r.guess.quantity}: the riddle was <b>wrong</b>. ${esc(nameOf(r.caller))} loses 2 cards.`;
  }

  const rows = state.seats.filter((seat) => r.hands[seat.id]).map((seat) => {
    const cards = r.hands[seat.id];
    const before = r.card_counts_before[seat.id];
    const after = r.card_counts[seat.id];
    const diff = after - before;
    let change = `<span class="change">${after}</span>`;
    if (after === 0) change = `<span class="change minus">${before} → OUT</span>`;
    else if (diff) change = `<span class="change ${diff > 0 ? "plus" : "minus"}">${before} → ${after} (${diff > 0 ? "+" : "−"}${Math.abs(diff)})</span>`;
    let jokerNote = "";
    if (cards.some((c) => c.suit === "joker")) {
      const used = r.jokers_counted?.[seat.id] ?? 0;
      jokerNote = r.kind === "fiddle"
        ? `<span class="muted"> · counted ${used} joker${used === 1 ? "" : "s"}</span>`
        : `<span class="muted"> · jokers are wild on a riddle</span>`;
    }
    const cardsHtml = cards.map((c) => cardHtml(c, true,
      c.suit === "joker" ? "joker" : cardTypes(c).includes(type) ? "match" : "dim")).join("");
    return `<div class="result-row">
      <div class="result-who"><b>${esc(seat.name)}</b> ${change}${jokerNote}</div>
      <div class="cards">${cardsHtml}</div>
    </div>`;
  }).join("");

  const next = game.phase === "game_over"
    ? "That's the game!"
    : `${esc(nameOf(game.current_player))} starts round ${game.round}.`;
  const humans = state.seats.filter((s) => s.kind === "human" && s.connected);
  const readyCount = humans.filter((s) => res.ready.includes(s.id)).length;
  const iAmReady = res.ready.includes(state.you);
  const deadline = stateAt + res.seconds_left * 1000;

  $("results-box").innerHTML = `
    <div class="muted">Round ${r.round} results</div>
    <h2 id="results-title" class="results-headline">${headline}</h2>
    <p class="results-verdict">${verdict}</p>
    <p class="muted results-key"><span class="key match"></span> counts as ${esc(type)}
      <span class="key joker"></span> joker</p>
    <div class="result-rows">${rows}</div>
    <p class="results-next">${next}</p>
    <div class="results-bar"><div class="results-bar-fill" data-bar-deadline="${deadline}" data-bar-total="${res.total}"></div></div>
    <div class="row results-actions">
      <button class="btn primary" id="ready-btn" ${iAmReady ? "disabled" : ""}>${iAmReady ? "Waiting for others…" : "Ready"}</button>
      <span class="muted">${readyCount}/${humans.length} ready · <span data-deadline="${deadline}"></span></span>
    </div>`;
}

function renderGuessControls() {
  const g = state.game.current_guess;
  for (const btn of document.querySelectorAll(".type-btn")) btn.classList.toggle("selected", btn.dataset.type === pickedType);
  const top = state.game.cards_in_play;   // can't guess more cards than there are
  pickedQty = Math.min(Math.max(pickedQty, minLegalQty(pickedType, g)), top);
  $("qty-value").textContent = pickedQty;
  $("qty-down").disabled = pickedQty <= minLegalQty(pickedType, g);
  $("qty-up").disabled = pickedQty >= top;
  $("guess-btn").disabled = minLegalQty(pickedType, g) > top;
}

function renderLastRound(r) {
  const el = $("last-round");
  el.classList.toggle("hidden", !r);
  if (!r) return;
  const outcome = r.kind === "fiddle"
    ? `${esc(nameOf(r.caller))} fiddled ${esc(nameOf(r.guesser))}'s ${guessText(r.guess)}. There were <b>${r.count}</b>: ${esc(nameOf(r.loser))} lost a card.`
    : `${esc(nameOf(r.caller))} riddled ${guessText(r.guess)}. There were <b>${r.count}</b>: ${r.correct ? (r.at_cap ? "correct (already at 8 cards)" : "correct, +1 card") : "wrong, −2 cards"}.`;
  const rows = Object.entries(r.hands).map(([id, cards]) => `
    <div class="reveal-row"><span class="who">${esc(nameOf(id))}</span><span class="cards">${cards.map((c) => cardHtml(c, true)).join("")}</span></div>`).join("");
  el.innerHTML = `<h2>Last reveal (round ${r.round})</h2><p>${outcome}</p>${rows}`;
}

// ------------------------------------------------------------ dealing animation

// The order the server dealt in: one card at a time round the table, skipping
// full hands (same as the engine's _start_round), so the animation matches.
function dealOrder(game) {
  const players = game.players.filter((p) => !p.eliminated).map((p) => ({ id: p.id, want: p.card_count, got: 0 }));
  let deck = 54;
  const order = [];
  let dealing = players;
  while (deck > 0 && dealing.length) {
    for (const p of dealing) {
      if (deck > 0) { p.got++; deck--; order.push(p.id); }
    }
    dealing = dealing.filter((p) => p.got < p.want);
  }
  return order;
}

function dealTarget(pid, index) {
  if (pid === state.you) return $("hand").children[index];
  return document.querySelector(`.mini-backs[data-pid="${CSS.escape(pid)}"]`)?.children[index];
}

function startDeal() {
  cancelDeal();
  if (matchMedia("(prefers-reduced-motion: reduce)").matches) return;
  const order = dealOrder(state.game);
  if (!order.length) return;
  deal = { round: state.game.round, launched: {}, shown: {}, timers: [] };
  renderGame();
  $("deck").classList.remove("hidden");
  const step = state.deal_ms_per_card;
  order.forEach((pid, i) => deal.timers.push(setTimeout(() => flyCard(pid), i * step)));
  deal.timers.push(setTimeout(finishDeal, (order.length - 1) * step + state.deal_fly_ms + 80));
}

function flyCard(pid) {
  if (!deal) return;
  const index = deal.launched[pid] ?? 0;
  deal.launched[pid] = index + 1;
  const land = () => {
    if (!deal) return;
    deal.shown[pid] = index + 1;
    const el = dealTarget(pid, index);
    if (el) { el.classList.remove("undealt", "pending"); el.classList.add("dealt-in"); }
  };
  const target = dealTarget(pid, index);
  if (!target) return land();
  const from = $("deck").getBoundingClientRect();
  const to = target.getBoundingClientRect();
  const fly = document.createElement("div");
  fly.className = "fly-card";
  Object.assign(fly.style, { left: `${from.left}px`, top: `${from.top}px`, width: `${from.width}px`, height: `${from.height}px` });
  document.body.appendChild(fly);
  const animation = fly.animate([
    { transform: "translate(0, 0) scale(1)" },
    { transform: `translate(${to.left - from.left}px, ${to.top - from.top}px) scale(${to.width / from.width}, ${to.height / from.height})` },
  ], { duration: state.deal_fly_ms, easing: "ease-out" });
  animation.onfinish = () => { fly.remove(); land(); };
  deal.timers.push(setTimeout(() => fly.remove(), state.deal_fly_ms + 500));   // in case the tab was hidden
}

function finishDeal() {
  cancelDeal();
  if (state?.game) renderGame();
}

function cancelDeal() {
  if (!deal) return;
  for (const t of deal.timers) clearTimeout(t);
  deal = null;
  $("deck").classList.add("hidden");
  for (const el of document.querySelectorAll(".fly-card")) el.remove();
}

function tickCountdowns() {
  for (const el of document.querySelectorAll("[data-deadline]")) {
    const left = Math.max(0, Math.ceil((Number(el.dataset.deadline) - Date.now()) / 1000));
    el.textContent = `${left}s`;
  }
  for (const el of document.querySelectorAll("[data-bar-deadline]")) {
    const left = Math.max(0, Number(el.dataset.barDeadline) - Date.now()) / 1000;
    el.style.width = `${(100 * left) / Number(el.dataset.barTotal)}%`;
  }
}
setInterval(tickCountdowns, 500);

// ------------------------------------------------------------ inputs

function myName() {
  const name = $("name-input").value.trim();
  if (!name) { toast("Enter your name first"); $("name-input").focus(); return null; }
  saved.set("rdf-name", name);
  return name;
}

$("create-btn").onclick = () => { const name = myName(); if (name) send("create_room", { name }); };
$("join-btn").onclick = () => {
  const name = myName();
  const code = $("code-input").value.trim().toUpperCase();
  if (!name) return;
  if (code.length !== 4) return toast("Enter the 4-letter code");
  send("join_room", { name, code });
};
$("code-input").addEventListener("keydown", (e) => { if (e.key === "Enter") $("join-btn").click(); });
$("name-input").addEventListener("keydown", (e) => { if (e.key === "Enter") ($("code-input").value ? $("join-btn") : $("create-btn")).click(); });

$("leave-btn").onclick = () => {
  if (state?.game && state.game.phase !== "game_over" && !confirm("Leave the game? A bot will take your seat.")) return;
  send("leave");
  saved.clear("rdf-seat");
  state = null;
  $("log").innerHTML = "";
  render();
};

$("copy-link-btn").onclick = async () => {
  const link = `${location.origin}/?code=${state.code}`;
  try { await navigator.clipboard.writeText(link); toast("Invite link copied"); } catch { toast(link); }
};

// background music (starts on the first click: browsers block sound until you interact)
const music = $("music");
music.volume = 0.3;
const MUSIC_END = 206.5;   // the recording ends with ~3s of silence: skip it so the loop flows
music.addEventListener("timeupdate", () => { if (music.currentTime >= MUSIC_END) music.currentTime = 0.4; });
let musicOn = saved.get("rdf-music") !== false;
function updateMusicButton() {
  const label = musicOn ? "Mute music" : "Play music";
  $("music-btn").textContent = musicOn ? "🔊" : "🔇";
  $("music-btn").setAttribute("aria-label", label);
  $("music-btn").title = label;
}
function startMusic() {
  if (musicOn && music.paused) music.play().catch(() => { /* not allowed yet: try again on the next click */ });
}
$("music-btn").onclick = () => {
  musicOn = !musicOn;
  saved.set("rdf-music", musicOn);
  if (musicOn) startMusic(); else music.pause();
  updateMusicButton();
};
document.addEventListener("pointerdown", startMusic);
document.addEventListener("keydown", startMusic);
updateMusicButton();

// rule book
function rulesOpen() { return !$("rules").classList.contains("hidden"); }
function openRules() { $("rules").classList.remove("hidden"); $("rules-close").focus(); }
function closeRules() { $("rules").classList.add("hidden"); $("rules-btn").focus(); }
$("rules-btn").onclick = openRules;
$("rules-close").onclick = closeRules;
$("rules").addEventListener("click", (e) => { if (e.target === $("rules")) closeRules(); });   // tap outside the box
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && rulesOpen()) closeRules(); });

$("chat-form").onsubmit = (e) => {
  e.preventDefault();
  const text = $("chat-input").value.trim();
  if (!text) return;
  send("chat", { text });
  $("chat-input").value = "";
};
$("chat-bubble").onclick = () => {
  $("chat-bubble").classList.add("hidden");
  if (state?.results) return;   // log is behind the results screen
  $("chat-panel").scrollIntoView({ behavior: "smooth", block: "center" });
  $("chat-input").focus({ preventScroll: true });
};

$("add-bot-btn").onclick = () => send("add_bot", { level: $("bot-level").value });
$("start-btn").onclick = () => send("start_game");
$("end-game-btn").onclick = () => { if (confirm("End the game for everyone and go back to the lobby?")) send("back_to_lobby"); };

for (const btn of document.querySelectorAll(".type-btn")) {
  btn.onclick = () => { pickedType = btn.dataset.type; pickedQty = minLegalQty(pickedType, state.game.current_guess); renderGuessControls(); };
}
$("qty-up").onclick = () => { pickedQty++; renderGuessControls(); };
$("qty-down").onclick = () => { pickedQty--; renderGuessControls(); };
$("guess-btn").onclick = () => act({ type: "guess", card_type: pickedType, quantity: pickedQty });
$("fiddle-btn").onclick = () => act({ type: "fiddle" });
$("riddle-btn").onclick = () => act({ type: "riddle" });

// buttons drawn inside render()
document.addEventListener("click", (e) => {
  const t = e.target.closest("button");
  if (!t) return;
  if (t.dataset.remove) {
    const seat = seatOf(t.dataset.remove);
    if (seat.kind === "bot" || confirm(`Remove ${seat.name}?`)) send("remove_seat", { seat_id: t.dataset.remove });
  }
  if (t.dataset.tobot) send("hand_to_bot", { seat_id: t.dataset.tobot });
  if (t.dataset.jokers) act({ type: "use_jokers", amount: Number(t.dataset.jokers) });
  if (t.id === "lobby-btn") send("back_to_lobby");
  if (t.id === "ready-btn") send("ready");
});

// start-up: remember name, prefill code from an invite link
$("name-input").value = saved.get("rdf-name") ?? "";
const inviteCode = new URLSearchParams(location.search).get("code");
if (inviteCode) $("code-input").value = inviteCode.toUpperCase().slice(0, 4);
render();
