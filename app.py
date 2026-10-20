import nextcord
from nextcord.ext import commands, tasks
from dataclasses import dataclass
from typing import Optional
import functools
import math
import random
import time
import os
from dotenv import load_dotenv
load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
COINDROP_ID = int(os.getenv("COINDROP_ID", "1422469144099426355"))
COINDROP = f"<@{COINDROP_ID}>"

# The only Discord user allowed to run any of this bot's commands.
OWNER_ID = 464445762986704918

intents = nextcord.Intents.default()
intents.guilds = True

# Slash commands are registered per server so changes show up instantly;
# global commands can take a long time to reach clients. Set GUILD_IDS to
# a comma-separated list to use other servers, or to an empty value to
# register globally instead.
GUILD_IDS = [
    int(g) for g in os.getenv("GUILD_IDS", "1422459919482884156").split(",") if g.strip()
]

bot = commands.Bot(intents=intents, default_guild_ids=GUILD_IDS or None)

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

# Amounts are USD; CoinDrop accepts sub-cent values like $0.0025.
AMOUNT_DECIMALS = 4
# Mirrors CoinDrop's config.MIN_TIP_AMOUNT -- smaller drops get rejected.
MIN_DROP_USD = 0.001

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

    # Budget jobs (/budgetdrops): a fixed USD total spread over the job's runs.
    total_budget: Optional[float] = None
    budget_remaining: Optional[float] = None
    # How far each drop may stray from the even share, 0.5 = ±50%.
    variety: float = 0

    paused: bool = False
    next_run: float = 0
    runs_done: int = 0
    last_command: Optional[str] = None
    created_by: Optional[int] = None


# guild_id -> {job_id -> Job}. Each server has its own jobs and its own
# job numbering, so nothing about one server's jobs leaks into another.
jobs: dict[int, dict[int, Job]] = {}
job_counters: dict[int, int] = {}


def guild_jobs(guild_id: int) -> dict[int, Job]:
    return jobs.setdefault(guild_id, {})


def next_job_id(guild_id: int) -> int:
    job_counters[guild_id] = job_counters.get(guild_id, 0) + 1
    return job_counters[guild_id]


def remove_job(guild_id: int, job_id: int):
    gjobs = jobs.get(guild_id)
    if gjobs is None:
        return
    gjobs.pop(job_id, None)
    if not gjobs:
        jobs.pop(guild_id, None)

# =========================
# Helpers
# =========================

def roll_interval(job: Job) -> int:
    return random.randint(job.min_interval_seconds, job.max_interval_seconds)


def roll_amount(job: Job) -> float:
    if job.budget_remaining is not None:
        return roll_budget_amount(job)
    return round(random.uniform(job.min_amount, job.max_amount), AMOUNT_DECIMALS)


def floor_usd(amount: float) -> float:
    factor = 10 ** AMOUNT_DECIMALS
    # Small epsilon so float noise (0.3 -> 0.29999...) doesn't lose a unit.
    return math.floor(amount * factor + 1e-6) / factor


def roll_budget_amount(job: Job) -> float:
    remaining = job.budget_remaining
    runs_left = job.remaining_runs

    # Last drop takes whatever is left, so the budget is spent exactly.
    if runs_left <= 1:
        return round(remaining, AMOUNT_DECIMALS)

    share = remaining / runs_left
    # Always leave enough for every later drop to meet CoinDrop's minimum.
    ceiling = remaining - MIN_DROP_USD * (runs_left - 1)
    low = max(MIN_DROP_USD, share * (1 - job.variety))
    high = min(ceiling, share * (1 + job.variety))

    amount = random.uniform(low, high) if high > low else share
    return max(MIN_DROP_USD, min(floor_usd(amount), floor_usd(ceiling)))


def format_usd(amount: float) -> str:
    # Up to 4 decimals, trailing zeros trimmed but never below cents: 0.5 -> 0.50, 0.0025 -> 0.0025
    text = f"{amount:.{AMOUNT_DECIMALS}f}".rstrip("0")
    whole, _, frac = text.partition(".")
    return f"${whole}.{frac.ljust(2, '0')}"


def build_command(job: Job, game: str, amount: float, currency: str) -> str:
    rules = GAMES[game]
    parts = [COINDROP, game, format_usd(amount), currency]

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
    if job.budget_remaining is not None:
        job.budget_remaining = round(job.budget_remaining - amount, AMOUNT_DECIMALS)
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
    if hours < 24:
        return f"{hours}h {minutes}m" if minutes else f"{hours}h"
    days, hours = divmod(hours, 24)
    return f"{days}d {hours}h" if hours else f"{days}d"


def describe_job(job: Job) -> str:
    runs = "∞" if job.remaining_runs is None else str(job.remaining_runs)
    status = "⏸️ paused" if job.paused else f"next in ~{format_seconds(job.next_run - time.time())}"
    interval = (
        format_seconds(job.min_interval_seconds)
        if job.min_interval_seconds == job.max_interval_seconds
        else f"{format_seconds(job.min_interval_seconds)}–{format_seconds(job.max_interval_seconds)}"
    )
    header = (
        f"**#{job.id}** in <#{job.channel_id}> | {status} | {runs} runs left\n"
        f"  games: {', '.join(job.games)}\n"
    )

    if job.budget_remaining is not None:
        ends = job.next_run + max(0, job.remaining_runs - 1) * job.max_interval_seconds
        share = job.budget_remaining / job.remaining_runs if job.remaining_runs else 0
        return header + (
            f"  budget: {format_usd(job.budget_remaining)} of {format_usd(job.total_budget)} left "
            f"({'/'.join(job.currencies)}), ~{format_usd(share)} per drop every {interval}\n"
            f"  ends in ~{format_seconds(ends - time.time())}"
        )

    return header + (
        f"  {format_usd(job.min_amount)}–{format_usd(job.max_amount)} {'/'.join(job.currencies)} "
        f"every {interval}"
    )


def owner_only(func):
    # Every command is owner-only and per-server. The server check runs here
    # rather than via slash_command(dm_permission=False), which not every
    # nextcord version has.
    @functools.wraps(func)
    async def wrapper(interaction: nextcord.Interaction, *args, **kwargs):
        if interaction.user.id != OWNER_ID:
            return await interaction.send(
                "You're not allowed to use this bot.",
                ephemeral=True
            )
        if interaction.guild is None:
            return await interaction.send(
                "This command only works in a server.",
                ephemeral=True
            )
        return await func(interaction, *args, **kwargs)
    return wrapper


async def check_drop_options(interaction, games, currency, duration, winners, channel):
    """Validate the options /randomdrops and /budgetdrops share.

    Sends the error and returns None if something is wrong, otherwise
    (game_list, currency_list, target_channel)."""

    async def fail(msg):
        await interaction.send(msg, ephemeral=True)

    game_list = parse_list(games, list(GAMES))
    if not game_list:
        return await fail(f"No valid games. Pick from: {', '.join(GAMES)} (or `all`).")

    currency_list = [c.upper() for c in parse_list(currency)]
    if not currency_list:
        return await fail("At least one currency required.")

    duration_seconds = parse_duration(duration)
    if duration_seconds is None or duration_seconds < MIN_DURATION_SECONDS:
        return await fail("Duration must look like `30s`, `1m` or `1h`, and be at least 5s.")

    if winners is not None and winners < 1:
        return await fail("Winners must be at least 1.")

    target = channel or interaction.channel

    if target.guild.id != interaction.guild.id:
        return await fail("Channel must be in this server.")

    return game_list, currency_list, target


async def start_job(interaction, job: Job, target, start_now: bool, label: str):
    guild_jobs(job.guild_id)[job.id] = job

    # Respond first so the CoinDrop command doesn't land before the ack.
    await interaction.send(
        f"Created {label} job #{job.id}\n{describe_job(job)}"
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
            remove_job(job.guild_id, job.id)


# =========================
# Scheduler
# =========================

@tasks.loop(seconds=5)
async def scheduler():

    now = time.time()

    remove_ids = []

    for guild_id, gjobs in list(jobs.items()):

        guild = bot.get_guild(guild_id)

        # Temporarily unavailable (outage) -- skip; on_guild_remove handles
        # the bot actually leaving.
        if guild is None:
            continue

        for job_id, job in list(gjobs.items()):

            if job.paused or now < job.next_run:
                continue

            channel = guild.get_channel(job.channel_id)

            # Only ever post in a channel that belongs to the job's own server.
            if channel is None or channel.guild.id != guild_id:
                remove_ids.append((guild_id, job_id))
                continue

            try:

                await execute_job(channel, job)

            except Exception as e:
                print(f"Scheduler error (guild {guild_id}, job #{job_id}): {e}")

            if job.remaining_runs is not None and job.remaining_runs <= 0:
                remove_ids.append((guild_id, job_id))
            else:
                job.next_run = now + roll_interval(job)

    for guild_id, job_id in remove_ids:
        remove_job(guild_id, job_id)


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


@bot.event
async def on_guild_remove(guild):
    jobs.pop(guild.id, None)
    job_counters.pop(guild.id, None)


# =========================
# Slash Commands
# =========================

@bot.slash_command(
    description="Start random CoinDrop games in a channel"
)
@owner_only
async def randomdrops(
    interaction: nextcord.Interaction,

    min_amount: float = nextcord.SlashOption(
        description="Minimum USD per drop, e.g. 0.0025 (up to 4 decimals, min 0.001)"
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

    checked = await check_drop_options(interaction, games, currency, duration, winners, channel)
    if checked is None:
        return
    game_list, currency_list, target = checked

    min_amount = round(min_amount, AMOUNT_DECIMALS)
    max_amount = round(max_amount, AMOUNT_DECIMALS)

    if min_amount < MIN_DROP_USD or max_amount < min_amount:
        return await interaction.send(
            f"Amounts must be at least {format_usd(MIN_DROP_USD)} and max ≥ min.",
            ephemeral=True
        )

    if min_interval_minutes < 1 or max_interval_minutes < min_interval_minutes:
        return await interaction.send(
            "Intervals must be at least 1 minute and max ≥ min.",
            ephemeral=True
        )

    if runs < 0:
        return await interaction.send(
            "Runs can't be negative.",
            ephemeral=True
        )

    job = Job(
        id=next_job_id(interaction.guild.id),
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

    await start_job(interaction, job, target, start_now, "random drop")


@bot.slash_command(
    description="Spend a fixed budget on random CoinDrop games over a number of days"
)
@owner_only
async def budgetdrops(
    interaction: nextcord.Interaction,

    days: float = nextcord.SlashOption(
        description="How many days to run for, e.g. 7 or 0.5"
    ),

    interval_minutes: int = nextcord.SlashOption(
        description="Minutes between drops"
    ),

    total: float = nextcord.SlashOption(
        description="Total USD to spend across every drop, e.g. 5"
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

    variety: int = nextcord.SlashOption(
        required=False,
        default=50,
        description="How much each drop may vary from an even share, in % (0 = all equal)"
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

    checked = await check_drop_options(interaction, games, currency, duration, winners, channel)
    if checked is None:
        return
    game_list, currency_list, target = checked

    if days <= 0:
        return await interaction.send(
            "Days must be more than 0.",
            ephemeral=True
        )

    if interval_minutes < 1:
        return await interaction.send(
            "Interval must be at least 1 minute.",
            ephemeral=True
        )

    if not 0 <= variety <= 100:
        return await interaction.send(
            "Variety must be between 0 and 100.",
            ephemeral=True
        )

    runs = int(days * 24 * 60 // interval_minutes)
    if runs < 1:
        return await interaction.send(
            f"{days:g} days is shorter than one {interval_minutes}-minute interval.",
            ephemeral=True
        )

    total = round(total, AMOUNT_DECIMALS)
    if total < MIN_DROP_USD * runs:
        return await interaction.send(
            f"{format_usd(total)} over {runs} drops is under CoinDrop's "
            f"{format_usd(MIN_DROP_USD)} minimum per drop. Raise the total to at least "
            f"{format_usd(MIN_DROP_USD * runs)}, or use a longer interval.",
            ephemeral=True
        )

    job = Job(
        id=next_job_id(interaction.guild.id),
        guild_id=interaction.guild.id,
        channel_id=target.id,
        games=game_list,
        currencies=currency_list,
        min_amount=MIN_DROP_USD,
        max_amount=total,
        min_interval_seconds=interval_minutes * 60,
        max_interval_seconds=interval_minutes * 60,
        remaining_runs=runs,
        duration=duration.strip().lower(),
        winners=winners,
        role_id=role.id if role else None,
        created_by=interaction.user.id,
        total_budget=total,
        budget_remaining=total,
        variety=variety / 100,
    )
    job.next_run = time.time() + roll_interval(job)

    await start_job(interaction, job, target, start_now, "budget drop")


@bot.slash_command(
    description="Fire one random CoinDrop game right now"
)
@owner_only
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

    duration_seconds = parse_duration(duration)
    if duration_seconds is None or duration_seconds < MIN_DURATION_SECONDS:
        return await interaction.send(
            "Duration must look like `30s`, `1m` or `1h`, and be at least 5s.",
            ephemeral=True
        )

    amount = round(amount, AMOUNT_DECIMALS)
    if amount < MIN_DROP_USD:
        return await interaction.send(
            f"Amount must be at least {format_usd(MIN_DROP_USD)}.",
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
@owner_only
async def jobs_list(interaction: nextcord.Interaction):

    gjobs = list(jobs.get(interaction.guild.id, {}).values())

    if not gjobs:
        return await interaction.send(
            "No active jobs."
        )

    await interaction.send(
        "\n\n".join(describe_job(job) for job in gjobs)
    )


def get_guild_job(interaction: nextcord.Interaction, job_id: int) -> Optional[Job]:
    return jobs.get(interaction.guild.id, {}).get(job_id)


@bot.slash_command(
    description="Delete a job"
)
@owner_only
async def delete_job(
    interaction: nextcord.Interaction,
    job_id: int
):

    if get_guild_job(interaction, job_id) is None:
        return await interaction.send(
            "Job not found.",
            ephemeral=True
        )

    remove_job(interaction.guild.id, job_id)

    await interaction.send(
        f"Deleted job #{job_id}"
    )


@bot.slash_command(
    description="Pause or resume a job"
)
@owner_only
async def pause_job(
    interaction: nextcord.Interaction,
    job_id: int
):

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
@owner_only
async def balance(
    interaction: nextcord.Interaction,

    coin: str = nextcord.SlashOption(
        required=False
    ),
):

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
