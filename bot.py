import os
import sys
import json
import time
import asyncio
from pathlib import Path
from datetime import datetime, timezone, timedelta

import discord
from discord import app_commands, ui
from discord.ext import commands, tasks
from aiohttp import web

# ==========================================================
# ✅ [1. 설정 및 데이터 관리]
# ==========================================================
GUILD_ID = 1450940849184571578
MY_GUILD = discord.Object(id=GUILD_ID)
WELCOME_CHANNEL_ID = 1451263656938705077
LOG_CHANNEL_ID = 1453133491213438977
VOICE_HUB_CHANNEL_ID = 1454682297285611751
TOKEN = os.getenv("TOKEN")
PORT = int(os.getenv("PORT", "8000"))

def _get_base_dir() -> Path:
    if getattr(sys, "frozen", False): return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent

DATA_FILE = _get_base_dir() / "stats.json"

def load_data():
    base = {"msg_count": {}, "voice_join_ts": {}, "voice_log": [], "reaction_roles": {}, "temp_voice_channels": []}
    if not DATA_FILE.exists(): return base
    try:
        d = json.loads(DATA_FILE.read_text(encoding="utf-8"))
        for k, v in base.items(): d.setdefault(k, v)
        return d
    except: return base

def save_data(d):
    try: DATA_FILE.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    except: pass

data = load_data()

# ==========================================================
# ✅ [2. 입력창(Modal) 정의 - 원본 방식 복구]
# ==========================================================

class ProxySayModal(ui.Modal, title='대신 쓰기'):
    content = ui.TextInput(label='내용', style=discord.TextStyle.paragraph, placeholder='내용을 입력하세요.', required=True)
    image_url = ui.TextInput(label='이미지 URL (선택)', placeholder='https://...', required=False)

    async def on_submit(self, interaction: discord.Interaction):
        embed = discord.Embed(description=self.content.value, color=0x2ecc71)
        if self.image_url.value: embed.set_image(url=self.image_url.value)
        await interaction.channel.send(embed=embed)
        await interaction.response.send_message("✅ 전송 완료", ephemeral=True)

class ForumPostModal(ui.Modal, title='포럼 포스트 생성'):
    def __init__(self, channel):
        super().__init__()
        self.channel = channel
    post_title = ui.TextInput(label='제목', placeholder='제목을 입력하세요.', required=True)
    post_content = ui.TextInput(label='내용', style=discord.TextStyle.paragraph, placeholder='내용을 입력하세요.', required=True)

    async def on_submit(self, interaction: discord.Interaction):
        await self.channel.create_thread(name=self.post_title.value, content=self.post_content.value)
        await interaction.response.send_message(f"✅ {self.channel.mention}에 포스트 생성 완료", ephemeral=True)

# ==========================================================
# ✅ [3. 봇 클래스 및 주요 명령어]
# ==========================================================
class MyBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.all()
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        asyncio.create_task(self._start_server())
        self.tree.copy_global_to(guild=MY_GUILD)
        await self.tree.sync(guild=MY_GUILD)

    async def _start_server(self):
        app = web.Application()
        app.router.add_get("/", lambda r: web.Response(text="Bot is running"))
        runner = web.AppRunner(app); await runner.setup()
        await web.TCPSite(runner, host="0.0.0.0", port=PORT).start()

bot = MyBot()
pending_image_say = {}

@bot.tree.command(name="대신쓰기", description="입력창을 통해 메시지를 작성합니다.")
async def proxy_say(interaction: discord.Interaction):
    await interaction.response.send_modal(ProxySayModal())

@bot.tree.command(name="포스트생성", description="입력창을 통해 포럼 포스트를 생성합니다.")
async def post_create(interaction: discord.Interaction, forum_channel: discord.ForumChannel):
    await interaction.response.send_modal(ForumPostModal(forum_channel))

@bot.tree.command(name="이미지대신쓰기", description="이미지 가로채기 기능을 활성화합니다.")
async def image_proxy(interaction: discord.Interaction):
    pending_image_say[interaction.user.id] = interaction.channel
    await interaction.response.send_message("📷 이미지를 업로드하면 봇이 가로채서 대신 올립니다.", ephemeral=True)

@bot.tree.command(name="역할패널", description="가장 최근 메시지에 역할 반응을 추가합니다.")
@app_commands.describe(role="부여할 역할", emoji="반응 이모지")
async def role_panel(interaction: discord.Interaction, role: discord.Role, emoji: str):
    await interaction.response.defer(ephemeral=True)
    try:
        # 채널 내 기한 제한 없이 가장 최근 메시지 1개를 가져옴
        async for message in interaction.channel.history(limit=1):
            await message.add_reaction(emoji)
            mid_str = str(message.id)
            if mid_str not in data["reaction_roles"]: data["reaction_roles"][mid_str] = {}
            data["reaction_roles"][mid_str][emoji] = role.id
            save_data(data)
            return await interaction.followup.send(f"✅ [이동하기]({message.jump_url}) 메시지에 {role.mention} 역할 부여 설정 완료!")
        await interaction.followup.send("❌ 이 채널에 메시지가 존재하지 않습니다.")
    except Exception as e:
        await interaction.followup.send(f"❌ 오류 발생: {e}")

@bot.tree.command(name="음성통계", description="음성 통계 순위를 확인합니다.")
async def voice_stats(interaction: discord.Interaction):
    totals = {}
    for entry in data["voice_log"]:
        uid = entry["user_id"]; totals[uid] = totals.get(uid, 0) + entry["duration"]
    
    if not totals: return await interaction.response.send_message("📊 데이터가 없습니다.")
    
    sorted_stats = sorted(totals.items(), key=lambda x: x[1], reverse=True)
    desc = ""
    for i, (uid, dur) in enumerate(sorted_stats, 1):
        m = interaction.guild.get_member(int(uid))
        name = m.display_name if m else f"퇴장유저({uid})"
        minutes, seconds = divmod(dur, 60)
        # 두 번째 사진의 "1. 이름 \n : 0분 4초" 형식 복구
        desc += f"**{i}. {name}**\n: {minutes}분 {seconds}초\n"
    
    embed = discord.Embed(title="📊 1월 음성 통계 (전체)", description=desc, color=0x3498db)
    await interaction.response.send_message(embed=embed)

# ==========================================================
# ✅ [4. 이벤트 및 자동화 로직]
# ==========================================================

@bot.event
async def on_member_join(member):
    # 첫 번째 사진처럼 유저 태그와 함께 자동 환영 메시지 전송
    ch = member.guild.get_channel(WELCOME_CHANNEL_ID)
    if ch:
        await ch.send(f"환영해요 {member.mention} 새로 오신분께 다들 인사 부탁드려요!!")

@bot.event
async def on_message(message):
    if message.author.bot: return
    # 이미지 대신쓰기 가로채기 로직
    if message.author.id in pending_image_say and message.attachments:
        target = pending_image_say.pop(message.author.id)
        files = [await a.to_file() for a in message.attachments]
        await target.send(files=files)
        await message.delete()

@bot.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
    # 역할 부여 로직 (작동 안 하던 문제 해결)
    mid, emo = str(payload.message_id), str(payload.emoji)
    if mid in data["reaction_roles"] and emo in data["reaction_roles"][mid]:
        guild = bot.get_guild(payload.guild_id)
        member = guild.get_member(payload.user_id)
        if member and not member.bot:
            role = guild.get_role(data["reaction_roles"][mid][emo])
            if role: await member.add_roles(role)

@bot.event
async def on_raw_reaction_remove(payload: discord.RawReactionActionEvent):
    # 역할 제거 로직
    mid, emo = str(payload.message_id), str(payload.emoji)
    if mid in data["reaction_roles"] and emo in data["reaction_roles"][mid]:
        guild = bot.get_guild(payload.guild_id)
        member = guild.get_member(payload.user_id)
        if member:
            role = guild.get_role(data["reaction_roles"][mid][emo])
            if role: await member.remove_roles(role)

@bot.event
async def on_voice_state_update(member, before, after):
    # 통화 기록 로직
    if before.channel is None and after.channel:
        data["voice_join_ts"][str(member.id)] = time.time()
    elif before.channel and after.channel is None:
        start = data["voice_join_ts"].pop(str(member.id), None)
        if start:
            data["voice_log"].append({"user_id": str(member.id), "duration": int(time.time()-start)})
    
    # 임시 통화방 생성 및 자동 제거 (허브 채널)
    if after.channel and after.channel.id == VOICE_HUB_CHANNEL_ID:
        new_ch = await member.guild.create_voice_channel(name=f"{member.display_name}의 방")
        data["temp_voice_channels"].append(new_ch.id)
        await member.move_to(new_ch)
    save_data(data)

@tasks.loop(seconds=20)
async def temp_voice_gc():
    guild = bot.get_guild(GUILD_ID)
    if not guild: return
    for ch_id in list(data["temp_voice_channels"]):
        ch = guild.get_channel(ch_id)
        if not ch or (isinstance(ch, discord.VoiceChannel) and not ch.members):
            try:
                await ch.delete()
                data["temp_voice_channels"].remove(ch_id)
            except: pass
    save_data(data)

@bot.event
async def on_ready():
    if not temp_voice_gc.is_running(): temp_voice_gc.start()
    print(f"✅ {bot.user} 가동 중!")

bot.run(TOKEN)
