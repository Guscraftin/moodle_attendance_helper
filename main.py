import os
import json
import time

import httpx, discord, aiosqlite
from numpy.random import MT19937, RandomState
from dotenv import load_dotenv

import constants

load_dotenv()


def pins2aggpins(pins):
    return ((pins[0] - 1000) * 9001 + pins[1] - 1000) * 9001 + pins[2] - 1000


def seed2aggpins(seed):
    rs = RandomState(seed)
    mt = MT19937()
    mt.state = rs.get_state()

    mt.random_raw()
    mt.random_raw()

    return pins2aggpins([mt.random_raw() % 9001 + 1000 for _ in range(3)])


def seed2pins_iter(seed):
    rs = RandomState(seed)
    mt = MT19937()
    mt.state = rs.get_state()

    mt.random_raw()
    mt.random_raw()

    for _ in range(30):
        yield mt.random_raw() % 9001 + 1000


def seed2pins(seed):
    return list(seed2pins_iter(seed))


async def get_seed_lookup_data(ctx, pin0):
    local_lookup_folder = os.environ.get("LOCAL_LOOKUP_FOLDER")
    if local_lookup_folder is not None:
        filename = os.path.join(local_lookup_folder, str(pin0 // 1000), str(pin0))
        file = open(filename, "rb")
        data = file.read()
    else:
        await ctx.defer()

        cid = constants.IPFS_CIDS[pin0 // 1000 - 1]
        if pin0 == 10000:
            url = f"https://{cid}.ipfs.nftstorage.link/"
        else:
            url = f"https://{cid}.ipfs.nftstorage.link/{pin0}"

        async with httpx.AsyncClient() as client:
            resp = await client.get(url, timeout=120)
            data = await resp.aread()

    return data


bot = discord.Bot()

channel_ids = set(os.environ.get("DISCORD_CHANNELS_WHITELIST").split(","))

db = None


@bot.event
async def on_ready():
    global db

    db_filename = os.environ.get("SQLITE_DB_FILE", "db.sqlite3")
    db = await aiosqlite.connect(db_filename)
    await db.executescript(
        """
        CREATE TABLE IF NOT EXISTS past_inputs(
            aggpins INTEGER PRIMARY KEY,
            seeds TEXT DEFAULT "[]",
            author_id INTEGER,
            datetime REAL DEFAULT (unixepoch('now'))
        );

        CREATE TABLE IF NOT EXISTS leaderboard(
            author_id INTEGER PRIMARY KEY,
            author_name,
            weekdate INTEGER DEFAULT
                (unixepoch('now', 'start of day', 'weekday 0')),
            score INTEGER
        );

        CREATE INDEX IF NOT EXISTS
            index_past_inputs_datetime on past_inputs(datetime);

        CREATE INDEX IF NOT EXISTS
            index_leaderboard_weekdate on leaderboard(weekdate);
        CREATE INDEX IF NOT EXISTS
            index_leaderboard_score on leaderboard(score);

        CREATE TRIGGER IF NOT EXISTS reset_leaderboard_every_week
            BEFORE INSERT ON leaderboard
            BEGIN
                DELETE FROM leaderboard
                WHERE weekdate < unixepoch('now', 'start of day', 'weekday 0');
            END;
        """
    )

    print(f"{bot.user} is ready and online!")


@bot.slash_command(description="Gimme the first three pins")
@discord.guild_only()
async def moodle_pins(
    ctx,
    pin0: discord.Option(int, "The first pin", min_value=1000, max_value=10000),
    pin1: discord.Option(int, "The second pin", min_value=1000, max_value=10000),
    pin2: discord.Option(int, "The third pin", min_value=1000, max_value=10000)
):
    if str(ctx.channel_id) not in channel_ids:
        return

    pins = [pin0, pin1, pin2]
    print(ctx.interaction.id, ctx.author.id, ctx.author.name, "requested", pins)

    target_aggpins = pins2aggpins(pins)

    query_past_inputs = "SELECT count(*) FROM past_inputs WHERE aggpins = ?"
    async with db.execute(query_past_inputs, [target_aggpins]) as cursor:
        async for row in cursor:
            if row[0] > 0:
                await ctx.respond("Too late o(> < )o", ephemeral=True)
                return
            break

    data = await get_seed_lookup_data(ctx, pin0)

    left = 0
    right = len(data) // 4

    tries = 0

    while right - left > 16:
        tries += 1

        mid = left + (right - left) // 2

        seed_bytes = data[mid * 4 : mid * 4 + 4]
        seed = int.from_bytes(seed_bytes, "big")

        aggpins = seed2aggpins(seed)

        if aggpins < target_aggpins:
            left = mid
        elif aggpins > target_aggpins:
            right = mid
        else:
            left = mid - 8
            right = mid + 8

    seeds = []
    for i in range(left, right):
        seed_bytes = data[i * 4 : i * 4 + 4]
        seed = int.from_bytes(seed_bytes, "big")
        if seed2aggpins(seed) == target_aggpins:
            seeds.append(seed)

    print(f"{ctx.interaction.id} {ctx.author.id} {ctx.author.name} got seeds {seeds}")

    if len(seeds) == 0:
        await ctx.respond("Wrong pins (╬ Ò﹏Ó)", ephemeral=True)
        return

    await db.execute(
        """
        INSERT INTO past_inputs(aggpins, seeds, author_id)
        VALUES(?, ?, ?)
        """,
        [target_aggpins, json.dumps(seeds), ctx.author.id],
    )
    await db.execute(
        """
        INSERT INTO leaderboard(author_id, author_name, score)
        VALUES(?, ?, ?)
        ON CONFLICT(author_id)
        DO UPDATE SET score = score + ?, author_name = ?
        """,
        [ctx.author.id, ctx.author.display_name, 1, 1, ctx.author.display_name],
    )
    await db.commit()

    current_leaderboard = ""
    query_leaderboard = (
        "SELECT author_name, score FROM leaderboard ORDER BY score DESC LIMIT 3"
    )
    async with db.execute(query_leaderboard) as cursor:
        async for row in cursor:
            current_leaderboard += constants.LEADERBOARD_LINE % (row[0], row[1])

    ########################################
    # Get time when pins were expired
    # TODO: Need the date with hour in Timestamp (I don't know if your a exactly the expired session with seconds)
    
    # For convert date to timestamp
    #TODO: Change the "" by the date
    import time
    expired_time = int(time.mktime(time.strptime("THE DATE LIKE -> 2023-03-19 14:06:45"))) - time.timezone
    ########################################

    if len(seeds) == 1:
        pinslist = seed2pins(seeds[0])
        next_three_pins = pinslist[2:5]
        next_three_pins_str = ", ".join(str(pin) for pin in next_three_pins)

        await ctx.respond(constants.MAIN_PINS_ANNOUNCE % ("The next three pins are: {next_three_pins_str}", expired_time, current_leaderboard)
        )
    else:
        next_pins_str = ""
        for seed in seeds:
            pinslist = seed2pins(seed)
            next_three_pins = pinslist[2:5]
            next_three_pins_str = ", ".join(str(pin) for pin in next_three_pins)

            next_pins_str += f"- {next_three_pins_str}\n"

        await ctx.respond(constants.MAIN_PINS_ANNOUNCE % ("The next three pins are one of these:\n{next_pins_str}", expired_time, current_leaderboard)
        )


@bot.slash_command(description="Oh, you're late? Just ask me the pins!")
@discord.guild_only()
async def moodle_late(ctx):
    if str(ctx.channel_id) not in channel_ids:
        return

    print(ctx.interaction.id, ctx.author.id, ctx.author.name, "requested late pins")

    current_seeds = []
    query_recent_seeds = """
    SELECT seeds, datetime
    FROM past_inputs
    WHERE datetime > unixepoch('now', '-1120 seconds')
    ORDER BY datetime DESC;
    """
    async with db.execute(query_recent_seeds) as cursor:
        async for row in cursor:
            for seed in json.loads(row[0]):
                current_seeds.append((seed, float(row[1])))

    if len(current_seeds) == 0:
        await ctx.respond("You're either too late or too early mate :/", ephemeral=True)
    elif len(current_seeds) == 1:
        pinslist = seed2pins(current_seeds[0][0])
        i = (int(time.time()) - int(current_seeds[0][1])) // 40 + 2
        next_three_pins = pinslist[i : i + 3]

        await ctx.respond(
            constants.PIN_ANNOUNCE_LINE % (next_three_pins[0], next_three_pins[1:]),
            ephemeral=True,
        )
    else:
        first_pins = []
        next_pins = []
        for (seed, timestamp) in current_seeds:
            pinslist = seed2pins(seed)
            i = (int(time.time()) - int(timestamp)) // 40 + 2
            next_three_pins = pinslist[i : i + 2]
            first_pins.append(next_three_pins[0])
            next_pins += next_three_pins[1:]

        await ctx.respond(
            constants.UNSURE_PINS_ANNOUNCE % (first_pins, next_pins), ephemeral=True
        )


bot.run(os.environ.get("DISCORD_TOKEN"))
