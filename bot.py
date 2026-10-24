import discord
from discord.ext import commands
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import json
import os
from datetime import datetime
from zoneinfo import ZoneInfo

TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = 1562368847389130782
EXCLUDE_CHANNEL_ID = 1561915350554247242
EXCLUDE_MESSAGE_ID = 1562492446892621835

intents = discord.Intents.default()
intents.members = True
intents.message_content = True
VOTES_FILE = "votes.json"
SCORES_FILE = "scores.json"

bot = commands.Bot(
    command_prefix="!",
    intents=intents
)

scheduler = AsyncIOScheduler(
    timezone=ZoneInfo("Asia/Tokyo")
)

FILE_NAME = "tantou.json"
EXCLUDE_MESSAGE_FILE = "exclude_message.json"


def load_data():
    if os.path.exists(FILE_NAME):
        with open(FILE_NAME, "r", encoding="utf-8") as f:
            data = json.load(f)

        # 古いtantou.jsonにも自動対応
        if "current_index" not in data:
            data["current_index"] = -1

        if "current_member_id" not in data:
            data["current_member_id"] = None

        if "member_order" not in data:
            data["member_order"] = []

        return data

    return {
        "current_index": -1,
        "current_member_id": None,
        "member_order": []
    }


def save_data(data):
    with open(FILE_NAME, "w", encoding="utf-8") as f:
        json.dump(
            data,
            f,
            ensure_ascii=False,
            indent=4
        )

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

    print("Guild:", guild.name)
    print("CHANNEL_ID:", CHANNEL_ID)

    channel = guild.get_channel(CHANNEL_ID)

    print("Channel:", channel)

    if channel is None:
        print("チャンネル取得失敗")
        return

    channel = bot.get_channel(CHANNEL_ID)

    if channel is None:
        channel = await bot.fetch_channel(CHANNEL_ID)

    data = load_data()

    target_id = data["current_member_id"]

    if target_id is None:
        return

    guild = bot.guilds[0]
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
    channel = guild.get_channel(EXCLUDE_CHANNEL_ID)

    if channel is None:
        try:
            channel = await bot.fetch_channel(EXCLUDE_CHANNEL_ID)
        except discord.NotFound:
            print("除外設定のチャンネルが見つかりません。")
            return set()
        except discord.Forbidden:
            print("除外設定のチャンネルを見る権限がありません。")
            return set()
        except discord.HTTPException as error:
            print(f"チャンネルの取得に失敗しました: {error}")
            return set()

    try:
        message = await channel.fetch_message(EXCLUDE_MESSAGE_ID)

    except discord.NotFound:
        print("除外設定メッセージが見つかりません。")
        return set()

    except discord.Forbidden:
        print("除外設定メッセージを読む権限がありません。")
        return set()

    except discord.HTTPException as error:
        print(f"除外設定メッセージの取得に失敗しました: {error}")
        return set()

    excluded_users = set()

    for reaction in message.reactions:
        if str(reaction.emoji) != "✅":
            continue

        async for user in reaction.users():
            if not user.bot:
                excluded_users.add(user.id)

    return excluded_users


async def get_valid_members(guild):
    excluded_ids = await get_excluded_users(guild)
    data = load_data()

    ordered_members = []
    registered_ids = set()

    # 設定された順番で追加
    for member_id in data["member_order"]:
        member = guild.get_member(int(member_id))

        if member is None:
            continue

        if member.bot:
            continue

        if member.id in excluded_ids:
            continue

        ordered_members.append(member)
        registered_ids.add(member.id)

    # 順番に未登録のメンバーは最後に追加
    for member in guild.members:
        if member.bot:
            continue

        if member.id in excluded_ids:
            continue

        if member.id in registered_ids:
            continue

        ordered_members.append(member)

    return ordered_members


async def update_member(guild):
    members = await get_valid_members(guild)

    if not members:
        return None

    data = load_data()
    current_member_id = data.get("current_member_id")

    current_position = -1

    # 現在の担当者が候補内にいる場合
    for index, member in enumerate(members):
        if member.id == current_member_id:
            current_position = index
            break

    next_position = (current_position + 1) % len(members)
    selected = members[next_position]

    data["current_index"] = next_position
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
    print(f"ログイン: {bot.user}")
    print(datetime.now(ZoneInfo("Asia/Tokyo")))

    if scheduler.running:
        return

    scheduler.add_job(
        weekly_member,
        trigger="cron",
        day_of_week="sat",
        hour=11,
        minute=0,
        id="weekly_member"
    )

    scheduler.add_job(
        start_vote,
        trigger="cron",
        day_of_week="sat",
        hour=23,
        minute=0,
        id="start_vote"
    )

    scheduler.add_job(
        finish_vote,
        trigger="cron",
        day_of_week="sun",
        hour=23,
        minute=0,
        id="finish_vote"
    )

    scheduler.start()

    print("Scheduler Started (JST)")
@bot.event
async def on_message(message):

    if message.author.bot:
        return

    text = message.content

    if "すぎ" in text:

        before = text.split("すぎ")[0]

        await message.channel.send(
            f"お前{before}スギ"
        )
    elif "過ぎ" in text:

        before = text.split("過ぎ")[0]

        await message.channel.send(
            f"お前{before}スギ"
        )

    await bot.process_commands(message)

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
async def today(ctx):
    await weekly_member()

@bot.command()
async def vote(ctx):
    await start_vote()
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
    try:
        channel = await bot.fetch_channel(EXCLUDE_CHANNEL_ID)
        message = await channel.fetch_message(EXCLUDE_MESSAGE_ID)

        await ctx.send(
            "✅ 除外設定メッセージはこちらです。\n"
            f"{message.jump_url}"
        )

    except discord.NotFound:
        await ctx.send(
            "❌ 除外設定メッセージが見つかりません。\n"
            "チャンネルIDとメッセージIDを確認してください。"
        )

    except discord.Forbidden:
        await ctx.send(
            "❌ Botに除外設定メッセージを見る権限がありません。"
        )

    except discord.HTTPException as error:
        await ctx.send(
            f"❌ メッセージの取得中にエラーが発生しました。\n"
            f"`{error}`"
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

@bot.command()
async def profile(ctx, member: discord.Member = None):
    # メンションがなければ、コマンドを使った本人を表示
    if member is None:
        member = ctx.author

    scores = load_scores()
    member_id = str(member.id)

    # その人の評価記録がない場合
    if member_id not in scores or len(scores[member_id]) == 0:
        await ctx.send(
            f"📭 {member.display_name} さんには、"
            "まだ授業評価の記録がありません。"
        )
        return

    member_scores = scores[member_id]

    average_score = sum(member_scores) / len(member_scores)
    highest_score = max(member_scores)
    lowest_score = min(member_scores)
    latest_score = member_scores[-1]
    evaluation_count = len(member_scores)

    # 全員の平均点を計算してランキングを作る
    ranking_data = []

    for user_id, score_list in scores.items():
        if not score_list:
            continue

        user_average = sum(score_list) / len(score_list)

        ranking_data.append({
            "user_id": user_id,
            "average": user_average
        })

    # 平均点が高い順
    ranking_data.sort(
        key=lambda item: item["average"],
        reverse=True
    )

    rank = None

    for index, item in enumerate(ranking_data, start=1):
        if item["user_id"] == member_id:
            rank = index
            break

    # 前回との変化
    if len(member_scores) >= 2:
        previous_score = member_scores[-2]
        score_change = latest_score - previous_score

        if score_change > 0:
            change_text = f"📈 +{score_change:.2f}"
        elif score_change < 0:
            change_text = f"📉 {score_change:.2f}"
        else:
            change_text = "➡️ 変化なし"
    else:
        change_text = "初回のため比較なし"

    # 順位に応じた表示
    if rank == 1:
        rank_text = "🥇 1位"
    elif rank == 2:
        rank_text = "🥈 2位"
    elif rank == 3:
        rank_text = "🥉 3位"
    else:
        rank_text = f"🏆 {rank}位"

    embed = discord.Embed(
        title=f"👤 {member.display_name} さんの授業プロフィール",
        color=discord.Color.blue()
    )

    embed.set_thumbnail(url=member.display_avatar.url)

    embed.add_field(
        name="現在順位",
        value=rank_text,
        inline=True
    )

    embed.add_field(
        name="総合平均",
        value=f"⭐ {average_score:.2f} / 5.00",
        inline=True
    )

    embed.add_field(
        name="評価された回数",
        value=f"📚 {evaluation_count}回",
        inline=True
    )

    embed.add_field(
        name="最高評価",
        value=f"🚀 {highest_score:.2f}",
        inline=True
    )

    embed.add_field(
        name="最低評価",
        value=f"🔻 {lowest_score:.2f}",
        inline=True
    )

    embed.add_field(
        name="前回評価",
        value=f"🕒 {latest_score:.2f}",
        inline=True
    )

    embed.add_field(
        name="前回からの変化",
        value=change_text,
        inline=False
    )

    # 最近5回分
    recent_scores = member_scores[-5:]
    recent_text = "\n".join(
        f"{index}. {score:.2f}"
        for index, score in enumerate(
            reversed(recent_scores),
            start=1
        )
    )

    embed.add_field(
        name="最近の評価",
        value=recent_text,
        inline=False
    )

    await ctx.send(embed=embed)
    
@bot.command()
@commands.has_permissions(administrator=True)
async def setorder(ctx, *members: discord.Member):
    if len(members) == 0:
        await ctx.send(
            "❌ 順番に設定するメンバーをメンションしてください。\n\n"
            "使用例：\n"
            "`!setorder @鈴木 @田中 @佐藤`"
        )
        return

    # 同じ人が複数回指定されていないか確認
    member_ids = [member.id for member in members]

    if len(member_ids) != len(set(member_ids)):
        await ctx.send(
            "❌ 同じメンバーが複数回指定されています。"
        )
        return

    # Botが指定されていないか確認
    bot_members = [
        member.display_name
        for member in members
        if member.bot
    ]

    if bot_members:
        await ctx.send(
            "❌ Botは担当順に登録できません。\n"
            + "\n".join(
                f"・{name}"
                for name in bot_members
            )
        )
        return

    data = load_data()
    data["member_order"] = member_ids

    # 現在の担当者が未設定の場合だけ初期状態にする
    if data["current_member_id"] is None:
        data["current_index"] = -1

    save_data(data)

    order_text = "\n".join(
        f"{index}. {member.display_name}"
        for index, member in enumerate(members, start=1)
    )

    await ctx.send(
        "✅ 担当順を更新しました！\n\n"
        f"{order_text}"
    )
    
@bot.command(name="order")
async def show_order(ctx):
    data = load_data()
    member_order = data.get("member_order", [])

    if not member_order:
        await ctx.send(
            "📭 担当順はまだ設定されていません。\n"
            "管理者が `!setorder @メンバー1 @メンバー2` "
            "で設定できます。"
        )
        return

    excluded_ids = await get_excluded_users(ctx.guild)
    lines = []

    for index, member_id in enumerate(member_order, start=1):
        member = ctx.guild.get_member(int(member_id))

        if member is None:
            lines.append(
                f"{index}. 不明なメンバー"
            )
            continue

        if member.id in excluded_ids:
            status = "🚫 除外中"
        elif member.id == data.get("current_member_id"):
            status = "📍 現在の担当"
        else:
            status = ""

        lines.append(
            f"{index}. {member.display_name} {status}".rstrip()
        )

    await ctx.send(
        "📋 担当順\n\n"
        + "\n".join(lines)
    )

@setorder.error
async def setorder_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send(
            "❌ 担当順を変更できるのは管理者だけです。"
        )

    elif isinstance(error, commands.MemberNotFound):
        await ctx.send(
            "❌ メンバーが見つかりませんでした。\n"
            "名前ではなくメンションで指定してください。"
        )


    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(
            "❌ 削除するメンバーを指定してください。\n"
            "使用例：`!removeorder @鈴木`"
        )

    elif isinstance(error, commands.MemberNotFound):
        await ctx.send(
            "❌ 指定されたメンバーが見つかりません。"
        )

bot.run(TOKEN)