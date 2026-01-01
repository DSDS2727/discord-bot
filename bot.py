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
BOOST_THANKS_CHANNEL_ID = 1454698715435761738
BOOST_THANKS_IMAGE_URL = "https://cdn.discordapp.com/emojis/1452721803431772190.webp?size=96&animated=true"

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
# ✅ [2. 모달(입력창) - 기존 기능 유지]
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
# ✅ [3. 봇 클래스]
# ==========================================================
class MyBot(commands.Bot):
    def __init__(self):
        # Intents 필수 설정 (모든 권한 허용)
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

# ==========================================================
# ✅ [4. 명령어]
# ==========================================================

@bot.tree.command(name="대신쓰기", description="입력창을 열어 메시지를 작성합니다.")
async def proxy_say(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator: return
    await interaction.response.send_modal(ProxySayModal())

@bot.tree.command(name="포스트생성", description="입력창을 통해 포럼 포스트를 생성합니다.")
async def post_create(interaction: discord.Interaction, forum_channel: discord.ForumChannel):
    if not interaction.user.guild_permissions.administrator: return
    await interaction.response.send_modal(ForumPostModal(forum_channel))

@bot.tree.command(name="이미지대신쓰기", description="이미지 가로채기 기능을 활성화합니다.")
async def image_proxy(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator: return
    pending_image_say[interaction.user.id] = interaction.channel
    await interaction.response.send_message("📷 이미지를 업로드하면 봇이 가로채서 대신 올립니다.", ephemeral=True)

@bot.tree.command(name="역할패널", description="채널의 가장 최근 메시지에 역할 반응을 추가합니다.")
@app_commands.describe(role="부여할 역할", emoji="반응 이모지")
async def role_panel(interaction: discord.Interaction, role: discord.Role, emoji: str):
    if not interaction.user.guild_permissions.administrator: return
    await interaction.response.defer(ephemeral=True)
    try:
        # 무제한 기한으로 가장 최근 메시지 찾기
        async for message in interaction.channel.history(limit=1):
            await message.add_reaction(emoji)
            mid_str = str(message.id)
            if mid_str not in data["reaction_roles"]: data["reaction_roles"][mid_str] = {}
            # 역할 ID를 정수형(int)으로 확실하게 저장
            data["reaction_roles"][mid_str][emoji] = role.id
            save_data(data)
            return await interaction.followup.send(f"✅ 설정 완료! {message.jump_url} 에 {role.mention} ({emoji}) 추가됨.")
        await interaction.followup.send("❌ 메시지를 찾을 수 없습니다.")
    except Exception as e:
        await interaction.followup.send(f"❌ 오류: {e}")

@bot.tree.command(name="음성통계", description="음성 통계 순위를 확인합니다.")
async def voice_stats(interaction: discord.Interaction):
    totals = {}
    for entry in data["voice_log"]:
        uid = entry["user_id"]; totals[uid] = totals.get(uid, 0) + entry["duration"]
    
    if not totals: return await interaction.response.send_message("📊 기록이 없습니다.")
    
    sorted_stats = sorted(totals.items(), key=lambda x: x[1], reverse=True)
    desc = ""
    for i, (uid, dur) in enumerate(sorted_stats, 1):
        m = interaction.guild.get_member(int(uid))
        name = m.display_name if m else f"Unknown({uid})"
        minutes, seconds = divmod(dur, 60)
        desc += f"**{i}. {name}**\n: {minutes}분 {seconds}초\n"
    
    embed = discord.Embed(title="📊 음성 통계 (전체)", description=desc, color=0x3498db)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="환영", description="환영 메시지 테스트용")
async def welcome_test(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator: return
    # 수정됨: 태그(@)가 확실히 되도록 mention 속성 사용
    await interaction.response.send_message(f"환영해요 {interaction.user.mention} 새로 오신분께 다들 인사 부탁드려요!!")

# ==========================================================
# ✅ [5. 이벤트 핸들러]
# ==========================================================

# 1. 자동 환영 메시지 (수정됨: 태그 적용)
@bot.event
async def on_member_join(member):
    ch = member.guild.get_channel(WELCOME_CHANNEL_ID)
    if ch:
        # 수정됨: member.mention은 <@유저ID>로 변환되어 알람이 갑니다.
        await ch.send(f"환영해요 {member.mention} 새로 오신분께 다들 인사 부탁드려요!!")

# 2. 역할 부여 로직 (수정됨: 데이터 타입 문제 해결)
@bot.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
    if payload.member.bot: return
    mid, emo = str(payload.message_id), str(payload.emoji)
    
    if mid in data["reaction_roles"] and emo in data["reaction_roles"][mid]:
        guild = bot.get_guild(payload.guild_id)
        # 저장된 ID를 반드시 int로 변환하여 역할 찾기
        role_id = int(data["reaction_roles"][mid][emo])
        role = guild.get_role(role_id)
        if role:
            try:
                await payload.member.add_roles(role)
            except discord.Forbidden:
                print(f"권한 부족: 봇의 역할이 {role.name} 보다 위에 있어야 합니다.")

@bot.event
async def on_raw_reaction_remove(payload: discord.RawReactionActionEvent):
    mid, emo = str(payload.message_id), str(payload.emoji)
    
    if mid in data["reaction_roles"] and emo in data["reaction_roles"][mid]:
        guild = bot.get_guild(payload.guild_id)
        role_id = int(data["reaction_roles"][mid][emo])
        role = guild.get_role(role_id)
        member = guild.get_member(payload.user_id)
        if role and member:
            try:
                await member.remove_roles(role)
            except: pass

# 3. 이미지 가로채기
@bot.event
async def on_message(message):
    if message.author.bot: return
    if message.author.id in pending_image_say and message.attachments:
        target = pending_image_say.pop(message.author.id)
        files = [await a.to_file() for a in message.attachments]
        await target.send(files=files)
        await message.delete()

# 4. 메시지 삭제 로그 (수정됨: 서버 프로필 이름 사용)
@bot.event
async def on_message_delete(message):
    if message.author.bot: return
    ch = bot.get_channel(LOG_CHANNEL_ID)
    if ch:
        # 수정됨: author.display_name은 서버 별명(없으면 닉네임)을 가져옵니다.
        embed = discord.Embed(
            title="메시지 삭제", 
            description=f"채널: {message.channel.mention}\n작성자: {message.author.display_name}\n내용: {message.content}", 
            color=0xff0000
        )
        await ch.send(embed=embed)

@bot.event
async def on_member_update(before, after):
    # 부스트 감사
    if before.premium_since is None and after.premium_since is not None:
        ch = after.guild.get_channel(BOOST_THANKS_CHANNEL_ID)
        if ch:
            embed = discord.Embed(title="서버 부스트!", description=f"{after.mention}님 감사합니다!", color=0x9b59b6)
            embed.set_image(url=BOOST_THANKS_IMAGE_URL)
            await ch.send(embed=embed)

# 5. 음성 채널 로직 (이름 변경돼도 삭제됨)
@bot.event
async def on_voice_state_update(member, before, after):
    # 통계 기록
    if before.channel is None and after.channel:
        data["voice_join_ts"][str(member.id)] = time.time()
    elif before.channel and after.channel is None:
        start = data["voice_join_ts"].pop(str(member.id), None)
        if start: data["voice_log"].append({"user_id": str(member.id), "duration": int(time.time()-start)})
    
    # 임시방 생성 (카테고리 유지)
    if after.channel and after.channel.id == VOICE_HUB_CHANNEL_ID:
        category = after.channel.category 
        new_ch = await member.guild.create_voice_channel(
            name=f"{member.display_name}의 방", 
            category=category 
        )
        # 여기서 생성된 채널의 고유 ID를 저장하므로, 나중에 이름을 바꿔도 ID는 변하지 않아 삭제 로직이 작동합니다.
        data["temp_voice_channels"].append(new_ch.id)
        await member.move_to(new_ch)
    save_data(data)

# 6. 음성방 청소 루프
@tasks.loop(seconds=20)
async def temp_voice_gc():
    guild = bot.get_guild(GUILD_ID)
    if not guild: return
    # 저장된 ID 리스트를 기반으로 검사합니다.
    for ch_id in list(data["temp_voice_channels"]):
        ch = guild.get_channel(ch_id) # 이름이 바뀌어도 ID로 채널을 찾습니다.
        
        # 채널이 아예 삭제되었거나(None), 존재하는데 멤버가 0명이면 삭제
        if not ch:
            data["temp_voice_channels"].remove(ch_id)
        elif isinstance(ch, discord.VoiceChannel) and not ch.members:
            try: 
                await ch.delete()
                data["temp_voice_channels"].remove(ch_id)
            except: 
                pass
    save_data(data)

@bot.event
async def on_ready():
    if not temp_voice_gc.is_running(): temp_voice_gc.start()
    print(f"✅ {bot.user} 가동 완료.")

bot.run(TOKEN)
