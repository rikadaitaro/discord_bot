import asyncio
import json
import os
from datetime import datetime
from zoneinfo import ZoneInfo

import discord
from discord.ext import commands
from apscheduler.schedulers.asyncio import AsyncIOScheduler

TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = 1561915350554247247
EXCLUDE_CHANNEL_ID = 1561915350554247242
EXCLUDE_MESSAGE_ID = 1562492446892621835

intents = discord.Intents.default()
intents.members = True
intents.message_content = True

VOTES_FILE = "votes.json"
SCORES_FILE = "scores.json"
FILE_NAME = "tantou.json"

bot = commands.Bot(
    command_prefix="!",
    intents=intents
)

scheduler = AsyncIOScheduler(
    timezone=ZoneInfo("Asia/Tokyo")
)

# 同時投票で votes.json / scores.json が壊れないようにするロック
_votes_lock = asyncio.Lock()
_scores_lock = asyncio.Lock()


# ---------- JSON の読み書き（共通・安全版） ----------

def _load_json(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as error:
        print(f"{path} の読み込みに失敗しました: {error}")
        return default
    return data if isinstance(data, dict) else default


def _save_json(path, data):
    # 書き込み途中で落ちても元ファイルが壊れないよう、一時ファイル経由で保存する
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, path)


def load_data():
    data = _load_json(FILE_NAME, {})

    # 古い tantou.json にも自動対応
    data.setdefault("current_index", -1)
    data.setdefault("current_member_id", None)
    data.setdefault("member_order", [])
    # 投票の受付状態（再起動後もボタンが生きるように保存する）
    data.setdefault("vote_active", False)
    data.setdefault("vote_target_id", None)
    # 次週の担当の選択: "change"（次の人に変更）/ "keep"（同じ人を続ける）/ None（未選択）
    # 土曜 23:00〜日曜 23:00 の間に管理者が選ぶ。未選択の場合は「keep」と同じ扱い。
    data.setdefault("next_action", None)
    data.setdefault("next_decided_by", None)
    return data


def save_data(data):
    _save_json(FILE_NAME, data)


def load_votes():
    votes = _load_json(VOTES_FILE, {})
    # 値が数値でないゴミが入っていたら除外する
    return {
        str(user_id): int(score)
        for user_id, score in votes.items()
        if isinstance(score, (int, float))
    }


def save_votes(data):
    _save_json(VOTES_FILE, data)


def load_scores():
    """scores.json を正規化して返す。

    正式な形:
        {"123": {"name": "名前", "total": 12.5, "count": 3,
                 "history": [4.0, 4.5, 4.0]}}
    total / count は週平均の合計と回数、history は週ごとの平均点の履歴。
    旧形式（history なし）もそのまま読める。history が無い場合は
    profile 側で total / count から平均だけを出す。
    """
    raw = _load_json(SCORES_FILE, {})
    scores = {}

    for user_id, entry in raw.items():
        # 万が一リスト形式の古いデータが混ざっていた場合の救済
        if isinstance(entry, list):
            history = [float(x) for x in entry if isinstance(x, (int, float))]
            scores[str(user_id)] = {
                "name": str(user_id),
                "total": round(sum(history), 2),
                "count": len(history),
                "history": history,
            }
            continue

        if not isinstance(entry, dict):
            continue

        history = entry.get("history") or []
        history = [float(x) for x in history if isinstance(x, (int, float))]
        count = int(entry.get("count") or len(history) or 0)
        total = float(entry.get("total") or (sum(history) if history else 0))

        scores[str(user_id)] = {
            "name": str(entry.get("name") or user_id),
            "total": total,
            "count": count,
            "history": history,
        }

    return scores


def save_scores(data):
    _save_json(SCORES_FILE, data)


# ---------- 通知チャンネル / サーバーの取得 ----------

async def get_announce_channel():
    """CHANNEL_ID からチャンネルを取る。bot.guilds[0] 決め打ちはしない。

    チャンネルが取れれば、そこから guild も分かるので
    複数サーバーに入っていても間違えない。
    """
    channel = bot.get_channel(CHANNEL_ID)
    if channel is None:
        try:
            channel = await bot.fetch_channel(CHANNEL_ID)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException) as error:
            print(f"通知チャンネルの取得に失敗しました: {error}")
            return None
    return channel


# ---------- 投票ビュー（再起動後も使える永続ビュー） ----------

class RatingView(discord.ui.View):

    def __init__(self, target_id):
        super().__init__(timeout=None)
        self.target_id = target_id

    async def save_vote(self, interaction, score):
        data = load_data()

        # 受付期間外・対象が違う古いボタンを弾く
        if not data.get("vote_active"):
            await interaction.response.send_message(
                "❌ この投票は受付期間外です。",
                ephemeral=True
            )
            return

        target_id = data.get("vote_target_id") or self.target_id

        if interaction.user.id == target_id:
            await interaction.response.send_message(
                "❌ 担当者本人は投票できません。",
                ephemeral=True
            )
            return

        async with _votes_lock:
            votes = load_votes()
            votes[str(interaction.user.id)] = score
            save_votes(votes)

        await interaction.response.send_message(
            f"✅ 評価を {score} に設定しました。",
            ephemeral=True
        )

    @discord.ui.button(
        label="⭐1",
        style=discord.ButtonStyle.secondary,
        custom_id="rating_1"
    )
    async def rate1(self, interaction, button):
        await self.save_vote(interaction, 1)

    @discord.ui.button(
        label="⭐2",
        style=discord.ButtonStyle.secondary,
        custom_id="rating_2"
    )
    async def rate2(self, interaction, button):
        await self.save_vote(interaction, 2)

    @discord.ui.button(
        label="⭐3",
        style=discord.ButtonStyle.primary,
        custom_id="rating_3"
    )
    async def rate3(self, interaction, button):
        await self.save_vote(interaction, 3)

    @discord.ui.button(
        label="⭐4",
        style=discord.ButtonStyle.success,
        custom_id="rating_4"
    )
    async def rate4(self, interaction, button):
        await self.save_vote(interaction, 4)

    @discord.ui.button(
        label="⭐5",
        style=discord.ButtonStyle.danger,
        custom_id="rating_5"
    )
    async def rate5(self, interaction, button):
        await self.save_vote(interaction, 5)


def _next_in_list(members, current_id):
    """候補リスト上で current_id の次の人を返す。状態は変えない（プレビュー用）。"""
    if not members:
        return None
    current_position = -1
    for index, member in enumerate(members):
        if member.id == current_id:
            current_position = index
            break
    return members[(current_position + 1) % len(members)]


class NextTantouView(discord.ui.View):
    """次週の担当を「変更する / 同じ人を続ける」から選ぶボタン。

    土曜 23:00 に start_vote と一緒に送られ、日曜 23:00 の締め切りまで
    管理者が何度でも選び直せる。勝手に担当は変わらない。
    """

    def __init__(self):
        super().__init__(timeout=None)

    async def _save_choice(self, interaction, action):
        # 管理者のみが選べる（誰でも押せると荒らされるため）
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message(
                "❌ 担当の変更 / 継続を選べるのは管理者のみです。",
                ephemeral=True
            )
            return

        data = load_data()
        if not data.get("vote_active"):
            await interaction.response.send_message(
                "❌ 今は担当の選択期間ではありません。"
                "（土曜 23:00〜日曜 23:00）",
                ephemeral=True
            )
            return

        data["next_action"] = action
        data["next_decided_by"] = interaction.user.id
        save_data(data)

        guild = interaction.guild
        current = None
        if guild is not None and data.get("current_member_id") is not None:
            current = guild.get_member(data["current_member_id"])
        current_text = current.mention if current is not None else "不明なメンバー"

        if action == "change":
            members = await get_valid_members(guild)
            next_member = _next_in_list(members, data.get("current_member_id"))
            next_text = (
                next_member.display_name if next_member is not None else "候補なし"
            )
            choice_text = f"🔁 次の担当に変更します（次は {next_text} さん）"
        else:
            choice_text = "⏸️ 同じ担当を続けます"

        await interaction.response.edit_message(
            content=(
                "📋 次週の担当について\n\n"
                f"現在の担当: {current_text}\n"
                f"選択: {choice_text}\n"
                f"（{interaction.user.display_name} さんが選択）\n\n"
                "日曜 23:00 の締め切りまで変更できます。"
            ),
            view=self
        )

    @discord.ui.button(
        label="🔁 次の担当に変更",
        style=discord.ButtonStyle.primary,
        custom_id="next_tantou_change"
    )
    async def change_button(self, interaction, button):
        await self._save_choice(interaction, "change")

    @discord.ui.button(
        label="⏸️ 同じ担当を続ける",
        style=discord.ButtonStyle.secondary,
        custom_id="next_tantou_keep"
    )
    async def keep_button(self, interaction, button):
        await self._save_choice(interaction, "keep")


async def send_next_choice(channel, guild):
    """土曜 23:00 に、次週の担当の選択肢を送る。担当はここでは変えない。"""
    data = load_data()
    data["next_action"] = None
    data["next_decided_by"] = None
    save_data(data)

    current_id = data.get("current_member_id")
    current = guild.get_member(current_id) if current_id is not None else None
    members = await get_valid_members(guild)
    next_member = _next_in_list(members, current_id)

    current_text = current.mention if current is not None else "不明なメンバー"
    next_text = (
        next_member.display_name if next_member is not None else "候補なし"
    )

    await channel.send(
        "📋 次週の担当について\n\n"
        f"現在の担当: {current_text}\n"
        f"変更する場合の次の担当: {next_text}\n\n"
        "土曜 23:00〜日曜 23:00 の間に、管理者が下のボタンで選んでください。\n"
        "・🔁 次の担当に変更\n"
        "・⏸️ 同じ担当を続ける\n"
        "選択がない場合は、同じ担当が続きます。",
        view=NextTantouView()
    )


async def start_vote():
    """土曜 23:00 に投票を開始する。成功したら True を返す。"""
    channel = await get_announce_channel()
    if channel is None:
        print("チャンネル取得失敗")
        return False

    guild = channel.guild
    data = load_data()
    target_id = data.get("current_member_id")

    if target_id is None:
        print("投票対象の担当者が決まっていません")
        return False

    # すでに同じ対象の投票が受付中なら、二重に開始して票を消さない
    if data.get("vote_active") and data.get("vote_target_id") == target_id:
        print("投票はすでに受付中です")
        return False

    member = guild.get_member(target_id)
    if member is None:
        print(f"投票対象のメンバーが見つかりません: {target_id}")
        return False

    async with _votes_lock:
        save_votes({})

    data["vote_active"] = True
    data["vote_target_id"] = target_id
    save_data(data)

    await channel.send(
        f"📊 {member.mention} さんの授業評価です。\n"
        "1～5で評価してください。",
        view=RatingView(target_id)
    )

    # 投票と同時に、次週の担当の選択肢も送る。担当はここでは変えない
    await send_next_choice(channel, guild)
    return True


async def finish_vote():
    """日曜 23:00 に投票を締め切り、結果と次の担当者を伝える。

    次の担当は勝手に変えない。土曜 23:00〜日曜 23:00 に管理者が
    選んだ内容（next_action）に従うだけ。
    - "change": 次の人に切り替える
    - "keep" / 未選択: 同じ担当が続く
    土曜 11:00 の weekly_member は切り替えずに再通知だけする。
    """
    channel = await get_announce_channel()
    if channel is None:
        print("チャンネル取得失敗")
        return False

    guild = channel.guild
    data = load_data()
    target_id = data.get("current_member_id")

    if target_id is None:
        print("投票対象の担当者が決まっていません")
        return False

    member = guild.get_member(target_id)
    display_name = member.display_name if member is not None else "不明なメンバー"

    async with _votes_lock:
        votes = load_votes()

    if len(votes) == 0:
        await channel.send(
            f"{display_name} さん\n"
            "投票がありませんでした。"
        )
    else:
        average = round(sum(votes.values()) / len(votes), 2)

        async with _scores_lock:
            scores = load_scores()
            entry = scores.get(str(target_id), {
                "name": display_name,
                "total": 0,
                "count": 0,
                "history": [],
            })
            entry["name"] = display_name
            entry["total"] = round(float(entry.get("total") or 0) + average, 2)
            entry["count"] = int(entry.get("count") or 0) + 1
            entry.setdefault("history", []).append(average)
            scores[str(target_id)] = entry
            save_scores(scores)

        await channel.send(
            f"📊 {display_name} さんの評価\n"
            f"平均点: {average}/5\n"
            f"投票数: {len(votes)}"
        )

    # 投票を締め切る（2 回集計されないようにここで必ずクリアする）
    async with _votes_lock:
        save_votes({})
    data = load_data()
    next_action = data.get("next_action")
    data["vote_active"] = False
    data["vote_target_id"] = None
    data["next_action"] = None
    data["next_decided_by"] = None
    save_data(data)

    # 土曜 23:00 に選ばれた内容に従って、次の担当を決める。勝手に変えない
    if next_action == "change":
        selected = await update_member(guild)
        if selected is None:
            await channel.send("次の担当候補がいません。")
            return True

        await channel.send(
            f"📢 次週の担当は {selected.mention} さんです！\n"
            "土曜 11:00 にも改めてお知らせします。"
        )
        return True

    # "keep" の場合も、選択がなかった場合も、同じ担当が続く
    reason = "選択されたため" if next_action == "keep" else "選択がなかったため"
    if member is not None:
        await channel.send(
            f"📢 次週も {member.mention} さんが担当です！"
            f"（{reason}、担当は変わりません）\n"
            "土曜 11:00 にも改めてお知らせします。"
        )
    else:
        await channel.send(
            "次週の担当が決まりませんでした。管理者が `!skip` で担当を変更できます。"
        )
    return True


@bot.command()
async def ranking(ctx):
    scores = load_scores()

    if len(scores) == 0:
        await ctx.send("まだ評価がありません。")
        return

    ranking_list = []
    for user in scores.values():
        count = int(user.get("count") or 0)
        if count <= 0:
            continue
        avg = round(float(user.get("total") or 0) / count, 2)
        ranking_list.append((user.get("name", "不明"), avg))

    if not ranking_list:
        await ctx.send("まだ評価がありません。")
        return

    ranking_list.sort(key=lambda x: x[1], reverse=True)

    text = "🏆 評価ランキング\n\n"
    for i, (name, score) in enumerate(ranking_list, start=1):
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
        try:
            member = guild.get_member(int(member_id))
        except (TypeError, ValueError):
            continue

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
    """土曜 11:00 のお知らせ。担当の切り替えはしない。

    切り替えは日曜 23:00 の finish_vote で済んでいるので、
    ここで進めると 1 人飛ばしてしまう。
    担当がまだ誰も決まっていない初回だけ進める。
    """
    channel = await get_announce_channel()
    if channel is None:
        print("チャンネル取得失敗")
        return False

    guild = channel.guild
    data = load_data()
    current_id = data.get("current_member_id")

    if current_id is None:
        selected = await update_member(guild)
        if selected is None:
            return False
        await channel.send(
            f"📢 今週の担当は {selected.mention} さんです！"
        )
        return True

    member = guild.get_member(current_id)
    if member is None:
        selected = await update_member(guild)
        if selected is None:
            return False
        await channel.send(
            f"📢 今週の担当は {selected.mention} さんです！"
        )
        return True

    await channel.send(
        f"📢 今週の担当は {member.mention} さんです！"
    )
    return True


async def show_current_tantou(ctx):
    """現在の担当を表示するだけ。ローテーションは進めない。"""
    data = load_data()

    if data["current_member_id"] is None:
        await ctx.send("まだ担当者が決まっていません。")
        return

    member = ctx.guild.get_member(data["current_member_id"])
    if member is None:
        await ctx.send("担当者が見つかりません。")
        return

    await ctx.send(
        f"📢 現在の担当は {member.display_name} さんです！"
    )


@bot.event
async def on_ready():
    print(f"ログイン: {bot.user}")
    print(datetime.now(ZoneInfo("Asia/Tokyo")))

    # 再起動後も、受付中の投票ボタンと担当選択ボタンが押せるように復旧する
    data = load_data()
    if data.get("vote_active") and data.get("vote_target_id") is not None:
        bot.add_view(RatingView(data["vote_target_id"]))
        bot.add_view(NextTantouView())
        print("投票ビューを復旧しました")

    if scheduler.running:
        return

    scheduler.add_job(
        weekly_member,
        trigger="cron",
        day_of_week="sat",
        hour=11,
        minute=0,
        id="weekly_member",
        replace_existing=True
    )

    scheduler.add_job(
        start_vote,
        trigger="cron",
        day_of_week="sat",
        hour=23,
        minute=0,
        id="start_vote",
        replace_existing=True
    )

    scheduler.add_job(
        finish_vote,
        trigger="cron",
        day_of_week="sun",
        hour=23,
        minute=0,
        id="finish_vote",
        replace_existing=True
    )

    scheduler.start()
    print("Scheduler Started (JST)")


@bot.event
async def on_message(message):
    if message.author.bot:
        return

    # コマンドには「すぎ」反応をしない
    if message.content.startswith("!"):
        await bot.process_commands(message)
        return

    text = message.content

    if "すぎ" in text:
        before = text.split("すぎ")[0].strip()
        # 「すぎ」だけ・長すぎる文には反応しない
        if before:
            await message.channel.send(f"お前{before}スギ")
    elif "過ぎ" in text:
        before = text.split("過ぎ")[0].strip()
        if before:
            await message.channel.send(f"お前{before}スギ")

    await bot.process_commands(message)


@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        return
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("❌ このコマンドは管理者のみ使用できます。")
        return
    if isinstance(error, commands.MemberNotFound):
        await ctx.send(
            "❌ メンバーが見つかりませんでした。\n"
            "名前ではなくメンションで指定してください。"
        )
        return
    if isinstance(error, commands.BadArgument):
        await ctx.send("❌ 引数の指定が正しくありません。")
        return

    print(f"コマンドエラー ({ctx.command}): {error}")
    await ctx.send("❌ コマンドの実行中にエラーが発生しました。")


@bot.command()
async def tantou(ctx):
    await show_current_tantou(ctx)


@bot.command()
async def next(ctx):
    members = await get_valid_members(ctx.guild)

    if len(members) == 0:
        await ctx.send("担当候補がいません。")
        return

    data = load_data()
    current_id = data.get("current_member_id")

    # 保存済みの current_index ではなく、今の候補リスト上の位置から次を決める
    # （除外・順番変更で index がずれても正しい人を出すため）
    next_member = _next_in_list(members, current_id)
    await ctx.send(
        f"📅 次回の担当は {next_member.display_name} さんです！"
    )


@bot.command()
async def tadokoro(ctx):
    if not os.path.exists("tadokoro.jpeg"):
        await ctx.send("❌ tadokoro.jpeg が見つかりません。")
        return
    try:
        await ctx.send(file=discord.File("tadokoro.jpeg"))
    except discord.HTTPException as error:
        await ctx.send(f"❌ 画像の送信に失敗しました。\n`{error}`")


@bot.command()
async def today(ctx):
    # 表示だけ。以前のように担当を進めない
    await show_current_tantou(ctx)


@bot.command()
@commands.has_permissions(administrator=True)
async def vote(ctx):
    # 管理者のみ。受付中の投票を上書きして票を消すこともない
    data = load_data()
    if data.get("vote_active"):
        await ctx.send("❌ 投票はすでに受付中です。")
        return

    started = await start_vote()
    if started:
        await ctx.send("✅ 投票を開始しました。")
    else:
        await ctx.send("❌ 投票を開始できませんでした。担当者が決まっているか確認してください。")


@bot.command()
@commands.has_permissions(administrator=True)
async def tantouchoice(ctx):
    """担当の選択メッセージをもう一度送る（土曜 23:00 の分を見逃した時用）。"""
    data = load_data()
    if not data.get("vote_active"):
        await ctx.send(
            "❌ 今は担当の選択期間ではありません。"
            "（土曜 23:00〜日曜 23:00）"
        )
        return

    channel = await get_announce_channel()
    if channel is None:
        await ctx.send("❌ 通知チャンネルが取得できませんでした。")
        return

    # 選択をリセットせず、メッセージだけ再送する（決まっている選択は残す）
    current_id = data.get("current_member_id")
    current = ctx.guild.get_member(current_id) if current_id is not None else None
    members = await get_valid_members(ctx.guild)
    next_member = _next_in_list(members, current_id)
    current_text = current.mention if current is not None else "不明なメンバー"
    next_text = next_member.display_name if next_member is not None else "候補なし"
    await channel.send(
        "📋 次週の担当について\n\n"
        f"現在の担当: {current_text}\n"
        f"変更する場合の次の担当: {next_text}\n\n"
        "日曜 23:00 の締め切りまでに、管理者が下のボタンで選んでください。\n"
        "・🔁 次の担当に変更\n"
        "・⏸️ 同じ担当を続ける\n"
        "選択がない場合は、同じ担当が続きます。",
        view=NextTantouView()
    )


@bot.command()
@commands.has_permissions(administrator=True)
async def skip(ctx):
    selected = await update_member(ctx.guild)

    if selected is None:
        await ctx.send("担当候補がいません。")
        return

    # 対象が変わるので、進行中の投票と次週の担当の選択は無効にする
    data = load_data()
    data["vote_active"] = False
    data["vote_target_id"] = None
    data["next_action"] = None
    data["next_decided_by"] = None
    save_data(data)
    async with _votes_lock:
        save_votes({})

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
            "❌ メッセージの取得中にエラーが発生しました。\n"
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


@bot.command()
async def profile(ctx, member: discord.Member = None):
    # メンションがなければ、コマンドを使った本人を表示
    if member is None:
        member = ctx.author

    scores = load_scores()
    member_id = str(member.id)

    # scores.json は {"name", "total", "count", "history"} の辞書形式。
    # 以前の profile はリスト形式を想定していて必ず落ちていたので、
    # ここでは辞書形式に合わせて計算する。
    if member_id not in scores:
        await ctx.send(
            f"📭 {member.display_name} さんには、"
            "まだ授業評価の記録がありません。"
        )
        return

    entry = scores[member_id]
    count = int(entry.get("count") or 0)
    if count <= 0:
        await ctx.send(
            f"📭 {member.display_name} さんには、"
            "まだ授業評価の記録がありません。"
        )
        return

    total = float(entry.get("total") or 0)
    average_score = round(total / count, 2)
    history = [float(x) for x in (entry.get("history") or [])]

    if history:
        highest_score = max(history)
        lowest_score = min(history)
        latest_score = history[-1]
        evaluation_count = len(history)
        recent_scores = history[-5:]
        recent_text = "\n".join(
            f"{index}. {score:.2f}"
            for index, score in enumerate(reversed(recent_scores), start=1)
        )
        if len(history) >= 2:
            score_change = history[-1] - history[-2]
            if score_change > 0:
                change_text = f"📈 +{score_change:.2f}"
            elif score_change < 0:
                change_text = f"📉 {score_change:.2f}"
            else:
                change_text = "➡️ 変化なし"
        else:
            change_text = "初回のため比較なし"
    else:
        # 旧データ（history なし）の場合：平均だけが分かる
        highest_score = average_score
        lowest_score = average_score
        latest_score = average_score
        evaluation_count = count
        recent_text = "履歴がありません（旧データ）"
        change_text = "履歴がないため比較なし"

    # 全員の平均点を計算してランキングを作る
    ranking_data = []
    for user_id, score_entry in scores.items():
        user_count = int(score_entry.get("count") or 0)
        if user_count <= 0:
            continue
        user_average = round(
            float(score_entry.get("total") or 0) / user_count, 2
        )
        ranking_data.append({
            "user_id": user_id,
            "average": user_average
        })

    ranking_data.sort(key=lambda item: item["average"], reverse=True)

    rank = None
    for index, item in enumerate(ranking_data, start=1):
        if item["user_id"] == member_id:
            rank = index
            break

    if rank == 1:
        rank_text = "🥇 1位"
    elif rank == 2:
        rank_text = "🥈 2位"
    elif rank == 3:
        rank_text = "🥉 3位"
    elif rank is None:
        rank_text = "圏外"
    else:
        rank_text = f"🏆 {rank}位"

    embed = discord.Embed(
        title=f"👤 {member.display_name} さんの授業プロフィール",
        color=discord.Color.blue()
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="現在順位", value=rank_text, inline=True)
    embed.add_field(name="総合平均", value=f"⭐ {average_score:.2f} / 5.00", inline=True)
    embed.add_field(name="評価された回数", value=f"📚 {evaluation_count}回", inline=True)
    embed.add_field(name="最高評価", value=f"🚀 {highest_score:.2f}", inline=True)
    embed.add_field(name="最低評価", value=f"🔻 {lowest_score:.2f}", inline=True)
    embed.add_field(name="前回評価", value=f"🕒 {latest_score:.2f}", inline=True)
    embed.add_field(name="前回からの変化", value=change_text, inline=False)
    embed.add_field(name="最近の評価", value=recent_text, inline=False)

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

    member_ids = [member.id for member in members]

    if len(member_ids) != len(set(member_ids)):
        await ctx.send("❌ 同じメンバーが複数回指定されています。")
        return

    bot_members = [
        member.display_name
        for member in members
        if member.bot
    ]
    if bot_members:
        await ctx.send(
            "❌ Botは担当順に登録できません。\n"
            + "\n".join(f"・{name}" for name in bot_members)
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
        try:
            member = ctx.guild.get_member(int(member_id))
        except (TypeError, ValueError):
            member = None

        if member is None:
            lines.append(f"{index}. 不明なメンバー")
            continue

        if member.id in excluded_ids:
            status = "🚫 除外中"
        elif member.id == data.get("current_member_id"):
            status = "📍 現在の担当"
        else:
            status = ""

        lines.append(f"{index}. {member.display_name} {status}".rstrip())

    await ctx.send("📋 担当順\n\n" + "\n".join(lines))


if not TOKEN:
    raise RuntimeError("DISCORD_TOKEN が設定されていません")

bot.run(TOKEN)
