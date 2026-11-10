# Riddle-da-Fiddle 🎻

*A silly little fiddly card game.*

## Status

A **multiplayer web version** is in progress. The game logic is ready for it; the server and website come next.

## Files

| File | What it is |
|------|------------|
| `riddle_da_fiddle_desktop.py` | The original terminal game, driven by `input()`. Kept as-is. |
| `riddle_da_fiddle_engine.py` | Web-ready game engine with the same rules: no `input()`/`print()`, driven by player actions, returns events and per-player views. |
| `play_engine_in_terminal.py` | Plays the web-ready engine in the terminal, for testing it without the website. |

## How to run

Requires Python 3 (no extra packages). Everyone shares one computer.

Original version:

```
python riddle_da_fiddle_desktop.py
```

Web-ready engine, in the terminal:

```
python play_engine_in_terminal.py
```

## Rules

### The deck

A standard 52-card deck plus two jokers.

Every card belongs to one or more **types**:

| Type   | Cards           |
|--------|-----------------|
| Lower  | 2 – 6           |
| Middle | 7 – 10          |
| Face   | Jack, Queen, King |

- **Aces are wild**: they count as Lower, Middle *and* Face.
- **Jokers** are flexible:
  - On a **fiddle**, each player holding jokers chooses how many of them count towards the guessed type.
  - On a **riddle**, jokers can count as any number from none to all of them, so the riddle is correct if the guess falls in that range.

### Setup

Each player starts with **6 cards**. Your card count is also your number of lives: lose them all and you're out.

At the start of every round the deck is reshuffled and each player is dealt as many cards as their current card count. You can see only your own hand.

### Guessing

Players take turns around the table. The first player makes a **guess**: a card type and a quantity, e.g. *"Middle 4"*. This is a claim that **across everyone's hands combined there are at least 4 Middle cards**.

On your turn you must do one of three things:

1. **Guess**: make a higher guess than the previous one. A guess is higher if it has a **larger quantity**, or the **same quantity with a higher type** (Lower < Middle < Face).
2. **Fiddle**: you think the previous guess is wrong. All hands are revealed and counted:
   - if there are **at least** that many, the guess was true, and **you lose 1 card**;
   - otherwise the guesser **loses 1 card**.
3. **Riddle**: you think the previous guess is *exactly* right. All hands are revealed and counted:
   - if the count is **exactly** the guessed number, **you gain 1 card**;
   - otherwise **you lose 2 cards**.

A fiddle or riddle ends the round. Cards are redealt and a new round begins.

### Winning

Players with no cards left are eliminated. The **last player with cards wins**.

## Plans

- [x] Restructure the game logic so it's driven by player actions rather than terminal input
- [ ] Multiplayer web version (Python backend, browser frontend)
- [ ] Game rooms / lobby codes so friends can play together online
- [ ] Graphics and a violin-themed look 🎻
