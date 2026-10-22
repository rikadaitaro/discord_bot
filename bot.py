import discord
from discord.ext import commands
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import json
import os

TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = 1562368847389130782

intents = discord.Intents.default()
intents.members = True
intents.message_content = True
VOTES_FILE = "votes.json"
SCORES_FILE = "scores.json"

bot = commands.Bot(
    command_prefix="!",
    intents=intents
)

FILE_NAME = "tantou.json"
EXCLUDE_MESSAGE_FILE = "exclude_message.json"


def load_data():
    if os.path.exists(FILE_NAME):
        with open(FILE_NAME, "r", encoding="utf-8") as f:
            return json.load(f)

    return {
        "current_index": -1,
        "current_member_id": None
    }


def save_data(data):
    with open(FILE_NAME, "w", encoding="utf-8") as f:
        json.dump(data, f)

def load_votes():
    if os.path.exists(VOTES_FILE):
        with open(VOTES_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_votes(data):
    with open(VOTES_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def load_scores():
    if os.path.exists(SCORES_FILE):
        with open(SCORES_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_scores(data):
    with open(SCORES_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

class RatingView(discord.ui.View):

    def __init__(self, target_id):
        super().__init__(timeout=None)
        self.target_id = target_id

    async def save_vote(
        self,
        interaction,
        score
    ):
        if interaction.user.id == self.target_id:
            await interaction.response.send_message(
                "❌ 担当者本人は投票できません。",
                ephemeral=True
            )
            return

        votes = load_votes()

        votes[str(interaction.user.id)] = score

        save_votes(votes)

        await interaction.response.send_message(
            f"✅ 評価を {score} に設定しました。",
            ephemeral=True
        )

    @discord.ui.button(
        label="⭐1",
        style=discord.ButtonStyle.secondary
    )
    async def rate1(
        self,
        interaction,
        button
    ):
        await self.save_vote(interaction, 1)

    @discord.ui.button(
        label="⭐2",
        style=discord.ButtonStyle.secondary
    )
    async def rate2(
        self,
        interaction,
        button
    ):
        await self.save_vote(interaction, 2)

    @discord.ui.button(
        label="⭐3",
        style=discord.ButtonStyle.primary
    )
    async def rate3(
        self,
        interaction,
        button
    ):
        await self.save_vote(interaction, 3)

    @discord.ui.button(
        label="⭐4",
        style=discord.ButtonStyle.success
    )
    async def rate4(
        self,
        interaction,
        button
    ):
        await self.save_vote(interaction, 4)

    @discord.ui.button(
        label="⭐5",
        style=discord.ButtonStyle.danger
    )
    async def rate5(
        self,
        interaction,
        button
    ):
        await self.save_vote(interaction, 5)

async def start_vote():

    guild = bot.guilds[0]
    channel = guild.get_channel(CHANNEL_ID)

    data = load_data()

    target_id = data["current_member_id"]

    if target_id is None:
        return

    member = guild.get_member(target_id)

    save_votes({})

    await channel.send(
        f"📊 {member.mention} さんの授業評価です。\n"
        "1～5で評価してください。",
        view=RatingView(target_id)
    )

async def finish_vote():

    guild = bot.guilds[0]
    channel = guild.get_channel(CHANNEL_ID)

    data = load_data()

    target_id = data["current_member_id"]

    if target_id is None:
        return

    member = guild.get_member(target_id)

    votes = load_votes()

    if len(votes) == 0:

        await channel.send(
            f"{member.display_name} さん\n"
            "投票がありませんでした。"
        )

        selected = await update_member(guild)

        if selected:
            await channel.send(
                f"📢 今週の担当は {selected.mention} さんです"
            )
    return

    average = round(
        sum(votes.values()) / len(votes),
        2
    )

    scores = load_scores()

    if str(target_id) not in scores:

        scores[str(target_id)] = {
            "name": member.display_name,
            "total": 0,
            "count": 0
        }

    scores[str(target_id)]["total"] += average
    scores[str(target_id)]["count"] += 1

    save_scores(scores)

    await channel.send(
        f"📊 {member.display_name} さんの評価\n"
        f"平均点: {average}/5\n"
        f"投票数: {len(votes)}"
    )

@bot.command()
async def ranking(ctx):

    scores = load_scores()

    if len(scores) == 0:
        await ctx.send("まだ評価がありません。")
        return

    ranking = []

    for user in scores.values():

        avg = round(
            user["total"] / user["count"],
            2
        )

        ranking.append(
            (user["name"], avg)
        )

    ranking.sort(
        key=lambda x: x[1],
        reverse=True
    )

    text = "🏆 評価ランキング\n\n"

    for i, (name, score) in enumerate(
        ranking,
        start=1
    ):
        text += f"{i}位 {name} : {score}\n"

    await ctx.send(text)

async def get_excluded_users(guild):
    if not os.path.exists(EXCLUDE_MESSAGE_FILE):
        return set()

    with open(EXCLUDE_MESSAGE_FILE, "r") as f:
        message_id = json.load(f)["message_id"]

    for channel in guild.text_channels:
        try:
            message = await channel.fetch_message(message_id)

            excluded = set()

            for reaction in message.reactions:
                if str(reaction.emoji) == "✅":
                    async for user in reaction.users():
                        if not user.bot:
                            excluded.add(user.id)

            return excluded

        except:
            pass

    return set()


async def get_valid_members(guild):
    excluded = await get_excluded_users(guild)

    return [
        m for m in guild.members
        if not m.bot and m.id not in excluded
    ]


async def update_member(guild):
    members = await get_valid_members(guild)

    if len(members) == 0:
        return None

    data = load_data()

    next_index = (data["current_index"] + 1) % len(members)

    selected = members[next_index]

    data["current_index"] = next_index
    data["current_member_id"] = selected.id

    save_data(data)

    return selected


async def weekly_member():
    guild = bot.guilds[0]

    channel = guild.get_channel(CHANNEL_ID)

    selected = await update_member(guild)

    if selected is None:
        return

    await channel.send(
        f"📢 今週の担当は {selected.mention} さんです！"
    )


@bot.event
async def on_ready():
    print(f"{bot.user} が起動しました")

    scheduler = AsyncIOScheduler()

    scheduler.add_job(
        start_vote,
        trigger="cron",
        day_of_week="sat",
        hour=23,
        minute=0
        )
    scheduler.add_job(
        finish_vote,
        trigger="cron",
        day_of_week="sun",
        hour=23,
        minute=0
        )
    scheduler.add_job(
        weekly_member,
        trigger="cron",
        day_of_week="sun",
        hour=23,
        minute=1
        )


    scheduler.start()


@bot.command()
async def tantou(ctx):
    data = load_data()

    if data["current_member_id"] is None:
        await ctx.send("まだ担当者が決まっていません。")
        return

    member = ctx.guild.get_member(
        data["current_member_id"]
    )

    if member is None:
        await ctx.send("担当者が見つかりません。")
        return

    await ctx.send(
        f"📢 現在の担当は {member.display_name} さんです！"
    )


@bot.command()
async def next(ctx):
    members = await get_valid_members(ctx.guild)

    if len(members) == 0:
        await ctx.send("担当候補がいません。")
        return

    data = load_data()

    next_index = (data["current_index"] + 1) % len(members)

    await ctx.send(
        f"📅 次回の担当は {members[next_index].display_name} さんです！"
    )


@bot.command()
async def tadokoro(ctx):
    await ctx.send(
        file=discord.File("tadokoro.jpeg")
    )


@bot.command()
@commands.has_permissions(administrator=True)
async def skip(ctx):
    selected = await update_member(ctx.guild)

    if selected is None:
        await ctx.send("担当候補がいません。")
        return

    await ctx.send(
        f"⏭️ 担当を変更しました！\n新しい担当は {selected.mention} さんです！"
    )


@bot.command()
@commands.has_permissions(administrator=True)
async def exclude(ctx):
    msg = await ctx.send(
        "✅を押すと担当候補から除外されます。"
    )

    await msg.add_reaction("✅")

    with open(EXCLUDE_MESSAGE_FILE, "w") as f:
        json.dump(
            {"message_id": msg.id},
            f
        )

    await ctx.send(
        "除外設定メッセージを作成しました。"
    )

@bot.command()
async def excluded(ctx):
    excluded_ids = await get_excluded_users(ctx.guild)

    if len(excluded_ids) == 0:
        await ctx.send("✅で除外されているメンバーはいません。")
        return

    names = []

    for user_id in excluded_ids:
        member = ctx.guild.get_member(user_id)

        if member:
            names.append(member.display_name)

    if len(names) == 0:
        await ctx.send("✅で除外されているメンバーはいません。")
        return

    await ctx.send(
        "🚫 除外中のメンバー\n" +
        "\n".join(f"・{name}" for name in names)
    )

@skip.error
async def skip_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send(
            "❌ このコマンドは管理者のみ使用できます。"
        )


@exclude.error
async def exclude_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send(
            "❌ このコマンドは管理者のみ使用できます。"
        )

bot.run(TOKEN)