# Riddle-da-Fiddle 🎻

*A silly little fiddly card game.*

## Status

The **multiplayer web version** works: lobbies with codes, 2–8 players, bots, and reconnecting. It still uses a plain test page; the proper design comes next.

## Files

| File | What it is |
|------|------------|
| `riddle_da_fiddle_desktop.py` | The original terminal game, driven by `input()`. Kept as-is. |
| `riddle_da_fiddle_engine.py` | Web-ready game engine with the same rules: no `input()`/`print()`, driven by player actions, returns events and per-player views. |
| `bots.py` | Computer players: **Noob** (mostly random), **Pro** (plays the odds), **Hacker** (cheats: sees every hand). |
| `server.py` | Web server (Flask-SocketIO): lobbies, host controls, bots, disconnect handling. |
| `templates/index.html`, `static/app.js`, `static/style.css` | The web page. |
| `static/cards/` | Pixel card images (50×75 PNG), named like `ace_of_spades.png`, `red_joker.png`, plus `back.png`. Replace them with your own art using the same names. |
| `assets_incoming/` | Raw downloaded asset packs (git-ignored: some licences don't allow sharing the originals). |
| `play_engine_in_terminal.py` | Plays the web-ready engine in the terminal, for testing it without the website. |
| `requirements.txt`, `render.yaml` | What the server needs, and how Render should run it. |

## How to run

### Web version (on your computer)

```
pip install -r requirements.txt
python server.py
```

Then open http://localhost:5000. To test with several players on one computer, use different browsers, or `localhost:5000` in one tab and `127.0.0.1:5000` in another. Tabs of the same address share a saved seat.

### Terminal versions (Python 3, no extra packages, everyone shares one computer)

```
python riddle_da_fiddle_desktop.py
python play_engine_in_terminal.py
```

## How the web version works

- **Lobby:** create a game to get a 4-letter code (or an invite link) and become the host. Friends join with the code. 2–8 seats.
- **Host controls:** add Noob/Pro/Hacker bots, remove players or bots, start the game, end it early.
- **Hidden cards:** each browser is only ever sent its own hand. Other hands only appear in the reveal after a fiddle or riddle. Bots run on the server, so the Hacker's view never reaches a browser.
- **Disconnects:** a refresh or wifi drop puts you back in your seat. If you're gone for 30 seconds (or the host clicks *Bot now*), a Pro bot plays your seat until you return. If you click *Leave*, or the host kicks you, a bot keeps your seat for the rest of that game.
- **Round results:** after each fiddle or riddle, a results screen stays up for 10 seconds (or until everyone clicks Ready). Nobody, bots included, can move until it closes.
- **Rule book:** the 📖 Rules button in the top bar opens the full rules on any screen, even mid-game.
- **Music:** background violin starts on your first click; 🔊 in the top bar mutes it (remembered per browser). Replace `static/audio/vivaldi-winter.*` to change the track.
- **Chat:** messages go in the log; a pop-up shows new ones when the log is off screen.
- **Limits:** one address can create 5 games every 10 minutes, and at most 200 games can be open at once.
- **Only bots left:** if no human is left playing, the game ends and the bot with the most cards wins. Two Hacker bots would otherwise riddle forever.

## Credits

Music: Vivaldi, *Winter* (1st movement), performed by John Harrison with the Wichita State University Chamber Players, licensed [CC BY-SA](https://creativecommons.org/licenses/by-sa/4.0/), via [Wikimedia Commons](https://commons.wikimedia.org/wiki/File:Vivaldi_-_Four_Seasons_4_Winter_mvt_1_Allegro_non_molto_-_John_Harrison_violin.oga). Files: `static/audio/vivaldi-winter.oga` (original) and `vivaldi-winter.mp3` (Wikimedia's MP3 copy, same licence).

Card art: [Pixel Cards by Jfred](https://jfredd.itch.io/pixel-cards) (CC0). The two jokers use the violin from the [16 Pixel Art Musical Instruments pack by K4nnx](https://k4nnx.itch.io/16-pixel-art-musical-instruments-sprites-pack-32x32) (free for commercial use; the original pack isn't redistributed here).

Frames and buttons: [Pixel Art Fantasy UI Pack by Quintino Pixels](https://quintino-pixels.itch.io/pixel-art-ui-elements) (CC0), cut into `static/ui/` (button colours recoloured from the teal one). Font: [Pixelify Sans](https://fonts.google.com/specimen/Pixelify+Sans) (Open Font License), loaded from Google Fonts.

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

Cards are dealt one at a time round the table. If the deck runs out (possible with 7–8 players near the 8-card maximum), the players with the most cards are the ones left a card or two short.

### Guessing

Players take turns around the table. The first player makes a **guess**: a card type and a quantity, e.g. *"Middle 4"*. This is a claim that **across everyone's hands combined there are at least 4 Middle cards**.

On your turn you must do one of three things:

1. **Guess**: make a higher guess than the previous one. A guess is higher if it has a **larger quantity**, or the **same quantity with a higher type** (Lower < Middle < Face).
   You can't guess more cards than there are in play (everyone's hands added together).
2. **Fiddle**: you think the previous guess is wrong. All hands are revealed and counted:
   - if there are **at least** that many, the guess was true, and **you lose 1 card**;
   - otherwise the guesser **loses 1 card**.
3. **Riddle**: you think the previous guess is *exactly* right. All hands are revealed and counted:
   - if the count is **exactly** the guessed number, **you gain 1 card** (up to a **maximum of 8**: if you already have 8, you stay on 8);
   - otherwise **you lose 2 cards**.

A fiddle or riddle ends the round. On the website a results screen shows every hand and who gained or lost cards; then cards are redealt and a new round begins.

### Who starts

The first round starts with the first player. After that, **whoever lost the last round starts the next one**:

- **Fiddle:** whoever lost a card (the fiddler if the guess was true, the guesser if it was false).
- **Riddle correct:** the guesser who got riddled.
- **Riddle wrong:** the riddler, who lost 2 cards.

If that player was just knocked out, the next player still in the game (going round the table) starts.

*(The desktop version predates this rule: there, the first player always starts.)*

### Winning

Players with no cards left are eliminated. The **last player with cards wins**.

## Plans

- [x] Restructure the game logic so it's driven by player actions rather than terminal input
- [x] Multiplayer web version (Python backend, browser frontend)
- [x] Game rooms / lobby codes so friends can play together online
- [x] Bots (Noob, Pro, Hacker)
- [x] Put it online (Render + fiddle.jackmarsters.com)
- [ ] Graphics and a violin-themed look 🎻
