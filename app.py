import nextcord
from nextcord.ext import commands, tasks
from dataclasses import dataclass
from typing import Optional
import random
import time
import os
from dotenv import load_dotenv
load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
COINDROP_ID = int(os.getenv("COINDROP_ID", "1422469144099426355"))
COINDROP = f"<@{COINDROP_ID}>"

intents = nextcord.Intents.default()
intents.guilds = True

bot = commands.Bot(intents=intents)

# Role mentions are only there for CoinDrop's parser -- never ping the role.
ALLOWED_MENTIONS = nextcord.AllowedMentions(everyone=False, roles=False, users=True)

# =========================
# Games
# =========================

# Every drop game CoinDrop's Discord bot supports, and which modifiers each
# one accepts (see CoinDrop/app.py's prefix commands).
GAMES = {
    "airdrop":    {"duration": True,  "winners": True},
    "raffle":     {"duration": True,  "winners": True},
    "mathtip":    {"duration": True,  "winners": True},
    "triviadrop": {"duration": True,  "winners": True},
    "coinflip":   {"duration": True,  "winners": False},
    "slowdrop":   {"duration": True,  "winners": False},
    "quickdrop":  {"duration": False, "winners": False},
}

# CoinDrop caps slowdrop at 5s and requires triviadrop to be at least 5s.
SLOWDROP_DURATION = "5s"
MIN_DURATION_SECONDS = 5

DURATION_UNITS = {"s": 1, "m": 60, "h": 3600}


def parse_duration(text: str) -> Optional[int]:
    text = text.strip().lower()
    if len(text) < 2 or text[-1] not in DURATION_UNITS or not text[:-1].isdigit():
        return None
    return int(text[:-1]) * DURATION_UNITS[text[-1]]


def parse_list(text: str, valid: Optional[list] = None) -> list[str]:
    items = [p.strip() for p in text.replace(" ", ",").split(",") if p.strip()]
    if valid is not None:
        items = [p.lower() for p in items]
        if "all" in items:
            return list(valid)
        items = [p for p in items if p in valid]
    return list(dict.fromkeys(items))


# =========================
# Models
# =========================

@dataclass
class Job:
    id: int
    guild_id: int
    channel_id: int

    games: list[str]
    currencies: list[str]

    min_amount: float
    max_amount: float

    min_interval_seconds: int
    max_interval_seconds: int

    # None = run forever
    remaining_runs: Optional[int]

    duration: str = "1m"
    winners: Optional[int] = None
    role_id: Optional[int] = None

    paused: bool = False
    next_run: float = 0
    runs_done: int = 0
    last_command: Optional[str] = None
    created_by: Optional[int] = None


jobs: dict[int, Job] = {}
job_counter = 1

# =========================
# Helpers
# =========================

def roll_interval(job: Job) -> int:
    return random.randint(job.min_interval_seconds, job.max_interval_seconds)


def roll_amount(job: Job) -> float:
    return round(random.uniform(job.min_amount, job.max_amount), 2)


def build_command(job: Job, game: str, amount: float, currency: str) -> str:
    rules = GAMES[game]
    parts = [COINDROP, game, f"${amount:.2f}", currency]

    if rules["duration"]:
        parts.append(SLOWDROP_DURATION if game == "slowdrop" else job.duration)

    if rules["winners"]:
        if job.winners:
            parts += ["for", str(job.winners)]
        elif game == "raffle":
            parts += ["for", "1"]

    if job.role_id:
        parts += ["role", f"<@&{job.role_id}>"]

    return " ".join(parts)


async def execute_job(channel, job: Job, game: Optional[str] = None):
    game = game or random.choice(job.games)
    currency = random.choice(job.currencies)
    amount = roll_amount(job)

    msg = build_command(job, game, amount, currency)
    await channel.send(msg, allowed_mentions=ALLOWED_MENTIONS)

    job.last_command = msg
    job.runs_done += 1
    if job.remaining_runs is not None:
        job.remaining_runs -= 1


def format_seconds(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s"
    minutes, seconds = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {seconds}s" if seconds else f"{minutes}m"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes}m" if minutes else f"{hours}h"


def describe_job(job: Job) -> str:
    runs = "∞" if job.remaining_runs is None else str(job.remaining_runs)
    status = "⏸️ paused" if job.paused else f"next in ~{format_seconds(job.next_run - time.time())}"
    interval = (
        format_seconds(job.min_interval_seconds)
        if job.min_interval_seconds == job.max_interval_seconds
        else f"{format_seconds(job.min_interval_seconds)}–{format_seconds(job.max_interval_seconds)}"
    )
    return (
        f"**#{job.id}** in <#{job.channel_id}> | {status} | {runs} runs left\n"
        f"  games: {', '.join(job.games)}\n"
        f"  ${job.min_amount:.2f}–${job.max_amount:.2f} {'/'.join(job.currencies)} "
        f"every {interval}"
    )


def has_manage_guild(interaction: nextcord.Interaction) -> bool:
    return interaction.user.guild_permissions.manage_guild


# =========================
# Scheduler
# =========================

@tasks.loop(seconds=5)
async def scheduler():

    now = time.time()

    remove_ids = []

    for job_id, job in list(jobs.items()):

        if job.paused or now < job.next_run:
            continue

        guild = bot.get_guild(job.guild_id)

        if guild is None:
            remove_ids.append(job_id)
            continue

        channel = guild.get_channel(job.channel_id)

        if channel is None:
            remove_ids.append(job_id)
            continue

        try:

            await execute_job(channel, job)

        except Exception as e:
            print(f"Scheduler error (job #{job_id}): {e}")

        if job.remaining_runs is not None and job.remaining_runs <= 0:
            remove_ids.append(job_id)
        else:
            job.next_run = now + roll_interval(job)

    for job_id in remove_ids:
        jobs.pop(job_id, None)


# =========================
# Events
# =========================

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (id {bot.user.id})")
    print(
        f"Make sure CoinDrop has whitelisted this bot: "
        f"@CoinDrop addbot {bot.user.id}"
    )

    if not scheduler.is_running():
        scheduler.start()


# =========================
# Slash Commands
# =========================

@bot.slash_command(
    description="Start random CoinDrop games in a channel"
)
async def randomdrops(
    interaction: nextcord.Interaction,

    min_amount: float = nextcord.SlashOption(
        description="Minimum USD per drop, e.g. 0.10"
    ),

    max_amount: float = nextcord.SlashOption(
        description="Maximum USD per drop, e.g. 1.00"
    ),

    min_interval_minutes: int = nextcord.SlashOption(
        description="Minimum minutes between drops"
    ),

    max_interval_minutes: int = nextcord.SlashOption(
        description="Maximum minutes between drops"
    ),

    currency: str = nextcord.SlashOption(
        required=False,
        default="BNB",
        description="Currency, or several to pick from at random, e.g. BNB,SOL,BAN"
    ),

    games: str = nextcord.SlashOption(
        required=False,
        default="all",
        description=f"all, or a list from: {', '.join(GAMES)}"
    ),

    runs: int = nextcord.SlashOption(
        required=False,
        default=0,
        description="Number of drops before stopping (0 = run until stopped)"
    ),

    duration: str = nextcord.SlashOption(
        required=False,
        default="1m",
        description="How long each game stays open, e.g. 30s, 1m, 5m (slowdrop always uses 5s)"
    ),

    winners: int = nextcord.SlashOption(
        required=False,
        description="Cap winners for airdrop/raffle/mathtip/triviadrop"
    ),

    role: nextcord.Role = nextcord.SlashOption(
        required=False,
        description="Only members with this role can join"
    ),

    channel: nextcord.TextChannel = nextcord.SlashOption(
        required=False,
        description="Channel to drop in (defaults to this one)"
    ),

    start_now: bool = nextcord.SlashOption(
        required=False,
        default=True,
        description="Fire the first drop immediately"
    ),
):

    global job_counter

    if not has_manage_guild(interaction):
        return await interaction.send(
            "Manage Server permission required.",
            ephemeral=True
        )

    game_list = parse_list(games, list(GAMES))
    if not game_list:
        return await interaction.send(
            f"No valid games. Pick from: {', '.join(GAMES)} (or `all`).",
            ephemeral=True
        )

    currency_list = [c.upper() for c in parse_list(currency)]
    if not currency_list:
        return await interaction.send(
            "At least one currency required.",
            ephemeral=True
        )

    if min_amount <= 0 or max_amount < min_amount:
        return await interaction.send(
            "Amounts must be positive and max ≥ min.",
            ephemeral=True
        )

    if min_interval_minutes < 1 or max_interval_minutes < min_interval_minutes:
        return await interaction.send(
            "Intervals must be at least 1 minute and max ≥ min.",
            ephemeral=True
        )

    duration_seconds = parse_duration(duration)
    if duration_seconds is None or duration_seconds < MIN_DURATION_SECONDS:
        return await interaction.send(
            "Duration must look like `30s`, `1m` or `1h`, and be at least 5s.",
            ephemeral=True
        )

    if winners is not None and winners < 1:
        return await interaction.send(
            "Winners must be at least 1.",
            ephemeral=True
        )

    if runs < 0:
        return await interaction.send(
            "Runs can't be negative.",
            ephemeral=True
        )

    target = channel or interaction.channel

    job = Job(
        id=job_counter,
        guild_id=interaction.guild.id,
        channel_id=target.id,
        games=game_list,
        currencies=currency_list,
        min_amount=min_amount,
        max_amount=max_amount,
        min_interval_seconds=min_interval_minutes * 60,
        max_interval_seconds=max_interval_minutes * 60,
        remaining_runs=runs or None,
        duration=duration.strip().lower(),
        winners=winners,
        role_id=role.id if role else None,
        created_by=interaction.user.id,
    )
    job.next_run = time.time() + roll_interval(job)

    job_counter += 1
    jobs[job.id] = job

    # Respond first so the CoinDrop command doesn't land before the ack.
    await interaction.send(
        f"Created random drop job #{job.id}\n{describe_job(job)}"
    )

    if start_now:
        try:
            await execute_job(target, job)
        except Exception as e:
            print(e)
            await interaction.followup.send(
                f"Couldn't post in {target.mention}: {e}",
                ephemeral=True
            )

        if job.remaining_runs is not None and job.remaining_runs <= 0:
            jobs.pop(job.id, None)


@bot.slash_command(
    description="Fire one random CoinDrop game right now"
)
async def dropnow(
    interaction: nextcord.Interaction,

    amount: float = nextcord.SlashOption(
        description="USD amount, e.g. 0.50"
    ),

    currency: str = nextcord.SlashOption(
        required=False,
        default="BNB"
    ),

    game: str = nextcord.SlashOption(
        required=False,
        default="random",
        choices={"random": "random", **{g: g for g in GAMES}}
    ),

    duration: str = nextcord.SlashOption(
        required=False,
        default="1m"
    ),

    winners: int = nextcord.SlashOption(
        required=False
    ),
):

    if not has_manage_guild(interaction):
        return await interaction.send(
            "Manage Server permission required.",
            ephemeral=True
        )

    duration_seconds = parse_duration(duration)
    if duration_seconds is None or duration_seconds < MIN_DURATION_SECONDS:
        return await interaction.send(
            "Duration must look like `30s`, `1m` or `1h`, and be at least 5s.",
            ephemeral=True
        )

    if amount <= 0:
        return await interaction.send(
            "Amount must be positive.",
            ephemeral=True
        )

    # Throwaway job so one-off drops share the scheduler's command builder.
    job = Job(
        id=0,
        guild_id=interaction.guild.id,
        channel_id=interaction.channel.id,
        games=list(GAMES),
        currencies=[currency.upper()],
        min_amount=amount,
        max_amount=amount,
        min_interval_seconds=0,
        max_interval_seconds=0,
        remaining_runs=1,
        duration=duration.strip().lower(),
        winners=winners,
    )

    picked = random.choice(job.games) if game == "random" else game

    await interaction.send(f"Dropping a **{picked}**!")
    await execute_job(interaction.channel, job, picked)


@bot.slash_command(
    description="View active random drop jobs"
)
async def jobs_list(interaction: nextcord.Interaction):

    guild_jobs = [
        job
        for job in jobs.values()
        if job.guild_id == interaction.guild.id
    ]

    if not guild_jobs:
        return await interaction.send(
            "No active jobs."
        )

    await interaction.send(
        "\n\n".join(describe_job(job) for job in guild_jobs)
    )


def get_guild_job(interaction: nextcord.Interaction, job_id: int) -> Optional[Job]:
    job = jobs.get(job_id)
    if job is None or job.guild_id != interaction.guild.id:
        return None
    return job


@bot.slash_command(
    description="Delete a job"
)
async def delete_job(
    interaction: nextcord.Interaction,
    job_id: int
):

    if not has_manage_guild(interaction):
        return await interaction.send(
            "Manage Server permission required.",
            ephemeral=True
        )

    if get_guild_job(interaction, job_id) is None:
        return await interaction.send(
            "Job not found.",
            ephemeral=True
        )

    del jobs[job_id]

    await interaction.send(
        f"Deleted job #{job_id}"
    )


@bot.slash_command(
    description="Pause or resume a job"
)
async def pause_job(
    interaction: nextcord.Interaction,
    job_id: int
):

    if not has_manage_guild(interaction):
        return await interaction.send(
            "Manage Server permission required.",
            ephemeral=True
        )

    job = get_guild_job(interaction, job_id)
    if job is None:
        return await interaction.send(
            "Job not found.",
            ephemeral=True
        )

    job.paused = not job.paused
    if not job.paused:
        job.next_run = time.time() + roll_interval(job)

    await interaction.send(
        f"{'Paused' if job.paused else 'Resumed'} job #{job_id}"
    )


@bot.slash_command(
    description="Get the bot's CoinDrop balance"
)
async def balance(
    interaction: nextcord.Interaction,

    coin: str = nextcord.SlashOption(
        required=False
    ),
):

    if not interaction.user.guild_permissions.administrator:
        return await interaction.send(
            "Administrator permission required.",
            ephemeral=True
        )

    await interaction.send("Asking CoinDrop…", ephemeral=True)

    if coin:
        await interaction.channel.send(
            f"{COINDROP} bal {coin}"
        )
    else:
        await interaction.channel.send(
            f"{COINDROP} bals top"
        )


if __name__ == "__main__":
    bot.run(TOKEN)
