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
# ✅ [1. 설정] 서버 정보 및 채널 ID (정확히 유지)
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

# ==========================================================
# ✅ [2. 데이터] 영구 저장 (봇 재시작 시에도 데이터 유지)
# ==========================================================
def _get_base_dir() -> Path:
    if getattr(sys, "frozen", False): return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent

DATA_FILE = _get_base_dir() / "stats.json"

def load_data():
    base = {
        "msg_count": {}, "voice_join_ts": {}, "voice_log": [],
        "reaction_roles": {}, "last_proxy_msg": {}, "temp_voice_channels": [],
        "last_monthly_reset": ""
    }
    if not DATA_FILE.exists(): return base
    try:
        d = json.loads(DATA_FILE.read_text(encoding="utf-8"))
        for k, v in base.items(): d.setdefault(k, v)
        return d
    except: return base

def save_data(d):
    try:
        DATA_FILE.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    except: pass

data = load_data()

# ==========================================================
# ✅ [3. 봇 클래스]
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
        asyncio.create_task(self._start_server())
        await self.tree.sync(guild=MY_GUILD)

    async def _start_server(self):
        app = web.Application()
        app.router.add_get("/", lambda r: web.Response(text="Bot Alive"))
        runner = web.AppRunner(app); await runner.setup()
        await web.TCPSite(runner, host="0.0.0.0", port=PORT).start()

bot = MyBot()
pending_image_say = {} 
pending_post_create = {}

# ==========================================================
# ✅ [4. 모든 명령어 통합]
# ==========================================================

# --- [A] 관리 기능: 대신쓰기 & 이미지 대신쓰기 ---
@bot.tree.command(name="대신쓰기", description="봇이 임베드로 메시지를 보냅니다.")
async def cmd_proxy_say(interaction: discord.Interaction, content: str):
    if not interaction.user.guild_permissions.administrator: return
    embed = discord.Embed(description=content, color=0x2ecc71)
    sent = await interaction.channel.send(embed=embed)
    data["last_proxy_msg"][str(interaction.channel.id)] = sent.id
    save_data(data)
    await interaction.response.send_message("✅ 메시지 전송 완료!", ephemeral=True)

@bot.tree.command(name="이미지대신쓰기", description="명령어 후 이미지를 올리면 봇이 가로채서 대신 올립니다.")
async def cmd_image_proxy(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator: return
    pending_image_say[interaction.user.id] = interaction.channel
    await interaction.response.send_message("📷 지금 이미지를 올려주세요. 봇이 삭제 후 다시 올립니다.", ephemeral=True)

# --- [B] 역할 패널: 이모지 반응형 (요청하신 이미지 방식) ---
@bot.tree.command(name="역할패널", description="마지막 봇 메시지에 반응(이모지)형 역할 부여를 추가합니다.")
@app_commands.describe(role="부여할 역할", emoji="사용할 이모지 (채팅창 이모지 그대로 입력)")
async def cmd_role_panel(interaction: discord.Interaction, role: discord.Role, emoji: str):
    if not interaction.user.guild_permissions.administrator: return
    
    msg_id = data["last_proxy_msg"].get(str(interaction.channel.id))
    if not msg_id:
        return await interaction.response.send_message("❌ 먼저 `/대신쓰기` 또는 `/이미지대신쓰기`를 해주세요.", ephemeral=True)

    try:
        msg = await interaction.channel.fetch_message(msg_id)
        await msg.add_reaction(emoji) # 메시지에 직접 이모지 추가
        
        mid_str = str(msg.id)
        if mid_str not in data["reaction_roles"]: data["reaction_roles"][mid_str] = {}
        data["reaction_roles"][mid_str][emoji] = role.id
        save_data(data)
        await interaction.response.send_message(f"✅ {role.name} 역할에 대한 {emoji} 반응이 추가되었습니다.", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"❌ 오류: 이모지를 추가할 수 없습니다. ({e})", ephemeral=True)

# --- [C] 유틸리티: 음성 통계 & 환영 ---
@bot.tree.command(name="음성통계", description="이번 달 모든 유저의 음성 통계를 정렬하여 보여줍니다.")
async def cmd_voice_stats(interaction: discord.Interaction):
    totals = {}
    for entry in data["voice_log"]:
        uid = entry["user_id"]
        totals[uid] = totals.get(uid, 0) + entry["duration"]
    
    if not totals: return await interaction.response.send_message("기록된 음성 데이터가 없습니다.")
    
    sorted_stats = sorted(totals.items(), key=lambda x: x[1], reverse=True)
    desc = ""
    for i, (uid, dur) in enumerate(sorted_stats, 1):
        member = interaction.guild.get_member(int(uid))
        name = member.display_name if member else f"탈퇴유저({uid})"
        h, m = divmod(dur // 60, 60)
        desc += f"**{i}. {name}**: {h}시간 {m}분\n"

    await interaction.response.send_message(embed=discord.Embed(title="📊 이번 달 전체 음성 통계", description=desc, color=0x3498db))

@bot.tree.command(name="환영", description="환영 메시지를 테스트합니다.")
async def cmd_welcome(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator: return
    await interaction.response.send_message(f"👋 {interaction.user.mention}님, 서버 환영 메시지 테스트 완료!")

# --- [D] 포스트 생성 ---
@bot.tree.command(name="포스트생성", description="포럼 채널에 이미지가 포함된 포스트를 생성합니다.")
async def cmd_post_create(interaction: discord.Interaction, forum_channel: discord.ForumChannel, title: str, content: str):
    if not interaction.user.guild_permissions.administrator: return
    pending_post_create[interaction.user.id] = {"ch_id": forum_channel.id, "title": title, "content": content, "files": []}
    await interaction.response.send_message("📷 이미지를 채널에 올리고 `!완료`를 입력하세요.", ephemeral=True)

@bot.command(name="완료")
async def post_done(ctx):
    st = pending_post_create.pop(ctx.author.id, None)
    if st:
        ch = bot.get_channel(st["ch_id"])
        await ch.create_thread(name=st["title"], content=st["content"], files=st["files"])
        await ctx.send("✅ 포스트 생성이 완료되었습니다!", delete_after=5)

# ==========================================================
# ✅ [5. 이벤트 핸들러 및 자동화 로직]
# ==========================================================

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot: return

    # 1. 이미지 가로채기 (이미지 대신쓰기)
    if message.author.id in pending_image_say and message.attachments:
        target_ch = pending_image_say.pop(message.author.id)
        files = [await a.to_file() for a in message.attachments]
        sent = await target_ch.send(files=files)
        data["last_proxy_msg"][str(target_ch.id)] = sent.id # 나중에 이 메시지에 역할패널 가능
        save_data(data); await message.delete(); return

    # 2. 포스트 생성용 이미지 수집
    if message.author.id in pending_post_create and message.attachments:
        st = pending_post_create[message.author.id]
        for a in message.attachments: st["files"].append(await a.to_file())
        await message.delete(); return

    await bot.process_commands(message)

# 3. 반응 추가/제거 시 역할 자동 부여 (상호작용 실패 없음)
@bot.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
    if payload.member.bot: return
    mid_str, emo = str(payload.message_id), str(payload.emoji)
    if mid_str in data["reaction_roles"] and emo in data["reaction_roles"][mid_str]:
        role = payload.member.guild.get_role(data["reaction_roles"][mid_str][emo])
        if role: await payload.member.add_roles(role)

@bot.event
async def on_raw_reaction_remove(payload: discord.RawReactionActionEvent):
    mid_str, emo = str(payload.message_id), str(payload.emoji)
    if mid_str in data["reaction_roles"] and emo in data["reaction_roles"][mid_str]:
        guild = bot.get_guild(payload.guild_id)
        member = guild.get_member(payload.user_id)
        role = guild.get_role(data["reaction_roles"][mid_str][emo])
        if role and member: await member.remove_roles(role)

# 4. 기타 원본 기능 (환영, 음성 기록, 부스트, 로그)
@bot.event
async def on_member_join(member):
    ch = member.guild.get_channel(WELCOME_CHANNEL_ID)
    if ch: await ch.send(f"👋 {member.mention}님, 우리 서버에 오신 것을 환영합니다!")

@bot.event
async def on_voice_state_update(member, before, after):
    if before.channel is None and after.channel:
        data["voice_join_ts"][str(member.id)] = time.time()
    elif before.channel and after.channel is None:
        start = data["voice_join_ts"].pop(str(member.id), None)
        if start:
            data["voice_log"].append({"user_id": str(member.id), "duration": int(time.time()-start)})
    
    if after.channel and after.channel.id == VOICE_HUB_CHANNEL_ID:
        new_ch = await member.guild.create_voice_channel(name=f"{member.display_name}의 통화방", category=after.channel.category)
        data["temp_voice_channels"].append(new_ch.id)
        await member.move_to(new_ch)
    save_data(data)

@bot.event
async def on_member_update(before, after):
    if before.premium_since is None and after.premium_since is not None:
        ch = after.guild.get_channel(BOOST_THANKS_CHANNEL_ID)
        if ch:
            embed = discord.Embed(title="💎 서버 부스트 감사!", description=f"{after.mention}님, 부스트 해주셔서 감사합니다!", color=0x9b59b6)
            embed.set_image(url=BOOST_THANKS_IMAGE_URL)
            await ch.send(embed=embed)

@bot.event
async def on_message_delete(message):
    if message.author.bot: return
    ch = message.guild.get_channel(LOG_CHANNEL_ID)
    if ch:
        embed = discord.Embed(title="🗑️ 메시지 삭제 기록", description=f"**채널**: {message.channel.mention}\n**작성자**: {message.author}\n**내용**: {message.content or '이미지/없음'}", color=0xff0000)
        await ch.send(embed=embed)

# ==========================================================
# ✅ [6. 주기적 루프]
# ==========================================================
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
