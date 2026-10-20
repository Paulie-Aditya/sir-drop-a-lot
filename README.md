# Dropalot

A Discord bot that runs random CoinDrop games in a channel at random intervals. It works like AutoDrop: it posts `@CoinDrop <game> ...` commands, and CoinDrop runs the game, paying for it from **this bot's own CoinDrop balance**. All state is in memory, so jobs are lost when the bot restarts. Jobs belong to the server they were created in: each server has its own job numbers, only sees its own jobs, and a job only ever posts in its own server. Commands don't work in DMs.

Games it can pick from: `airdrop`, `raffle`, `mathtip`, `triviadrop`, `coinflip`, `slowdrop`, `quickdrop`.

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env   # then fill in DISCORD_TOKEN
.venv/bin/python app.py
```

Before it will work:

1. **Whitelist the bot in CoinDrop.** CoinDrop ignores messages from bots that aren't whitelisted. The CoinDrop owner runs `@CoinDrop addbot <dropalot bot id>`; the bot prints its id at startup.
2. **Fund it.** Deposit to the bot's CoinDrop wallet, or tip it.
3. Invite it with the `applications.commands` scope and the Send Messages permission.

## Commands

Every command can only be run by Discord user `464445762986704918` (`OWNER_ID` in `app.py`). Anyone else gets a private "not allowed" reply.

| Command | What it does |
|---|---|
| `/randomdrops` | Starts a job that picks a random game, amount (USD, up to 4 decimals, min $0.001), currency, and delay (between min and max minutes) for each drop |
| `/budgetdrops` | Runs for *x* days, one drop every *y* minutes, spending *z* USD in total. Same random games and currencies as `/randomdrops` |
| `/dropnow` | Runs one game now (random, or a game you choose) |
| `/jobs_list` | Lists this server's jobs |
| `/pause_job <id>` | Pauses or resumes a job |
| `/delete_job <id>` | Stops a job |
| `/balance [coin]` | Asks CoinDrop for the bot's balance |

`/randomdrops` options: `games` (`all` or a list such as `airdrop,mathtip,coinflip`), `currency` (one currency, or a list to pick from at random), `runs` (0 means run until stopped), `duration`, `winners`, `role`, `channel`, `start_now`.

`/budgetdrops` options: `days`, `interval_minutes`, `total`, plus `variety` (how much each drop may differ from an even share, 0–100%, default 50) and the same `games`, `currency`, `duration`, `winners`, `role`, `channel`, `start_now` as `/randomdrops`. The number of drops is `days × 1440 ÷ interval_minutes`. Amounts are random but the total is always spent exactly, the last drop takes whatever is left, and no drop goes below CoinDrop's $0.001 minimum. Example: `days:7 interval_minutes:60 total:5` gives 168 drops averaging about $0.03 each.

CoinDrop's own limits are built in: slowdrop always runs for 5s, quickdrop has no duration, `winners` only applies to airdrop/raffle/mathtip/triviadrop, and a raffle with no `winners` set defaults to `for 1`.

## Deploying (EC2 / Ubuntu)

```bash
cd ~ && git clone https://github.com/Paulie-Aditya/sir-drop-a-lot.git && cd sir-drop-a-lot
python3 -m venv venv && venv/bin/pip install -r requirements.txt
cp .env.example .env && nano .env      # set DISCORD_TOKEN
sudo cp deploy/dropalot.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now dropalot
journalctl -u dropalot -f              # watch logs
```

To update: `git pull && venv/bin/pip install -r requirements.txt && sudo systemctl restart dropalot`.
Jobs live in memory, so a restart clears them.
