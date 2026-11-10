"""
Computer players for Riddle-da-Fiddle.

    action = choose_action(game, player_id, "pro")
    events = game.handle_action(player_id, action)

Levels:
    noob   - mostly random: random guesses, fiddles and riddles on a whim.
    pro    - plays the odds: works out how likely each guess is from its own
             hand and the cards it can't see.
    hacker - cheats: looks at every hand, so it always knows the true count.

Bots only run on the server, so the hacker's view of all hands never reaches
any player's browser.
"""

import random
from math import comb

from riddle_da_fiddle_engine import (
    CARD_TYPES, TYPE_RANK, SUITS, MAX_CARDS, PHASE_GUESSING, PHASE_CHOOSING_JOKERS, card_types, is_joker,
)

LEVELS = ("noob", "pro", "hacker")

FULL_DECK = [{"suit": s, "rank": r} for s in SUITS for r in range(1, 14)] + [
    {"suit": "joker", "rank": 1}, {"suit": "joker", "rank": 2},
]


def choose_action(game, player_id, level, rng=random):
    """Pick an action dict for this bot, or None if it has nothing to do."""
    me = game.get_player(player_id)
    if game.phase == PHASE_CHOOSING_JOKERS:
        return {"type": "use_jokers", "amount": _choose_jokers(game, me, level, rng)}
    if game.phase == PHASE_GUESSING and game.current_player() is me:
        if level == "hacker":
            return _hacker_turn(game, me, rng)
        if level == "pro":
            return _pro_turn(game, me, rng)
        return _noob_turn(game, me, rng)
    return None


# ---------------------------------------------------------------- shared helpers

def min_legal_quantity(card_type, previous):
    """Smallest quantity of card_type that beats the previous guess."""
    if previous is None:
        return 1
    if TYPE_RANK[card_type] > TYPE_RANK[previous["card_type"]]:
        return previous["quantity"]
    return previous["quantity"] + 1


def _guess(card_type, quantity):
    return {"type": "guess", "card_type": card_type, "quantity": quantity}


def _choose_jokers(game, me, level, rng):
    have = me.joker_count()
    if level == "noob":
        return rng.randint(0, have)
    pending = game.pending_fiddle
    if me.id == pending["guesser_id"]:   # I want the count high: my guess was true
        return have
    return 0                             # fiddler wants it low; bystanders don't care


# ---------------------------------------------------------------- noob

def _noob_turn(game, me, rng):
    previous = game.current_guess
    if previous is not None:
        roll = rng.random()
        if roll < 0.25:
            return {"type": "fiddle"}
        if roll < 0.32:
            return {"type": "riddle"}
    card_type = rng.choice(CARD_TYPES)
    if previous is None:
        return _guess(card_type, rng.randint(1, 3))
    return _guess(card_type, min_legal_quantity(card_type, previous) + rng.choice([0, 0, 1, 2]))


# ---------------------------------------------------------------- pro

def _others_distribution(game, me, card_type):
    """Probability that the other players hold exactly k cards of card_type,
    for k = 0..(cards in other hands). Exact hypergeometric over unseen cards."""
    unseen = [c for c in FULL_DECK if c not in me.hand]
    n_unseen = len(unseen)
    n_others = sum(len(p.hand) for p in game.active_players() if p is not me)
    n_others = min(n_others, n_unseen)
    matching = sum(1 for c in unseen if card_type in card_types(c))
    total = comb(n_unseen, n_others)
    return [comb(matching, k) * comb(n_unseen - matching, n_others - k) / total
            for k in range(n_others + 1)]


def _expected_other_jokers(game, me):
    unseen = 54 - len(me.hand)
    n_others = sum(len(p.hand) for p in game.active_players() if p is not me)
    jokers_left = 2 - me.joker_count()
    return n_others * jokers_left / unseen if unseen else 0


PRO_RANDOM_MOVE_CHANCE = 0.15


def _pro_turn(game, me, rng):
    # Now and then play a random move: feels less robotic, and stops a table of
    # Pro bots getting stuck in a cycle when nearly the whole deck is dealt.
    if rng.random() < PRO_RANDOM_MOVE_CHANCE:
        return _noob_turn(game, me, rng)
    previous = game.current_guess
    my_jokers = me.joker_count()
    own = {t: me.count_type(t) for t in CARD_TYPES}
    dist = {t: _others_distribution(game, me, t) for t in CARD_TYPES}

    def p_at_least(card_type, quantity, extra=0):
        return sum(p for k, p in enumerate(dist[card_type]) if own[card_type] + extra + k >= quantity)

    # Best raise: for each type, the lowest legal guess and how likely it is to be true
    # (my own jokers count for me if someone fiddles my guess).
    raises = []
    for t in CARD_TYPES:
        q = min_legal_quantity(t, previous)
        raises.append((p_at_least(t, q, my_jokers), t, q))
    best_p, best_type, best_q = max(raises)

    if previous is None:
        # Opening guess: the highest quantity that's still ~65% likely.
        options = []
        for t in CARD_TYPES:
            q = 1
            while p_at_least(t, q + 1, my_jokers) >= 0.65:
                q += 1
            options.append((q, p_at_least(t, q, my_jokers), t))
        q, _, t = max(options)
        return _guess(t, q)

    t, q = previous["card_type"], previous["quantity"]
    p_true = p_at_least(t, q)

    # Riddle wins if the count can land exactly on q (jokers can fill the gap).
    jokers_est = my_jokers + round(_expected_other_jokers(game, me))
    p_riddle = sum(p for k, p in enumerate(dist[t]) if q - jokers_est <= own[t] + k <= q)

    if p_riddle >= 0.5:
        return {"type": "riddle"}
    if (1 - p_true) >= best_p:
        return {"type": "fiddle"}
    return _guess(best_type, best_q)


# ---------------------------------------------------------------- hacker

def _hacker_turn(game, me, rng):
    previous = game.current_guess
    players = game.active_players()
    base = {t: sum(p.count_type(t) for p in players) for t in CARD_TYPES}
    total_jokers = sum(p.joker_count() for p in players)
    my_jokers = me.joker_count()

    if previous is not None:
        t, q = previous["card_type"], previous["quantity"]
        at_cap = me.card_count >= MAX_CARDS     # riddling wins nothing at the cap: skip it
        if base[t] <= q <= base[t] + total_jokers and not at_cap:
            return {"type": "riddle"}       # jokers can make the count exactly q: guaranteed
        if q > base[t] + total_jokers:
            return {"type": "fiddle"}       # false even with every joker: guaranteed

    if previous is None:
        t = max(CARD_TYPES, key=lambda c: base[c])
        return _guess(t, max(1, base[t] + my_jokers - rng.randint(0, 1)))

    # A raise that's true even if nobody else counts their jokers.
    safe = []
    for t in CARD_TYPES:
        q = min_legal_quantity(t, previous)
        if q <= base[t] + my_jokers:
            safe.append((q, -(base[t] + my_jokers - q), t))
    if safe:
        q, _, t = min(safe)
        return _guess(t, q)

    # No true raise exists: bluff with the smallest one.
    q, t = min((min_legal_quantity(t, previous), t) for t in CARD_TYPES)
    return _guess(t, q)
