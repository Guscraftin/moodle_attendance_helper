import os

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


def seed2finalpin(seed):
    rs = RandomState(seed)
    mt = MT19937()
    mt.state = rs.get_state()

    for _ in range(31):
        mt.random_raw()

    return mt.random_raw() % 9001 + 1000


async def get_seed_lookup_data(pin0):
    local_lookup_folder = os.environ.get("LOCAL_LOOKUP_FOLDER")
    if local_lookup_folder is not None:
        filename = os.path.join(local_lookup_folder, str(pin0 // 1000), str(pin0))
        file = open(filename, "rb")
        data = file.read()
    else:
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
async def on_connect():
    global db

    db_filename = os.environ.get("SQLITE_DB_FILE", "db.sqlite3")
    db = await aiosqlite.connect(db_filename)
    await db.executescript(
        """
        CREATE TABLE IF NOT EXISTS past_inputs(
            aggpins INTEGER PRIMARY KEY,
            seed INTEGER DEFAULT NULL,
            author_id INTEGER,
            date REAL DEFAULT (unixepoch('now'))
        );

        CREATE TABLE IF NOT EXISTS leaderboard(
            author_id INTEGER PRIMARY KEY,
            author_name,
            weekdate INTEGER DEFAULT
                (unixepoch('now', 'start of day', 'weekday 0')),
            score INTEGER
        );

        CREATE INDEX IF NOT EXISTS
            index_past_inputs_date on past_inputs(date);

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


@bot.event
async def on_ready():
    print(f"{bot.user} is ready and online!")


@bot.slash_command(description = "Given the first three pins, gives a pin valid for the next 18mn")
async def moodle_pins(
    ctx, pin0: discord.Option(int), pin1: discord.Option(int), pin2: discord.Option(int)
):
    if str(ctx.channel_id) not in channel_ids:
        return

    pins = [pin0, pin1, pin2]
    print(ctx.interaction.id, ctx.author.id, ctx.author.name, "requested", pins)

    for pin in pins:
        if pin < 1000 or pin > 10000:
            await ctx.respond("Wrong pins (╬ Ò﹏Ó)", ephemeral=True)
            return

    target_aggpins = pins2aggpins(pins)

    query_past_inputs = "SELECT count(*) FROM past_inputs WHERE aggpins = ?"
    async with db.execute(query_past_inputs, [target_aggpins]) as cursor:
        async for row in cursor:
            if row[0] > 0:
                await ctx.respond("Too late o(> < )o", ephemeral=True)
                return
            break

    await ctx.defer()

    data = await get_seed_lookup_data(pin0)

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
    final_pins = []
    for i in range(left, right):
        seed_bytes = data[i * 4 : i * 4 + 4]
        seed = int.from_bytes(seed_bytes, "big")
        if seed2aggpins(seed) == target_aggpins:
            seeds.append(seed)
            final_pins.append(seed2finalpin(seed))

    print(
        f"{ctx.interaction.id} {ctx.author.id} {ctx.author.name} got seeds {seeds} and pins {final_pins}"
    )

    if len(final_pins) == 0:
        await ctx.respond("Wrong pins (╬ Ò﹏Ó)", ephemeral=True)
        return

    await db.execute(
        """
        INSERT INTO past_inputs(aggpins, author_id)
        VALUES(?, ?)
        """,
        [target_aggpins, ctx.author.id]
    )
    await db.execute(
        """
        INSERT INTO leaderboard(author_id, author_name, score)
        VALUES(?, ?, ?)
        ON CONFLICT(author_id)
        DO UPDATE SET score = score + ?
        """,
        [ctx.author.id, ctx.author.display_name, 1, 1]
    )
    await db.commit()

    current_leaderboard = "== LEADERBOARD ==\n"
    query_leaderboard = "SELECT author_name, score FROM leaderboard ORDER BY score DESC LIMIT 3"
    async with db.execute(query_leaderboard) as cursor:
        async for row in cursor:
            current_leaderboard += f"=> {row[0]} ({row[1]} points)"

    if len(final_pins) == 1:
        await ctx.respond(f"<(￣︶￣)> This pin is valid for the next 18mn: {final_pins[0]}\n\n{current_leaderboard}")
    else:
        await ctx.respond(f"(・・ ) ? One of these pins is valid for the next 18mn: {final_pins}\n\n{current_leaderboard}")


bot.run(os.environ.get("DISCORD_TOKEN"))
