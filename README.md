# Dropalot

A Discord bot that runs random CoinDrop games in a channel at random intervals. It works like AutoDrop: it posts `@CoinDrop <game> ...` commands, and CoinDrop runs the game, paying for it from **this bot's own CoinDrop balance**. All state is in memory, so jobs are lost when the bot restarts.

Games it can pick from: `airdrop`, `raffle`, `mathtip`, `triviadrop`, `coinflip`, `slowdrop`, `quickdrop`.

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
# put your token in .env: DISCORD_TOKEN=...
.venv/bin/python app.py
```

Before it will work:

1. **Whitelist the bot in CoinDrop.** CoinDrop ignores messages from bots that aren't whitelisted. The CoinDrop owner runs `@CoinDrop addbot <dropalot bot id>`; the bot prints its id at startup.
2. **Fund it.** Deposit to the bot's CoinDrop wallet, or tip it.
3. Invite it with the `applications.commands` scope and the Send Messages permission.

## Commands

| Command | Who | What it does |
|---|---|---|
| `/randomdrops` | Manage Server | Starts a job that picks a random game, amount (USD, between min and max), currency, and delay (between min and max minutes) for each drop |
| `/dropnow` | Manage Server | Runs one game now (random, or a game you choose) |
| `/jobs_list` | anyone | Lists this server's jobs |
| `/pause_job <id>` | Manage Server | Pauses or resumes a job |
| `/delete_job <id>` | Manage Server | Stops a job |
| `/balance [coin]` | Administrator | Asks CoinDrop for the bot's balance |

`/randomdrops` options: `games` (`all` or a list such as `airdrop,mathtip,coinflip`), `currency` (one currency, or a list to pick from at random), `runs` (0 means run until stopped), `duration`, `winners`, `role`, `channel`, `start_now`.

CoinDrop's own limits are built in: slowdrop always runs for 5s, quickdrop has no duration, `winners` only applies to airdrop/raffle/mathtip/triviadrop, and a raffle with no `winners` set defaults to `for 1`.
