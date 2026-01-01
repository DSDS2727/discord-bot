import os
import sys
import json
import time
import asyncio
from pathlib import Path
from datetime import datetime, timezone, timedelta

import discord
from discord import app_commands
from discord.ext import commands, tasks
from aiohttp import web

# ==========================================================
# ✅ [1. 기본 설정]
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
KST = timezone(timedelta(hours=9))

# 데이터 저장 경로
def _get_base_dir() -> Path:
    if getattr(sys, "frozen", False): return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent

DATA_FILE = _get_base_dir() / "stats.json"

def load_data():
    base = {
        "msg_count": {}, "voice_join_ts": {}, "voice_log": [],
        "reaction_roles": {}, "last_proxy_msg": {}, "temp_voice_channels": []
    }
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
# ✅ [2. 봇 메인 설정]
# ==========================================================
class MyBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.members = True
        intents.message_content = True
        intents.reactions = True
        intents.voice_states = True
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        # 헬스체크 서버 가동
        asyncio.create_task(self._start_server())
        # 슬래시 명령어 강제 동기화
        self.tree.copy_global_to(guild=MY_GUILD)
        await self.tree.sync(guild=MY_GUILD)

    async def _start_server(self):
        app = web.Application()
        app.router.add_get("/", lambda r: web.Response(text="Bot is Live"))
        runner = web.AppRunner(app); await runner.setup()
        await web.TCPSite(runner, host="0.0.0.0", port=PORT).start()

bot = MyBot()
pending_image_say = {} 
pending_post_create = {}

# ==========================================================
# ✅ [3. 명령어 통합]
# ==========================================================

# --- 역할패널 (이모지 반응 방식) ---
@bot.tree.command(name="역할패널", description="메시지에 반응을 달아 역할을 부여합니다.")
@app_commands.describe(role="역할", emoji="이모지 (움직이는 이모지 포함)")
async def cmd_role_panel(interaction: discord.Interaction, role: discord.Role, emoji: str):
    if not interaction.user.guild_permissions.administrator: return
    
    msg_id = data["last_proxy_msg"].get(str(interaction.channel.id))
    if not msg_id:
        return await interaction.response.send_message("❌ `/대신쓰기`를 먼저 해주세요.", ephemeral=True)

    try:
        msg = await interaction.channel.fetch_message(msg_id)
        await msg.add_reaction(emoji)
        
        mid_str = str(msg.id)
        if mid_str not in data["reaction_roles"]: data["reaction_roles"][mid_str] = {}
        data["reaction_roles"][mid_str][emoji] = role.id
        save_data(data)
        await interaction.response.send_message(f"✅ {role.name} 역할용 {emoji} 반응 추가 완료!", ephemeral=True)
    except:
        await interaction.response.send_message("❌ 이모지 추가 실패. (봇 권한이나 이모지 형식을 확인하세요)", ephemeral=True)

# --- 원본 기능: 이미지대신쓰기 ---
@bot.tree.command(name="이미지대신쓰기", description="이미지를 올리면 봇이 대신 전송합니다.")
async def cmd_image_proxy(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator: return
    pending_image_say[interaction.user.id] = interaction.channel
    await interaction.response.send_message("📷 이미지를 지금 업로드하세요.", ephemeral=True)

# --- 원본 기능: 환영 ---
@bot.tree.command(name="환영", description="환영 메시지 수동 테스트")
async def cmd_welcome(interaction: discord.Interaction):
    await interaction.response.send_message("👋 환영 기능 정상 작동 중!")

# --- 원본 기능: 음성통계 (정렬 포함) ---
@bot.tree.command(name="음성통계", description="전체 유저 음성 통계")
async def cmd_voice_stats(interaction: discord.Interaction):
    totals = {}
    for entry in data["voice_log"]:
        uid = entry["user_id"]; totals[uid] = totals.get(uid, 0) + entry["duration"]
    
    if not totals: return await interaction.response.send_message("기록 없음")
    
    sorted_stats = sorted(totals.items(), key=lambda x: x[1], reverse=True)
    desc = ""
    for i, (uid, dur) in enumerate(sorted_stats, 1):
        m = interaction.guild.get_member(int(uid))
        name = m.display_name if m else f"Unknown({uid})"
        desc += f"**{i}. {name}**: {dur//60}분\n"
    await interaction.response.send_message(embed=discord.Embed(title="📊 음성 통계", description=desc, color=0x3498db))

# --- 원본 기능: 포스트생성 ---
@bot.tree.command(name="포스트생성", description="포럼에 이미지 포스트 생성")
async def cmd_post_create(interaction: discord.Interaction, forum_channel: discord.ForumChannel, title: str, content: str):
    pending_post_create[interaction.user.id] = {"ch_id": forum_channel.id, "title": title, "content": content, "files": []}
    await interaction.response.send_message("📷 이미지를 올린 후 `!완료`를 입력하세요.", ephemeral=True)

@bot.tree.command(name="대신쓰기")
async def cmd_proxy_say(interaction: discord.Interaction, content: str):
    embed = discord.Embed(description=content, color=0x2ecc71)
    sent = await interaction.channel.send(embed=embed)
    data["last_proxy_msg"][str(interaction.channel.id)] = sent.id
    save_data(data)
    await interaction.response.send_message("✅ 전송 완료", ephemeral=True)

# ==========================================================
# ✅ [4. 이벤트 로직]
# ==========================================================

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot: return

    # 이미지 가로채기
    if message.author.id in pending_image_say and message.attachments:
        target = pending_image_say.pop(message.author.id)
        files = [await a.to_file() for a in message.attachments]
        sent = await target.send(files=files)
        data["last_proxy_msg"][str(target.id)] = sent.id
        save_data(data); await message.delete(); return

    # 포스트생성용 이미지 수집
    if message.author.id in pending_post_create and message.attachments:
        st = pending_post_create[message.author.id]
        for a in message.attachments: st["files"].append(await a.to_file())
        await message.delete(); return

    await bot.process_commands(message)

@bot.command(name="완료")
async def post_done(ctx):
    st = pending_post_create.pop(ctx.author.id, None)
    if st:
        ch = bot.get_channel(st["ch_id"])
        await ch.create_thread(name=st["title"], content=st["content"], files=st["files"])
        await ctx.send("✅ 포스트 생성 완료")

# 반응 추가/제거 시 역할 자동 부여 (상호작용 실패 없음)
@bot.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
    if payload.member.bot: return
    mid, emo = str(payload.message_id), str(payload.emoji)
    if mid in data["reaction_roles"] and emo in data["reaction_roles"][mid]:
        role = payload.member.guild.get_role(data["reaction_roles"][mid][emo])
        if role: await payload.member.add_roles(role)

@bot.event
async def on_raw_reaction_remove(payload: discord.RawReactionActionEvent):
    mid, emo = str(payload.message_id), str(payload.emoji)
    if mid in data["reaction_roles"] and emo in data["reaction_roles"][mid]:
        guild = bot.get_guild(payload.guild_id)
        member = guild.get_member(payload.user_id)
        role = guild.get_role(data["reaction_roles"][mid][emo])
        if role and member: await member.remove_roles(role)

@bot.event
async def on_member_join(member):
    ch = member.guild.get_channel(WELCOME_CHANNEL_ID)
    if ch: await ch.send(f"👋 {member.mention}님 환영합니다!")

@bot.event
async def on_voice_state_update(member, before, after):
    # 음성 기록 로직
    if before.channel is None and after.channel:
        data["voice_join_ts"][str(member.id)] = time.time()
    elif before.channel and after.channel is None:
        start = data["voice_join_ts"].pop(str(member.id), None)
        if start: data["voice_log"].append({"user_id": str(member.id), "duration": int(time.time()-start)})
    
    # 허브 채널
    if after.channel and after.channel.id == VOICE_HUB_CHANNEL_ID:
        new_ch = await member.guild.create_voice_channel(name=f"{member.display_name}의 방")
        data["temp_voice_channels"].append(new_ch.id)
        await member.move_to(new_ch)
    save_data(data)

@tasks.loop(seconds=20)
async def temp_voice_gc_loop():
    guild = bot.get_guild(GUILD_ID)
    if not guild: return
    for ch_id in list(data.get("temp_voice_channels", [])):
        ch = guild.get_channel(ch_id)
        if not ch or (isinstance(ch, discord.VoiceChannel) and not ch.members):
            try: await ch.delete(); data["temp_voice_channels"].remove(ch_id)
            except: pass
    save_data(data)

@bot.event
async def on_ready():
    if not temp_voice_gc_loop.is_running(): temp_voice_gc_loop.start()
    print(f"✅ {bot.user} 가동 중!")

bot.run(TOKEN)
