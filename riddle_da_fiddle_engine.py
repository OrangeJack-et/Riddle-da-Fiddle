"""
Riddle-da-Fiddle game engine (web-ready version).

Same rules as riddle_da_fiddle_desktop.py, but written as a *state machine*:
the game never asks for input and never prints. Instead:

    game = Game(["alice", "bob", "carol"])
    events = game.start()
    events = game.handle_action("alice", {"type": "guess", "card_type": "middle", "quantity": 2})
    view   = game.state_for("bob")     # what bob is allowed to see

- handle_action() checks the move, updates the game, and returns a list of
  events (plain dicts) describing what happened. A bad move raises
  IllegalAction with a human-readable reason.
- state_for() returns only what one player may see: their own hand, everyone's
  card counts and the current guess. Other players' hands are only revealed
  in the result of a fiddle or riddle.

Everything returned is plain dicts/lists/strings/ints, so it can be sent to a
browser as JSON directly.
"""

import random

# ---------------------------------------------------------------- constants

STARTING_CARDS = 6
CARD_TYPES = ("lower", "middle", "face")
TYPE_RANK = {"lower": 0, "middle": 1, "face": 2}
SUITS = ("hearts", "diamonds", "clubs", "spades")

PHASE_WAITING = "waiting_to_start"
PHASE_GUESSING = "guessing"
PHASE_CHOOSING_JOKERS = "choosing_jokers"
PHASE_GAME_OVER = "game_over"


class IllegalAction(Exception):
    """Raised when a player tries a move that isn't allowed right now."""


# ---------------------------------------------------------------- cards

def build_deck(rng=random):
    """52 cards + 2 jokers, shuffled. Rank 1 = Ace, 11-13 = J/Q/K."""
    deck = [{"suit": suit, "rank": rank} for suit in SUITS for rank in range(1, 14)]
    deck.append({"suit": "joker", "rank": 1})
    deck.append({"suit": "joker", "rank": 2})
    rng.shuffle(deck)
    return deck


def is_joker(card):
    return card["suit"] == "joker"


def card_types(card):
    """Which types a card counts as. Aces count as all three, jokers as none
    (jokers are handled separately)."""
    if is_joker(card):
        return set()
    rank = card["rank"]
    if rank == 1:
        return set(CARD_TYPES)
    if rank <= 6:
        return {"lower"}
    if rank <= 10:
        return {"middle"}
    return {"face"}


def is_higher_guess(card_type, quantity, previous):
    """True if (card_type, quantity) beats the previous guess.
    previous is None for the first guess of a round."""
    if previous is None:
        return quantity >= 1
    if quantity > previous["quantity"]:
        return True
    if quantity == previous["quantity"]:
        return TYPE_RANK[card_type] > TYPE_RANK[previous["card_type"]]
    return False


# ---------------------------------------------------------------- player

class Player:
    def __init__(self, player_id, name=None):
        self.id = player_id
        self.name = name if name is not None else str(player_id)
        self.card_count = STARTING_CARDS   # also your lives
        self.hand = []

    @property
    def eliminated(self):
        return self.card_count <= 0

    def count_type(self, card_type):
        return sum(1 for card in self.hand if card_type in card_types(card))

    def joker_count(self):
        return sum(1 for card in self.hand if is_joker(card))


# ---------------------------------------------------------------- game

class Game:
    def __init__(self, player_ids, names=None, rng=None):
        """player_ids: list of unique ids (e.g. usernames or socket ids).
        names: optional {id: display name}."""
        player_ids = list(player_ids)
        if len(player_ids) < 2:
            raise ValueError("Need at least 2 players")
        if len(set(player_ids)) != len(player_ids):
            raise ValueError("Player ids must be unique")
        names = names or {}

        self.rng = rng or random.Random()
        self.players = [Player(pid, names.get(pid)) for pid in player_ids]
        self.phase = PHASE_WAITING
        self.round_number = 0
        self.current_index = 0          # whose turn it is (index into self.players)
        self.current_guess = None       # {"player_id", "card_type", "quantity"}
        self.pending_fiddle = None      # set while players choose jokers
        self.last_round = None          # result of the most recent fiddle/riddle
        self.winner_id = None
        self._round_players = set()     # ids of players dealt in this round

    # ------------------------------------------------------------ helpers

    def get_player(self, player_id):
        for player in self.players:
            if player.id == player_id:
                return player
        raise IllegalAction("Unknown player: " + str(player_id))

    def active_players(self):
        return [p for p in self.players if not p.eliminated]

    def current_player(self):
        if self.phase != PHASE_GUESSING:
            return None
        return self.players[self.current_index]

    def _next_active_index(self, index):
        n = len(self.players)
        for step in range(1, n + 1):
            candidate = (index + step) % n
            if not self.players[candidate].eliminated:
                return candidate
        return index

    def _total_of_type(self, card_type):
        return sum(p.count_type(card_type) for p in self.active_players())

    # ------------------------------------------------------------ flow

    def start(self):
        """Deal the first round. Returns events."""
        if self.phase != PHASE_WAITING:
            raise IllegalAction("The game has already started")
        return self._start_round()

    def _start_round(self):
        self.round_number += 1
        self._round_players = {p.id for p in self.active_players()}
        deck = build_deck(self.rng)
        for player in self.players:
            player.hand = []
            if player.eliminated:
                continue
            # The deck only has 54 cards; if hands would need more, deal what's left.
            for _ in range(min(player.card_count, len(deck))):
                player.hand.append(deck.pop())

        # Same as the desktop version: the first remaining player starts each round.
        self.current_index = self.players.index(self.active_players()[0])
        self.current_guess = None
        self.pending_fiddle = None
        self.phase = PHASE_GUESSING
        return [{
            "type": "round_started",
            "round": self.round_number,
            "starting_player": self.players[self.current_index].id,
        }]

    def handle_action(self, player_id, action):
        """Apply one player's action. Returns a list of event dicts.

        Actions:
            {"type": "guess", "card_type": "lower"|"middle"|"face", "quantity": int}
            {"type": "fiddle"}
            {"type": "riddle"}
            {"type": "use_jokers", "amount": int}    (only after a fiddle)
        """
        player = self.get_player(player_id)
        if not isinstance(action, dict):
            raise IllegalAction("Action must be a dict")
        action_type = action.get("type")

        if self.phase == PHASE_WAITING:
            raise IllegalAction("The game hasn't started yet")
        if self.phase == PHASE_GAME_OVER:
            raise IllegalAction("The game is over")
        if player.eliminated:
            raise IllegalAction("You're out of the game")

        if self.phase == PHASE_CHOOSING_JOKERS:
            if action_type != "use_jokers":
                raise IllegalAction("Waiting for players to choose how many jokers to count")
            return self._use_jokers(player, action)

        # PHASE_GUESSING
        if player is not self.current_player():
            raise IllegalAction("It's not your turn")
        if action_type == "guess":
            return self._guess(player, action)
        if action_type in ("fiddle", "riddle"):
            if self.current_guess is None:
                raise IllegalAction("The first player of a round has to guess")
            if action_type == "fiddle":
                return self._fiddle(player)
            return self._riddle(player)
        raise IllegalAction("Unknown action: " + str(action_type))

    # ------------------------------------------------------------ actions

    def _guess(self, player, action):
        card_type = str(action.get("card_type", "")).strip().lower()
        if card_type not in CARD_TYPES:
            raise IllegalAction("Card type must be lower, middle or face")
        quantity = action.get("quantity")
        if isinstance(quantity, str) and quantity.strip().lstrip("-").isdigit():
            quantity = int(quantity)
        if not isinstance(quantity, int) or isinstance(quantity, bool):
            raise IllegalAction("Quantity must be a whole number")
        if not is_higher_guess(card_type, quantity, self.current_guess):
            raise IllegalAction("Not a legal guess: it must be higher than the previous guess")

        self.current_guess = {"player_id": player.id, "card_type": card_type, "quantity": quantity}
        self.current_index = self._next_active_index(self.current_index)
        return [{
            "type": "guess_made",
            "player": player.id,
            "card_type": card_type,
            "quantity": quantity,
            "next_player": self.players[self.current_index].id,
        }]

    def _fiddle(self, fiddler):
        joker_holders = [p.id for p in self.active_players() if p.joker_count() > 0]
        self.pending_fiddle = {
            "fiddler_id": fiddler.id,
            "guesser_id": self.current_guess["player_id"],
            "choices": {},              # player_id -> jokers counted
            "waiting_for": joker_holders,
        }
        events = [{
            "type": "fiddle_called",
            "player": fiddler.id,
            "guesser": self.current_guess["player_id"],
            "guess": dict(self.current_guess),
        }]
        if not joker_holders:
            return events + self._resolve_fiddle()

        # Everyone holding jokers now chooses (at the same time) how many count.
        self.phase = PHASE_CHOOSING_JOKERS
        events.append({"type": "choose_jokers", "waiting_for": list(joker_holders)})
        return events

    def _use_jokers(self, player, action):
        pending = self.pending_fiddle
        if player.id not in pending["waiting_for"]:
            raise IllegalAction("You don't need to choose jokers")
        if player.id in pending["choices"]:
            raise IllegalAction("You've already chosen")
        amount = action.get("amount")
        if isinstance(amount, str) and amount.strip().isdigit():
            amount = int(amount)
        if not isinstance(amount, int) or isinstance(amount, bool):
            raise IllegalAction("Amount must be a whole number")
        have = player.joker_count()
        if not 0 <= amount <= have:
            raise IllegalAction("You have " + str(have) + " joker(s); choose 0 to " + str(have))

        pending["choices"][player.id] = amount
        # Don't reveal the amount yet: only that this player has chosen.
        events = [{"type": "jokers_chosen", "player": player.id}]
        if len(pending["choices"]) == len(pending["waiting_for"]):
            events += self._resolve_fiddle()
        return events

    def _resolve_fiddle(self):
        pending = self.pending_fiddle
        guess = self.current_guess
        fiddler = self.get_player(pending["fiddler_id"])
        guesser = self.get_player(pending["guesser_id"])

        base_count = self._total_of_type(guess["card_type"])
        jokers_counted = sum(pending["choices"].values())
        count = base_count + jokers_counted

        if guess["quantity"] <= count:      # the guess was true: fiddler loses
            loser, winner = fiddler, guesser
        else:                               # the guess was false: guesser loses
            loser, winner = guesser, fiddler
        loser.card_count -= 1

        result = {
            "kind": "fiddle",
            "caller": fiddler.id,
            "guesser": guesser.id,
            "guess": dict(guess),
            "count": count,
            "jokers_counted": dict(pending["choices"]),
            "winner": winner.id,
            "loser": loser.id,
            "change": {loser.id: -1},
        }
        return self._finish_round(result)

    def _riddle(self, riddler):
        guess = self.current_guess
        base_count = self._total_of_type(guess["card_type"])
        jokers = sum(p.joker_count() for p in self.active_players())

        # Jokers are flexible on a riddle: they can make the count anything
        # from base_count to base_count + jokers.
        count = base_count
        if base_count <= guess["quantity"] <= base_count + jokers:
            count = guess["quantity"]

        correct = guess["quantity"] == count
        change = 1 if correct else -2
        riddler.card_count += change

        result = {
            "kind": "riddle",
            "caller": riddler.id,
            "guesser": guess["player_id"],
            "guess": dict(guess),
            "count": count,
            "jokers_in_play": jokers,
            "correct": correct,
            "change": {riddler.id: change},
        }
        return self._finish_round(result)

    def _finish_round(self, result):
        # Reveal every hand from the round that just ended.
        result["hands"] = {p.id: [dict(c) for c in p.hand] for p in self.players if p.hand}
        result["card_counts"] = {p.id: max(p.card_count, 0) for p in self.players}
        result["round"] = self.round_number
        self.last_round = result
        self.pending_fiddle = None

        events = [dict(result, type="round_result")]

        for player in self.players:
            if player.eliminated and player.id in self._round_players:   # just knocked out
                player.card_count = 0
                player.hand = []
                events.append({"type": "player_eliminated", "player": player.id})

        remaining = self.active_players()
        if len(remaining) < 2:
            self.phase = PHASE_GAME_OVER
            self.winner_id = remaining[0].id if remaining else None
            events.append({"type": "game_won", "player": self.winner_id})
            return events

        return events + self._start_round()

    # ------------------------------------------------------------ views

    def state_for(self, player_id):
        """Everything one player is allowed to see, as a JSON-ready dict."""
        me = self.get_player(player_id)
        current = self.current_player()
        pending = self.pending_fiddle
        my_turn = current is me

        return {
            "you": me.id,
            "phase": self.phase,
            "round": self.round_number,
            "your_hand": [dict(c) for c in me.hand],
            "your_jokers": me.joker_count(),
            "players": [
                {
                    "id": p.id,
                    "name": p.name,
                    "card_count": max(p.card_count, 0),
                    "eliminated": p.eliminated,
                }
                for p in self.players
            ],
            "current_player": current.id if current else None,
            "current_guess": dict(self.current_guess) if self.current_guess else None,
            "your_turn": my_turn,
            "can_guess": my_turn,
            "can_fiddle_or_riddle": my_turn and self.current_guess is not None,
            "waiting_for_jokers": (
                [pid for pid in pending["waiting_for"] if pid not in pending["choices"]]
                if pending and self.phase == PHASE_CHOOSING_JOKERS else []
            ),
            "last_round": self.last_round,
            "winner": self.winner_id,
        }
