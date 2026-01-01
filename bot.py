import os
import sys
import json
import time
import asyncio
import traceback
from pathlib import Path
from typing import Optional, Dict, Any, List
from datetime import datetime, timezone, timedelta

import discord
from discord import app_commands
from discord.ext import commands, tasks
from aiohttp import web

# ==========================================================
# ✅ [설정] 서버 정보 및 채널 ID
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
# ✅ [데이터] 영구 저장을 위한 stats.json 관리
# ==========================================================
def _get_base_dir() -> Path:
    if getattr(sys, "frozen", False): return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent

BASE_DIR = _get_base_dir()
DATA_FILE = BASE_DIR / "stats.json"

def _default_data() -> Dict[str, Any]:
    return {
        "msg_count": {},
        "voice_join_ts": {},
        "voice_log": [],
        "last_monthly_reset": "",
        "reaction_roles": {}, # { "msg_id": { "role_id": {"label": "이름", "emoji": "이모지"} } }
        "last_proxy_message_id_by_channel": {},
        "last_forum_post_by_forum": {},
        "temp_voice_channels": []
    }

def load_data() -> Dict[str, Any]:
    base = _default_data()
    if not DATA_FILE.exists(): return base
    try:
        d = json.loads(DATA_FILE.read_text(encoding="utf-8"))
        for k, v in base.items(): d.setdefault(k, v)
        return d
    except: return base

def save_data(d: Dict[str, Any]) -> None:
    try:
        DATA_FILE.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    except: pass

data = load_data()

# ==========================================================
# ✅ [역할패널] 영구 유지 및 이모지 지원 UI 클래스
# ==========================================================
class RoleButton(discord.ui.Button):
    def __init__(self, role_id: int, label: str, emoji: str = None):
        # custom_id를 고정해야 봇이 꺼졌다 켜져도 이 버튼을 인식함
        super().__init__(style=discord.ButtonStyle.secondary, label=label, emoji=emoji, custom_id=f"persistent_role:{role_id}")
        self.role_id = role_id

    async def callback(self, interaction: discord.Interaction):
        role = interaction.guild.get_role(self.role_id)
        if not role:
            return await interaction.response.send_message("역할을 찾을 수 없습니다.", ephemeral=True)
        
        if role in interaction.user.roles:
            await interaction.user.remove_roles(role)
            await interaction.response.send_message(f"✅ **{role.name}** 역할이 제거되었습니다.", ephemeral=True)
        else:
            await interaction.user.add_roles(role)
            await interaction.response.send_message(f"✅ **{role.name}** 역할이 부여되었습니다.", ephemeral=True)

class RolePanelView(discord.ui.View):
    def __init__(self, roles_info: Dict[str, Dict[str, str]]):
        super().__init__(timeout=None) # timeout=None 이 중요
        for rid, info in roles_info.items():
            self.add_item(RoleButton(role_id=int(rid), label=info['label'], emoji=info.get('emoji')))

# ==========================================================
# ✅ [봇 메인] 초기 설정 및 영구 뷰 등록
# ==========================================================
class MyBot(commands.Bot):
    async def setup_hook(self):
        asyncio.create_task(self._start_server())
        # 저장된 모든 역할 패널 로드 (영구 유지의 핵심)
        for msg_id, roles in data.get("reaction_roles", {}).items():
            self.add_view(RolePanelView(roles))
        
        self.tree.clear_commands(guild=MY_GUILD)
        self.tree.copy_global_to(guild=MY_GUILD)
        await self.tree.sync(guild=MY_GUILD)

    async def _start_server(self):
        app = web.Application()
        app.router.add_get("/", lambda r: web.Response(text="ok"))
        runner = web.AppRunner(app); await runner.setup()
        await web.TCPSite(runner, host="0.0.0.0", port=PORT).start()

intents = discord.Intents.default()
intents.members = True
intents.message_content = True
intents.voice_states = True
bot = MyBot(command_prefix="!", intents=intents)

# 원본 기능용 상태 변수들
pending_image_say_as = {}
pending_post_create = {}

# ==========================================================
# ✅ [명령어] 모든 기능 포함 (기존 + 신규)
# ==========================================================

# 1. 역할패널 (개선됨: 영구유지, 최근 메시지 자동 타겟, 이모지 지원)
@bot.tree.command(name="역할패널", description="가장 최근 보낸 봇 메시지에 역할 버튼을 영구 추가합니다.")
@app_commands.describe(role="역할", label="버튼이름", emoji="이모지")
async def cmd_role_panel(interaction: discord.Interaction, role: discord.Role, label: str, emoji: str = None):
    if not interaction.user.guild_permissions.administrator: return
    
    msg_id = data.get("last_proxy_message_id_by_channel", {}).get(str(interaction.channel.id))
    if not msg_id:
        return await interaction.response.send_message("버튼을 부착할 봇 메시지가 없습니다. 먼저 `/대신쓰기`를 해주세요.", ephemeral=True)
    
    try:
        msg = await interaction.channel.fetch_message(msg_id)
    except:
        return await interaction.response.send_message("해당 메시지를 찾을 수 없습니다.", ephemeral=True)

    mid_str, rid_str = str(msg_id), str(role.id)
    data.setdefault("reaction_roles", {})
    data["reaction_roles"].setdefault(mid_str, {})
    data["reaction_roles"][mid_str][rid_str] = {"label": label, "emoji": emoji}
    save_data(data)
    
    view = RolePanelView(data["reaction_roles"][mid_str])
    await msg.edit(view=view)
    bot.add_view(view)
    await interaction.response.send_message(f"✅ '{label}' 버튼 추가 완료!", ephemeral=True)

# 2. 대신쓰기 (기존 기능 유지)
@bot.tree.command(name="대신쓰기")
async def cmd_proxy_say(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator: return
    class Modal(discord.ui.Modal, title="대신쓰기"):
        content = discord.ui.TextInput(label="내용", style=discord.TextStyle.paragraph)
        async def on_submit(self, it: discord.Interaction):
            sent = await it.channel.send(embed=discord.Embed(description=self.content.value, color=0x2ecc71))
            data.setdefault("last_proxy_message_id_by_channel", {})
            data["last_proxy_message_id_by_channel"][str(it.channel.id)] = sent.id
            save_data(data)
            await it.response.send_message("전송 완료 (이 메시지에 버튼을 달 수 있습니다)", ephemeral=True)
    await interaction.response.send_modal(Modal())

# 3. 음성통계 (기존 기능 유지: 모든 유저 출력)
@bot.tree.command(name="음성통계", description="이번 달 모든 유저의 음성 통계를 출력합니다.")
async def cmd_voice_stats(interaction: discord.Interaction):
    now = datetime.now(KST)
    totals = {}
    for entry in data["voice_log"]:
        uid = entry["user_id"]
        totals[uid] = totals.get(uid, 0) + entry["duration"]
    
    if not totals: return await interaction.response.send_message("기록된 데이터가 없습니다.")
    
    sorted_stats = sorted(totals.items(), key=lambda x: x[1], reverse=True)
    desc = ""
    for i, (uid, dur) in enumerate(sorted_stats, 1):
        m = interaction.guild.get_member(int(uid))
        name = m.display_name if m else f"Unknown({uid})"
        h, rem = divmod(dur, 3600); mi, sec = divmod(rem, 60)
        time_str = f"{h}시간 {mi}분" if h > 0 else f"{mi}분 {sec}초"
        desc += f"**{i}. {name}**: {time_str}\n"

    embed = discord.Embed(title=f"📊 {now.month}월 음성 통계 (전체)", description=desc, color=discord.Color.blue())
    await interaction.response.send_message(embed=embed)

# 4. 포스트생성 (기존 기능 유지: 이미지 수집 프로세스)
@bot.tree.command(name="포스트생성", description="포럼 채널에 이미지가 포함된 새 포스트를 만듭니다.")
@app_commands.describe(forum_channel="포럼 채널", title="제목", content="내용")
async def cmd_post_create(interaction: discord.Interaction, forum_channel: discord.ForumChannel, title: str, content: str):
    if not interaction.user.guild_permissions.administrator: return
    pending_post_create[interaction.user.id] = {"channel_id": forum_channel.id, "title": title, "content": content, "files": []}
    await interaction.response.send_message("📷 이미지를 채널에 업로드해주세요. 모두 올린 후 `!완료`를 입력하면 생성됩니다.", ephemeral=True)

@bot.command(name="완료")
async def post_done(ctx):
    st = pending_post_create.pop(ctx.author.id, None)
    if not st: return
    ch = bot.get_channel(st["channel_id"])
    await ch.create_thread(name=st["title"], content=st["content"], files=st.get("files", []))
    await ctx.send("✅ 포스트 생성이 완료되었습니다!", delete_after=5)

# ==========================================================
# ✅ [이벤트] 주요 이벤트 핸들러
# ==========================================================
@bot.event
async def on_message(message: discord.Message):
    if message.author.bot: return
    
    # 메시지 통계 카운트
    uid = str(message.author.id)
    data["msg_count"][uid] = data["msg_count"].get(uid, 0) + 1
    
    # 이미지 수집 (대신쓰기/포스트생성 전용)
    if message.author.id in pending_image_say_as and message.attachments:
        target = pending_image_say_as.pop(message.author.id)
        files = [await a.to_file() for a in message.attachments]
        sent = await target.send(files=files)
        data.setdefault("last_proxy_message_id_by_channel", {})
        data["last_proxy_message_id_by_channel"][str(target.id)] = sent.id
        save_data(data); await message.delete(); return

    if message.author.id in pending_post_create and message.attachments:
        st = pending_post_create[message.author.id]
        st.setdefault("files", [])
        for a in message.attachments:
            st["files"].append(await a.to_file())
        await message.delete(); return

    await bot.process_commands(message)

@bot.event
async def on_voice_state_update(member: discord.Member, before, after):
    uid = str(member.id)
    now = time.time()
    
    # 시간 기록
    if before.channel is None and after.channel: data["voice_join_ts"][uid] = now
    elif before.channel and after.channel is None:
        start = data["voice_join_ts"].pop(uid, None)
        if start: data["voice_log"].append({"user_id": uid, "duration": int(now - start), "ts": int(now)})
    
    # 허브 채널 (자동 생성)
    if after.channel and after.channel.id == VOICE_HUB_CHANNEL_ID:
        new_ch = await member.guild.create_voice_channel(name=f"{member.display_name}님의 방", category=after.channel.category)
        data.setdefault("temp_voice_channels", []).append(new_ch.id)
        await member.move_to(new_ch)
    save_data(data)

@bot.event
async def on_member_update(before, after):
    # 부스트 알림
    if before.premium_since is None and after.premium_since is not None:
        ch = after.guild.get_channel(BOOST_THANKS_CHANNEL_ID)
        if ch:
            embed = discord.Embed(title="서버 부스트!", description=f"{after.mention}님, 부스트 감사합니다! 💎", color=0x9b59b6)
            embed.set_image(url=BOOST_THANKS_IMAGE_URL)
            await ch.send(embed=embed)

@bot.event
async def on_message_delete(message):
    if message.author.bot: return
    ch = message.guild.get_channel(LOG_CHANNEL_ID)
    if ch:
        embed = discord.Embed(title="메시지 삭제", description=f"채널: {message.channel.mention}\n작성자: {message.author}\n내용: {message.content or '이미지/없음'}", color=0xffa500)
        await ch.send(embed=embed)

# ==========================================================
# ✅ [루프] 주기적 관리
# ==========================================================
@tasks.loop(minutes=1)
async def monthly_reset_loop():
    now = datetime.now(KST)
    if now.day == 1 and now.hour == 0 and now.minute == 0:
        data["voice_log"] = []; data["voice_join_ts"] = {}; save_data(data)

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
    if not monthly_reset_loop.is_running(): monthly_reset_loop.start()
    if not temp_voice_gc_loop.is_running(): temp_voice_gc_loop.start()

bot.run(TOKEN)
