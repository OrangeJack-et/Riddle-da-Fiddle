"""
Play the web-ready engine in the terminal (everyone shares one keyboard).

This is just a test harness: it turns typed input into engine actions and
prints the events that come back. The website will do the same job, but with
buttons and websockets instead of input() and print().

    python play_engine_in_terminal.py
"""

from riddle_da_fiddle_engine import Game, IllegalAction, PHASE_GUESSING, PHASE_CHOOSING_JOKERS, PHASE_GAME_OVER

RANK_NAMES = {1: "A", 11: "J", 12: "Q", 13: "K"}
SUIT_SYMBOLS = {"hearts": "H", "diamonds": "D", "clubs": "C", "spades": "S"}  # plain letters: Windows terminals can't always print ♥♦♣♠


def card_text(card):
    if card["suit"] == "joker":
        return "Joker"
    return RANK_NAMES.get(card["rank"], str(card["rank"])) + SUIT_SYMBOLS[card["suit"]]


def hand_text(hand):
    return ", ".join(card_text(c) for c in hand) or "(empty)"


def describe(event):
    kind = event["type"]
    if kind == "round_started":
        return "\n=== Round " + str(event["round"]) + " === Player " + str(event["starting_player"]) + " starts"
    if kind == "guess_made":
        return "Player " + str(event["player"]) + " guesses " + str(event["quantity"]) + " " + event["card_type"]
    if kind == "fiddle_called":
        g = event["guess"]
        return "Player " + str(event["player"]) + " FIDDLES player " + str(event["guesser"]) + "'s guess of " + str(g["quantity"]) + " " + g["card_type"]
    if kind == "choose_jokers":
        return "Jokers in play! Waiting for: " + ", ".join("player " + str(p) for p in event["waiting_for"])
    if kind == "jokers_chosen":
        return "Player " + str(event["player"]) + " has chosen their jokers"
    if kind == "round_result":
        lines = ["--- Reveal ---"]
        for pid, hand in event["hands"].items():
            lines.append("  player " + str(pid) + ": " + hand_text(hand))
        g = event["guess"]
        lines.append("There were " + str(event["count"]) + " " + g["card_type"] + " (guess was " + str(g["quantity"]) + ")")
        if event["kind"] == "fiddle":
            lines.append("Player " + str(event["winner"]) + " won the round, player " + str(event["loser"]) + " loses a card")
        elif event["correct"] and event["at_cap"]:
            lines.append("Player " + str(event["caller"]) + " riddled correctly but is already at the maximum of 8 cards")
        elif event["correct"]:
            lines.append("Player " + str(event["caller"]) + " riddled correctly and gains a card!")
        else:
            lines.append("Player " + str(event["caller"]) + " riddled incorrectly and loses 2 cards")
        lines.append("Cards remaining: " + ", ".join("player " + str(p) + ": " + str(n) for p, n in event["card_counts"].items()))
        return "\n".join(lines)
    if kind == "player_eliminated":
        return "Player " + str(event["player"]) + " is out!"
    if kind == "game_won":
        return "\n*** Player " + str(event["player"]) + " won the game! ***"
    return str(event)


def show(events):
    for event in events:
        print(describe(event))


def ask_turn(game, player_id):
    state = game.state_for(player_id)
    print("\nPlayer " + str(player_id) + "'s turn. Your hand: " + hand_text(state["your_hand"]))
    if not state["can_fiddle_or_riddle"]:
        choice = "guess"
    else:
        g = state["current_guess"]
        choice = input("Previous guess: " + str(g["quantity"]) + " " + g["card_type"] + ". Guess, fiddle or riddle?: ").strip().lower()
    if choice == "guess":
        card_type = input("Which card type (lower, middle, face): ")
        quantity = input("How many: ")
        return {"type": "guess", "card_type": card_type, "quantity": quantity}
    return {"type": choice}


def main():
    while True:
        try:
            num_players = int(input("How many players? "))
            game = Game([str(i) for i in range(1, num_players + 1)])
            break
        except ValueError as error:
            print(error)

    show(game.start())
    while game.phase != PHASE_GAME_OVER:
        try:
            if game.phase == PHASE_GUESSING:
                player_id = game.current_player().id
                action = ask_turn(game, player_id)
            elif game.phase == PHASE_CHOOSING_JOKERS:
                player_id = game.state_for(game.players[0].id)["waiting_for_jokers"][0]
                have = game.state_for(player_id)["your_jokers"]
                amount = input("Player " + str(player_id) + ", you have " + str(have) + " joker(s). How many count?: ")
                action = {"type": "use_jokers", "amount": amount}
            show(game.handle_action(player_id, action))
        except IllegalAction as error:
            print("Not allowed: " + str(error))


if __name__ == "__main__":
    main()
