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
MAX_CARDS = 8          # a correct riddle can't take you above this
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
        self.lost_a_card = False           # for the "Perfect Pitch" win callout

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
        # Last Stand: the round after a player drops to 1 card, the first guess's
        # card type is locked for everyone for the rest of that round.
        self.last_stand_player = None   # id of the player on their last stand this round
        self.last_stand_type = None     # the locked card type (set by the round's first guess)
        # Round-win streaks (for the "hacking noises" callout): a player's streak goes up when they
        # win a round they're in, resets when they lose one, and is untouched by rounds they're not in.
        self.round_streaks = {pid: 0 for pid in player_ids}

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

    def cards_in_play(self):
        """Cards in everyone's hands this round: the biggest guess allowed."""
        return sum(len(p.hand) for p in self.active_players())

    def _total_of_type(self, card_type):
        return sum(p.count_type(card_type) for p in self.active_players())

    # ------------------------------------------------------------ flow

    def start(self):
        """Deal the first round. Returns events."""
        if self.phase != PHASE_WAITING:
            raise IllegalAction("The game has already started")
        return self._start_round()

    def _start_round(self, starter_id=None):
        """Deal a new round. starter_id: who starts (the last round's loser);
        if they're out, the next player still in after them starts.
        None (first round) = the first seat starts."""
        self.round_number += 1
        self._round_players = {p.id for p in self.active_players()}
        deck = build_deck(self.rng)
        for player in self.players:
            player.hand = []
        # Deal like a real dealer: one card at a time round the table, skipping
        # anyone whose hand is full. If the 54 cards run out (e.g. 8 players near
        # the 8-card cap), the players with the most cards are the ones left short.
        dealing = [p for p in self.players if not p.eliminated]
        while deck and dealing:
            for player in dealing:
                if deck:
                    player.hand.append(deck.pop())
            dealing = [p for p in dealing if len(p.hand) < p.card_count]

        if starter_id is None:
            self.current_index = self.players.index(self.active_players()[0])
        else:
            index = self.players.index(self.get_player(starter_id))
            if self.players[index].eliminated:
                index = self._next_active_index(index)
            self.current_index = index
        self.current_guess = None
        self.pending_fiddle = None
        self.last_stand_type = None
        self.phase = PHASE_GUESSING
        return [{
            "type": "round_started",
            "round": self.round_number,
            "starting_player": self.players[self.current_index].id,
            "last_stand": self.last_stand_player,
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
        if isinstance(quantity, str) and quantity.strip().lstrip("-").isdigit() and len(quantity.strip()) <= 4:
            quantity = int(quantity)
        if not isinstance(quantity, int) or isinstance(quantity, bool):
            raise IllegalAction("Quantity must be a whole number")
        if quantity > self.cards_in_play():
            raise IllegalAction("There are only " + str(self.cards_in_play()) + " cards in play")
        if self.last_stand_type and card_type != self.last_stand_type:
            raise IllegalAction("Last Stand! Everyone has to guess " + self.last_stand_type.capitalize() + " this round")
        if not is_higher_guess(card_type, quantity, self.current_guess):
            raise IllegalAction("Not a legal guess: it must be higher than the previous guess")

        self.current_guess = {"player_id": player.id, "card_type": card_type, "quantity": quantity}
        if self.last_stand_player and self.last_stand_type is None:
            self.last_stand_type = card_type        # the first guess sets the locked type
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
        loser.lost_a_card = True

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
            "next_starter": loser.id,       # whoever lost a card starts next round
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
        at_cap = correct and riddler.card_count >= MAX_CARDS
        if not correct:
            change = -2
        elif at_cap:
            change = 0                      # already at the maximum: no extra card
        else:
            change = 1
        riddler.card_count += change
        if change < 0:
            riddler.lost_a_card = True

        result = {
            "kind": "riddle",
            "caller": riddler.id,
            "guesser": guess["player_id"],
            "guess": dict(guess),
            "count": count,
            "jokers_in_play": jokers,
            "correct": correct,
            "change": {riddler.id: change},
            "at_cap": at_cap,
            # Correct: the guesser "got riddled" and starts. Wrong: the riddler lost 2 and starts.
            "next_starter": guess["player_id"] if correct else riddler.id,
        }
        return self._finish_round(result)

    def _finish_round(self, result):
        # Reveal every hand from the round that just ended.
        result["hands"] = {p.id: [dict(c) for c in p.hand] for p in self.players if p.hand}
        result["card_counts_before"] = {p.id: p.card_count - result["change"].get(p.id, 0) for p in self.players}
        result["card_counts"] = {p.id: max(p.card_count, 0) for p in self.players}
        result["round"] = self.round_number
        # who won / lost this round (a wrong riddle has a loser but no winner)
        if result["kind"] == "fiddle":
            round_winner, round_loser = result["winner"], result["loser"]
        elif result["correct"]:
            round_winner, round_loser = result["caller"], result["guesser"]
        else:
            round_winner, round_loser = None, result["caller"]
        if round_winner:
            self.round_streaks[round_winner] += 1
        self.round_streaks[round_loser] = 0
        result["round_winner"] = round_winner
        result["winner_streak"] = self.round_streaks[round_winner] if round_winner else 0
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

        # Last Stand: whoever just dropped to exactly 1 card (every time it happens)
        dropped = [pid for pid, change in result["change"].items()
                   if change < 0 and self.get_player(pid).card_count == 1]
        self.last_stand_player = dropped[0] if dropped else None
        return events + self._start_round(result["next_starter"])

    def end_early(self, reason=""):
        """Stop the game now; the remaining player with the most cards wins.
        (Used by the server, e.g. when only bots are left, since two perfect
        Hacker bots can riddle forever.)"""
        if self.phase in (PHASE_WAITING, PHASE_GAME_OVER):
            return []
        remaining = self.active_players()
        winner = max(remaining, key=lambda p: p.card_count)   # ties: earliest seat
        self.phase = PHASE_GAME_OVER
        self.pending_fiddle = None
        self.winner_id = winner.id
        return [{"type": "game_won", "player": winner.id, "early": True, "reason": reason}]

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
            "cards_in_play": self.cards_in_play(),
            "last_stand": (
                {"player": self.last_stand_player, "card_type": self.last_stand_type}
                if self.last_stand_player and self.phase in (PHASE_GUESSING, PHASE_CHOOSING_JOKERS) else None
            ),
            "current_player": current.id if current else None,
            "current_guess": dict(self.current_guess) if self.current_guess else None,
            "your_turn": my_turn,
            "can_guess": my_turn,
            "can_fiddle_or_riddle": my_turn and self.current_guess is not None,
            "fiddle": (
                {"fiddler": pending["fiddler_id"], "guesser": pending["guesser_id"]}
                if pending and self.phase == PHASE_CHOOSING_JOKERS else None
            ),
            "waiting_for_jokers": (
                [pid for pid in pending["waiting_for"] if pid not in pending["choices"]]
                if pending and self.phase == PHASE_CHOOSING_JOKERS else []
            ),
            "last_round": self.last_round,
            "winner": self.winner_id,
            # win callouts: did the winner never lose a card? / win on their last card?
            "winner_flawless": (not self.get_player(self.winner_id).lost_a_card) if self.winner_id else False,
        }
