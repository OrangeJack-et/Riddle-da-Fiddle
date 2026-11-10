"""
Riddle-da-Fiddle web server (Flask-SocketIO).

Run locally:   python server.py      then open http://localhost:5000

How it fits together:
    browser  --socket.io-->  server.py  --handle_action()-->  riddle_da_fiddle_engine.py
    browser  <--room_state-- server.py  <--state_for()------  (each player gets only their own view)

Rooms ("lobbies") have a 4-letter code, 2-8 seats, and a host (whoever made it).
Seats are humans or bots. If a human disconnects during a game, a 30 second
countdown starts; after that (or straight away if the host says so) a bot plays
their seat until they come back.
"""

import os
import random
import secrets
import threading
import time

from flask import Flask, render_template, request
from flask_socketio import SocketIO

import bots
from riddle_da_fiddle_engine import (
    Game, IllegalAction, PHASE_GUESSING, PHASE_CHOOSING_JOKERS, PHASE_GAME_OVER,
)

MIN_PLAYERS = 2
MAX_PLAYERS = 8
MAX_NAME_LENGTH = 16
MAX_CHAT_LENGTH = 200
CHAT_BURST = 5                                                      # messages one player can send...
CHAT_WINDOW = 5                                                     # ...in this many seconds
DISCONNECT_GRACE = float(os.environ.get("DISCONNECT_GRACE", 30))   # seconds before a bot takes over
BOT_DELAY = float(os.environ.get("BOT_DELAY", 1.5))                # seconds a bot "thinks"
TAKEOVER_LEVEL = "pro"                                              # bot level that fills in for humans
EMPTY_ROOM_TIMEOUT = 10 * 60                                        # delete rooms nobody is in
CODE_LETTERS = "ABCDEFGHJKLMNPQRSTUVWXYZ"                           # no I/O: too easy to confuse
RESULTS_PAUSE = float(os.environ.get("RESULTS_PAUSE", 10))         # seconds the round results screen stays up
DEAL_MS_PER_CARD = 120                                              # dealing animation: gap between cards
DEAL_FLY_MS = 350                                                   # ...and how long one card takes to fly
MAX_ROOMS = 200                                                     # rooms open at once, site-wide
ROOMS_PER_IP = 5                                                    # rooms one address can create...
ROOMS_PER_IP_WINDOW = 10 * 60                                       # ...in this many seconds

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", secrets.token_hex(16))
socketio = SocketIO(app, async_mode="threading")

LOCK = threading.RLock()   # every change to ROOMS happens while holding this
ROOMS = {}                 # code -> Room
SID_INDEX = {}             # socket id -> (room code, seat id)
ROOMS_CREATED = {}         # ip address -> times it created a room (for the rate limit)


class UserError(Exception):
    """Something the player did wrong; the message is shown to them."""


# ---------------------------------------------------------------- data

class Seat:
    def __init__(self, seat_id, name, kind, level=None):
        self.id = seat_id
        self.name = name
        self.kind = kind                    # "human" or "bot"
        self.level = level                  # bot level, None for humans
        self.token = secrets.token_urlsafe(16) if kind == "human" else None   # secret rejoin key
        self.sid = None                     # current socket id (None = disconnected)
        self.taken_over = False             # human seat currently played by a bot
        self.disconnect_deadline = None     # time.time() when a bot will take over
        self.disconnect_generation = 0      # bumps on every connect/disconnect to cancel old timers
        self.chat_times = []                # when this seat last sent chat (spam limit)

    @property
    def bot_controlled(self):
        return self.kind == "bot" or self.taken_over

    def public(self, now):
        seconds_left = None
        if self.disconnect_deadline is not None:
            seconds_left = max(0, round(self.disconnect_deadline - now))
        return {
            "id": self.id,
            "name": self.name,
            "kind": self.kind,
            "level": self.level,
            "connected": self.kind == "bot" or self.sid is not None,
            "taken_over": self.taken_over,
            "seconds_until_bot": seconds_left,
        }


class Room:
    def __init__(self, code):
        self.code = code
        self.seats = []
        self.host_id = None
        self.game = None
        self.next_seat_number = 1
        self.bot_task_running = False
        self.empty_since = None
        # Round results screen: while set, nobody (human or bot) can move.
        # {"until": time.time() it ends, "ready": {seat ids who clicked Ready}, "generation": n}
        self.results = None
        self.results_generation = 0
        self.deal_until = 0                 # time.time() the dealing animation ends (bots wait for it)

    def seat(self, seat_id):
        for seat in self.seats:
            if seat.id == seat_id:
                return seat
        return None

    def humans(self):
        return [s for s in self.seats if s.kind == "human"]

    def connected_humans(self):
        return [s for s in self.seats if s.kind == "human" and s.sid is not None]

    def add_seat(self, name, kind, level=None):
        seat = Seat("s" + str(self.next_seat_number), self.unique_name(name), kind, level)
        self.next_seat_number += 1
        self.seats.append(seat)
        return seat

    def unique_name(self, name):
        taken = {s.name.lower() for s in self.seats}
        candidate, number = name, 2
        while candidate.lower() in taken:
            candidate = name + " " + str(number)
            number += 1
        return candidate

    def game_running(self):
        return self.game is not None and self.game.phase != PHASE_GAME_OVER


# ---------------------------------------------------------------- helpers

def clean_name(name):
    name = " ".join(str(name or "").split())[:MAX_NAME_LENGTH]
    if not name:
        raise UserError("Please enter a name")
    return name


def new_code():
    while True:
        code = "".join(random.choice(CODE_LETTERS) for _ in range(4))
        if code not in ROOMS:
            return code


def client_ip():
    """The player's address. On Render the real one is the first entry of
    X-Forwarded-For (it can be faked, which is why there's a site-wide cap too)."""
    forwarded = request.headers.get("X-Forwarded-For", "")
    return forwarded.split(",")[0].strip() or request.remote_addr or "?"


def check_room_limits():
    """Stop one person (or a script) creating thousands of rooms."""
    now = time.time()
    for ip in list(ROOMS_CREATED):
        ROOMS_CREATED[ip] = [t for t in ROOMS_CREATED[ip] if now - t < ROOMS_PER_IP_WINDOW]
        if not ROOMS_CREATED[ip]:
            del ROOMS_CREATED[ip]
    ip = client_ip()
    if len(ROOMS) >= MAX_ROOMS or len(ROOMS_CREATED.get(ip, [])) >= ROOMS_PER_IP:
        raise UserError("Too many games have been made recently, try again in a few minutes.")
    ROOMS_CREATED.setdefault(ip, []).append(now)


def attach(room, seat, sid):
    old = seat.sid
    if old and old != sid:
        SID_INDEX.pop(old, None)
        socketio.emit("removed", {"reason": "You opened the game in another tab."}, to=old)
    seat.sid = sid
    seat.disconnect_deadline = None
    seat.disconnect_generation += 1
    SID_INDEX[sid] = (room.code, seat.id)
    room.empty_since = None


def detach(sid):
    code, seat_id = SID_INDEX.pop(sid, (None, None))
    room = ROOMS.get(code)
    seat = room.seat(seat_id) if room else None
    if seat and seat.sid == sid:
        seat.sid = None
    return room, seat


def caller():
    """The room and seat of whoever sent the current message."""
    code, seat_id = SID_INDEX.get(request.sid, (None, None))
    room = ROOMS.get(code)
    seat = room.seat(seat_id) if room else None
    if not seat:
        raise UserError("You're not in a game")
    return room, seat


def require_host(room, seat):
    if room.host_id != seat.id:
        raise UserError("Only the host can do that")


def fix_host(room):
    """Make sure the host is a human still in the room (prefer someone connected)."""
    host = room.seat(room.host_id)
    if host and host.kind == "human" and host.sid is not None:
        return
    candidates = room.connected_humans() or room.humans()
    if host and host.kind == "human" and host in candidates:
        return
    room.host_id = candidates[0].id if candidates else None


def delete_room_if_empty(room):
    if not room.humans():
        ROOMS.pop(room.code, None)
        return True
    if not room.connected_humans() and room.empty_since is None:
        room.empty_since = time.time()
    return False


def remove_seat(room, seat, reason):
    """Remove a seat in the lobby, or hand it to a bot for good during a game."""
    if seat.sid:
        socketio.emit("removed", {"reason": reason}, to=seat.sid)
        SID_INDEX.pop(seat.sid, None)
        seat.sid = None
    seat.disconnect_generation += 1
    seat.disconnect_deadline = None
    if room.game_running():
        seat.kind, seat.level, seat.token, seat.taken_over = "bot", TAKEOVER_LEVEL, None, False
        seat.name = room.unique_name(seat.name + " (bot)")
    else:
        room.seats.remove(seat)
    fix_host(room)


def payload(room, seat, events):
    now = time.time()
    return {
        "code": room.code,
        "you": seat.id,
        "host": room.host_id,
        "is_host": room.host_id == seat.id,
        "seats": [s.public(now) for s in room.seats],
        "min_players": MIN_PLAYERS,
        "max_players": MAX_PLAYERS,
        "game": room.game.state_for(seat.id) if room.game else None,
        "results": {
            "seconds_left": max(0, room.results["until"] - now),
            "total": RESULTS_PAUSE,
            "ready": sorted(room.results["ready"]),
        } if room.results else None,
        "deal_ms_per_card": DEAL_MS_PER_CARD,
        "deal_fly_ms": DEAL_FLY_MS,
        "events": events or [],
    }


def broadcast(room, events=None):
    """Send every connected human their own view of the room."""
    for seat in room.seats:
        if seat.sid:
            socketio.emit("room_state", payload(room, seat, events), to=seat.sid)


def check_only_bots_left(room):
    """If no remaining player has a connected human, end the game (bots alone
    could play forever, e.g. two Hackers). Returns events."""
    game = room.game
    if not room.game_running():
        return []
    for player in game.active_players():
        seat = room.seat(player.id)
        if seat.kind == "human" and not seat.taken_over:
            return []
    return game.end_early("Only bots were left")


# ---------------------------------------------------------------- round results pause

def after_moves(room, events, wake_bots=True):
    """Run after any move (human or bot): end bot-only games, put up the
    results screen if a round just finished, tell everyone, wake the bots."""
    events = list(events) + check_only_bots_left(room)
    if any(e["type"] == "round_result" for e in events):
        start_results(room)
    broadcast(room, events)
    if wake_bots:
        schedule_bots(room)


def start_results(room):
    room.results_generation += 1
    room.results = {"until": time.time() + RESULTS_PAUSE, "ready": set(), "generation": room.results_generation}
    socketio.start_background_task(results_timer, room.code, room.results_generation)


def results_timer(code, generation):
    socketio.sleep(RESULTS_PAUSE)
    with LOCK:
        room = ROOMS.get(code)
        if room and room.results and room.results["generation"] == generation:
            end_results(room)


def start_deal_wait(room):
    """Browsers animate the deal one card at a time; bots wait until it's done."""
    if room.game_running():
        cards = room.game.cards_in_play()
        room.deal_until = time.time() + (max(cards - 1, 0) * DEAL_MS_PER_CARD + DEAL_FLY_MS) / 1000 + 0.3


def end_results(room):
    room.results = None
    start_deal_wait(room)
    broadcast(room)
    schedule_bots(room)


def everyone_ready(room):
    watching = {s.id for s in room.connected_humans()}
    return watching <= room.results["ready"]


# ---------------------------------------------------------------- bots

def bot_actors(room):
    """Seats a bot needs to act for right now."""
    game = room.game
    if not room.game_running() or room.results:   # results screen up: wait
        return []
    if game.phase == PHASE_GUESSING:
        seat = room.seat(game.current_player().id)
        return [seat] if seat.bot_controlled else []
    if game.phase == PHASE_CHOOSING_JOKERS:
        waiting = game.state_for(game.players[0].id)["waiting_for_jokers"]
        return [room.seat(pid) for pid in waiting if room.seat(pid).bot_controlled]
    return []


def schedule_bots(room):
    if room.bot_task_running or not bot_actors(room) or not room.connected_humans():
        return
    room.bot_task_running = True
    socketio.start_background_task(bot_loop, room.code)


def bot_loop(code):
    while True:
        socketio.sleep(BOT_DELAY * random.uniform(0.7, 1.3))
        with LOCK:
            room = ROOMS.get(code)
            if room is None:
                return
            deal_wait = room.deal_until - time.time()
        if deal_wait > 0:                 # cards still being dealt on screen
            socketio.sleep(deal_wait)
        with LOCK:
            room = ROOMS.get(code)
            if room is None:
                return
            actors = bot_actors(room)
            if not actors or not room.connected_humans():   # nobody watching: pause
                room.bot_task_running = False
                return
            seat = actors[0]
            action = bots.choose_action(room.game, seat.id, seat.level or TAKEOVER_LEVEL)
            try:
                events = room.game.handle_action(seat.id, action)
            except IllegalAction as error:   # shouldn't happen; don't let a bot freeze the game
                app.logger.error("Bot %s made an illegal move %s: %s", seat.id, action, error)
                room.bot_task_running = False
                return
            after_moves(room, events, wake_bots=False)   # this loop carries on by itself


# ---------------------------------------------------------------- disconnect timers

def disconnect_timer(code, seat_id, generation):
    socketio.sleep(DISCONNECT_GRACE)
    with LOCK:
        room = ROOMS.get(code)
        seat = room.seat(seat_id) if room else None
        if not seat or seat.kind != "human" or seat.sid or seat.disconnect_generation != generation:
            return   # they came back (or left) in the meantime
        seat.disconnect_deadline = None
        if room.game_running():
            take_over(room, seat)
        else:
            room.seats.remove(seat)   # in the lobby: free up the seat
            fix_host(room)
            if delete_room_if_empty(room):
                return
            broadcast(room)


def take_over(room, seat):
    seat.taken_over = True
    seat.disconnect_deadline = None
    events = [{"type": "bot_took_over", "player": seat.id}] + check_only_bots_left(room)
    broadcast(room, events)
    schedule_bots(room)


def sweep_empty_rooms():
    while True:
        socketio.sleep(60)
        with LOCK:
            now = time.time()
            for code, room in list(ROOMS.items()):
                if room.empty_since and now - room.empty_since > EMPTY_ROOM_TIMEOUT:
                    del ROOMS[code]


_sweeper_started = False


def start_sweeper():
    global _sweeper_started
    if not _sweeper_started:
        _sweeper_started = True
        socketio.start_background_task(sweep_empty_rooms)


# ---------------------------------------------------------------- socket handlers

def handler(event):
    """Register a socket handler that holds the lock and turns errors into
    {"ok": False, "error": ...} replies (and an 'error_msg' to the sender)."""
    def wrap(function):
        @socketio.on(event)
        def run(data=None):
            data = data if isinstance(data, dict) else {}
            try:
                with LOCK:
                    result = function(data)
                return dict({"ok": True}, **(result or {}))
            except (UserError, IllegalAction) as error:
                socketio.emit("error_msg", {"message": str(error)}, to=request.sid)
                return {"ok": False, "error": str(error)}
        return run
    return wrap


@socketio.on("connect")
def on_connect():
    start_sweeper()


@socketio.on("disconnect")
def on_disconnect(*args):
    with LOCK:
        room, seat = detach(request.sid)
        if not seat or seat.sid is not None:
            return
        seat.disconnect_generation += 1
        seat.disconnect_deadline = time.time() + DISCONNECT_GRACE
        socketio.start_background_task(disconnect_timer, room.code, seat.id, seat.disconnect_generation)
        fix_host(room)
        delete_room_if_empty(room)
        broadcast(room)


def leave_current_room(sid):
    """If this socket is already seated somewhere, leave that seat first."""
    if sid in SID_INDEX:
        room, seat = detach(sid)
        if room and seat:
            remove_seat(room, seat, "You left the game.")
            if not delete_room_if_empty(room):
                broadcast(room)


@handler("create_room")
def create_room(data):
    name = clean_name(data.get("name"))
    check_room_limits()
    leave_current_room(request.sid)
    room = Room(new_code())
    ROOMS[room.code] = room
    seat = room.add_seat(name, "human")
    room.host_id = seat.id
    attach(room, seat, request.sid)
    broadcast(room)
    return {"code": room.code, "token": seat.token, "seat_id": seat.id}


@handler("join_room")
def join_room(data):
    name = clean_name(data.get("name"))
    code = str(data.get("code", "")).strip().upper()
    room = ROOMS.get(code)
    if room is None:
        raise UserError("No game with code " + code)
    if room.game is not None:
        raise UserError("That game has already started")
    if len(room.seats) >= MAX_PLAYERS:
        raise UserError("That game is full")
    leave_current_room(request.sid)
    seat = room.add_seat(name, "human")
    attach(room, seat, request.sid)
    fix_host(room)
    broadcast(room)
    return {"code": room.code, "token": seat.token, "seat_id": seat.id}


@handler("rejoin")
def rejoin(data):
    code = str(data.get("code", "")).strip().upper()
    token = data.get("token")
    room = ROOMS.get(code)
    seat = next((s for s in room.seats if token and s.token == token), None) if room else None
    if seat is None:
        raise UserError("That game has ended")
    attach(room, seat, request.sid)
    events = []
    if seat.taken_over:
        seat.taken_over = False
        events.append({"type": "player_returned", "player": seat.id})
    fix_host(room)
    broadcast(room, events)
    schedule_bots(room)
    return {"code": room.code, "token": seat.token, "seat_id": seat.id}


@handler("leave")
def leave(data):
    room, seat = caller()
    detach(request.sid)
    remove_seat(room, seat, "You left the game.")
    if not delete_room_if_empty(room):
        events = [{"type": "player_left", "player": seat.id, "name": seat.name}]
        broadcast(room, events + check_only_bots_left(room))
        schedule_bots(room)


@handler("chat")
def chat(data):
    room, seat = caller()
    text = " ".join(str(data.get("text") or "").split())[:MAX_CHAT_LENGTH]
    if not text:
        return
    now = time.time()
    seat.chat_times = [t for t in seat.chat_times if now - t < CHAT_WINDOW]
    if len(seat.chat_times) >= CHAT_BURST:
        raise UserError("Slow down a bit!")
    seat.chat_times.append(now)
    message = {"player": seat.id, "name": seat.name, "text": text}
    for s in room.seats:
        if s.sid:
            socketio.emit("chat", message, to=s.sid)


@handler("add_bot")
def add_bot(data):
    room, seat = caller()
    require_host(room, seat)
    level = data.get("level")
    if level not in bots.LEVELS:
        raise UserError("Unknown bot level")
    if room.game is not None:
        raise UserError("Bots can only be added in the lobby")
    if len(room.seats) >= MAX_PLAYERS:
        raise UserError("The game is full")
    room.add_seat(level.capitalize() + " Bot", "bot", level)
    broadcast(room)


@handler("remove_seat")
def remove_seat_handler(data):
    room, seat = caller()
    require_host(room, seat)
    target = room.seat(data.get("seat_id"))
    if target is None:
        raise UserError("No such player")
    if target is seat:
        raise UserError("Use Leave to leave the game")
    if room.game_running() and target.kind == "bot":
        raise UserError("Bots can't be removed mid-game")
    name = target.name
    remove_seat(room, target, "The host removed you from the game.")
    events = [{"type": "player_kicked", "player": target.id, "name": name}]
    broadcast(room, events + check_only_bots_left(room))
    schedule_bots(room)


@handler("hand_to_bot")
def hand_to_bot(data):
    room, seat = caller()
    require_host(room, seat)
    target = room.seat(data.get("seat_id"))
    if not room.game_running() or target is None or target.kind != "human" or target.sid or target.taken_over:
        raise UserError("Only a disconnected player's seat can be handed to a bot")
    target.disconnect_generation += 1   # cancel their countdown
    take_over(room, target)


@handler("start_game")
def start_game(data):
    room, seat = caller()
    require_host(room, seat)
    if room.game is not None:
        raise UserError("The game has already started")
    if not MIN_PLAYERS <= len(room.seats) <= MAX_PLAYERS:
        raise UserError("You need " + str(MIN_PLAYERS) + " to " + str(MAX_PLAYERS) + " players")
    room.game = Game([s.id for s in room.seats], names={s.id: s.name for s in room.seats})
    events = room.game.start()
    start_deal_wait(room)
    broadcast(room, events)
    schedule_bots(room)


@handler("action")
def action(data):
    room, seat = caller()
    if room.game is None:
        raise UserError("The game hasn't started")
    if room.results:
        raise UserError("Wait for the next round to start")
    after_moves(room, room.game.handle_action(seat.id, data.get("action")))


@handler("ready")
def ready(data):
    room, seat = caller()
    if not room.results:
        return
    room.results["ready"].add(seat.id)
    if everyone_ready(room):
        end_results(room)
    else:
        broadcast(room)


@handler("back_to_lobby")
def back_to_lobby(data):
    room, seat = caller()
    require_host(room, seat)
    if room.game is None:
        raise UserError("You're already in the lobby")
    room.game = None
    room.results = None
    for s in list(room.seats):
        s.taken_over = False
        if s.kind == "human" and s.sid is None:   # gone: free their seat
            room.seats.remove(s)
        s.disconnect_deadline = None
        s.disconnect_generation += 1
    fix_host(room)
    broadcast(room, [{"type": "back_to_lobby"}])


# ---------------------------------------------------------------- pages

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/healthz")
def healthz():
    return "ok"


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print("Riddle-da-Fiddle running at http://localhost:" + str(port))
    socketio.run(app, host="0.0.0.0", port=port, allow_unsafe_werkzeug=True)
