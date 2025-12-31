# bot.py  (discord.py 2.6+ 기준)
# ----------------------------------------------------------
# 포함 기능(요청한 것 전부 유지 + 누락 최소화):
# - 길드 슬래시 즉시 등록(길드 전용 sync)
# - stats.json 저장(안정적으로)
# - 메시지 카운트
# - 음성 체류 통계(이번달 + 현재 접속중 포함) /음성통계
# - 월초(1일 00:00 KST) 자동 초기화
# - 허브 음성채널 입장 -> 개인 통화방 생성(허브 채널의 카테고리로 생성)
# - 통화방 0명 되면 즉시 삭제 + 2초/8초 재시도 + 20초마다 GC 청소(가끔 남는 문제 해결)
# - /대신쓰기(관리자) : 모달로 글 대신 전송(포럼이면 스레드 생성)
# - /이미지대신쓰기(관리자) : 다음 첨부 1회 봇이 대신 올리고 원본 삭제
# - /환영(관리자) : 자동 문구로 환영 채널에 전송(추가 입력 없음)
# - /포럼생성(관리자) : REST로 포럼 채널 생성(이름만)
# - /포스트생성(관리자) : 이미지 먼저 업로드(원본 자동삭제) -> 버튼 -> 모달(Shift+Enter 가능)
#   * 포스트에 이미지가 "맨 위"에 오게 하려면: 첫 글(Starter message)에 files를 붙여야 하는데
#     라이브러리/버전에 따라 create_thread(files=) 지원이 다릅니다.
#     그래서: (가능하면) create_thread(files=)로 첫 글에 파일 붙이고,
#            안되면 첫 글 " "로 만들고 스레드 첫 메시지로 파일을 바로 전송(그 다음 본문)
# - /역할패널(관리자) : 마지막 대신쓰기/포스트생성 메시지에 이모지->역할 연결(포럼에서도 동작)
# - 리액션 역할 지급/회수(on_raw_reaction_add/remove)
# - 메시지 삭제 로그(on_message_delete) + "채팅 채널로 이동" 버튼
# - 서버 부스트 감지 -> 감사 채널에 embed+이미지 자동 전송
# ----------------------------------------------------------

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

# ==========================================================
# ✅ 서버 설정 (네 ID)
# ==========================================================
GUILD_ID = 1450940849184571578
MY_GUILD = discord.Object(id=GUILD_ID)

WELCOME_CHANNEL_ID = 1451263656938705077
LOG_CHANNEL_ID = 1453133491213438977

# 🔥 허브 음성채널 ID (여기 채널로 들어가면 개인 통화방 생성)
VOICE_HUB_CHANNEL_ID = 1454682297285611751

# ✅ 부스트 감사 메시지를 보낼 채널 ID
BOOST_THANKS_CHANNEL_ID = 1454698715435761738

# ✅ 감사 메시지에 넣을 이미지 URL
BOOST_THANKS_IMAGE_URL = "https://cdn.discordapp.com/emojis/1452721803431772190.webp?size=96&animated=true"

# ==========================================================
# ✅ 토큰
# ==========================================================
TOKEN = os.getenv("DISCORD_TOKEN")

# ==========================================================
# ✅ 타임존(KST)
# ==========================================================
KST = timezone(timedelta(hours=9))

# ==========================================================
# ✅ 데이터 파일 (stats.json)
# ==========================================================
def _get_base_dir() -> Path:
    # exe로 패키징 되었을 때도 동작하도록
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


BASE_DIR = _get_base_dir()
BASE_DIR.mkdir(parents=True, exist_ok=True)
DATA_FILE = BASE_DIR / "stats.json"


def _default_data() -> Dict[str, Any]:
    return {
        "msg_count": {},
        "voice_join_ts": {},   # {user_id: ts}
        "voice_log": [],       # [{user_id,duration,ts}]
        "last_monthly_reset": "",
        "reaction_roles": {},  # {message_id: {emoji_str: role_id}}
        "last_proxy_message_id_by_channel": {},  # {channel_id: message_id}
        "last_forum_post_by_forum": {},          # {forum_id: {"thread_id": int, "message_id": int}}
        "temp_voice_channels": []                # [voice_channel_id, ...]  (우리가 만든 통화방 기록)
    }


def load_data() -> Dict[str, Any]:
    base = _default_data()

    if not DATA_FILE.exists():
        try:
            DATA_FILE.write_text(json.dumps(base, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass
        return base

    try:
        d = json.loads(DATA_FILE.read_text(encoding="utf-8"))
        for k, v in base.items():
            d.setdefault(k, v)

        if not isinstance(d.get("reaction_roles"), dict):
            d["reaction_roles"] = {}
        if not isinstance(d.get("voice_log"), list):
            d["voice_log"] = []
        if not isinstance(d.get("last_proxy_message_id_by_channel"), dict):
            d["last_proxy_message_id_by_channel"] = {}
        if not isinstance(d.get("voice_join_ts"), dict):
            d["voice_join_ts"] = {}
        if not isinstance(d.get("msg_count"), dict):
            d["msg_count"] = {}
        if not isinstance(d.get("last_forum_post_by_forum"), dict):
            d["last_forum_post_by_forum"] = {}
        if not isinstance(d.get("temp_voice_channels"), list):
            d["temp_voice_channels"] = []
        return d
    except Exception:
        return base


def save_data(d: Dict[str, Any]) -> None:
    # tmp -> replace 방식으로 최대한 안전하게
    text = json.dumps(d, ensure_ascii=False, indent=2)
    tmp = DATA_FILE.with_suffix(".tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(str(tmp), str(DATA_FILE))
    except Exception:
        try:
            if tmp.exists():
                tmp.unlink()
        except Exception:
            pass


data = load_data()

# ==========================================================
# ✅ intents
# ==========================================================
intents = discord.Intents.default()
intents.guilds = True
intents.members = True
intents.message_content = True
intents.reactions = True
intents.voice_states = True

# /이미지대신쓰기
pending_image_say_as: Dict[int, discord.abc.Messageable] = {}

# /포스트생성 이미지 첨부 대기
# user_id -> {"forum_id": int, "channel_id": int, "files": List[discord.File], "created_at": float}
pending_post_create: Dict[int, Dict[str, Any]] = {}

# ==========================================================
# ✅ 유틸
# ==========================================================
def is_admin(interaction: discord.Interaction) -> bool:
    return interaction.user.guild_permissions.administrator


def _now_ts() -> float:
    return time.time()


def _start_of_month_kst(dt: datetime) -> datetime:
    return datetime(dt.year, dt.month, 1, 0, 0, 0, tzinfo=KST)


def _fmt_seconds(sec: int) -> str:
    sec = max(0, int(sec))
    h = sec // 3600
    m = (sec % 3600) // 60
    s = sec % 60
    if h > 0:
        return f"{h}시간 {m}분"
    if m > 0:
        return f"{m}분 {s}초"
    return f"{s}초"


def _get_last_proxy_message_id(channel_id: int) -> Optional[int]:
    v = data.get("last_proxy_message_id_by_channel", {}).get(str(channel_id))
    try:
        return int(v) if v else None
    except Exception:
        return None


def _set_last_proxy_message_id(channel_id: int, message_id: int) -> None:
    data.setdefault("last_proxy_message_id_by_channel", {})
    data["last_proxy_message_id_by_channel"][str(channel_id)] = int(message_id)
    save_data(data)


def _set_last_forum_post(forum_id: int, thread_id: int, message_id: int) -> None:
    data.setdefault("last_forum_post_by_forum", {})
    data["last_forum_post_by_forum"][str(forum_id)] = {
        "thread_id": int(thread_id),
        "message_id": int(message_id),
    }
    save_data(data)


# ==========================================================
# ✅ 임시 통화방(삭제 안정화) 유틸
# ==========================================================
def _remember_temp_vc(ch_id: int) -> None:
    data.setdefault("temp_voice_channels", [])
    ch_id = int(ch_id)
    if ch_id not in data["temp_voice_channels"]:
        data["temp_voice_channels"].append(ch_id)
        save_data(data)


def _forget_temp_vc(ch_id: int) -> None:
    ch_id = int(ch_id)
    arr = list(map(int, data.get("temp_voice_channels", [])))
    data["temp_voice_channels"] = [x for x in arr if x != ch_id]
    save_data(data)


def _is_temp_vc(ch_id: int) -> bool:
    try:
        return int(ch_id) in set(map(int, data.get("temp_voice_channels", [])))
    except Exception:
        return False


async def _try_delete_voice_channel(ch: discord.abc.GuildChannel, reason: str) -> bool:
    try:
        await ch.delete(reason=reason)
        return True
    except discord.Forbidden:
        print(f"[TEMP-VC] delete forbidden: {getattr(ch, 'id', '?')} / {getattr(ch, 'name', '')}")
        return False
    except discord.HTTPException as e:
        print(f"[TEMP-VC] delete HTTPException: {getattr(ch, 'id', '?')} / {getattr(ch, 'name', '')} / {repr(e)}")
        return False
    except Exception as e:
        print(f"[TEMP-VC] delete unknown: {getattr(ch, 'id', '?')} / {getattr(ch, 'name', '')} / {repr(e)}")
        return False


async def _delete_if_empty(ch: discord.VoiceChannel, delay: float, reason: str) -> None:
    await asyncio.sleep(delay)

    # fetch로 최신 상태 확인(캐시 꼬임 대비)
    try:
        guild = ch.guild
        fresh = guild.get_channel(ch.id)
        if not isinstance(fresh, discord.VoiceChannel):
            try:
                fetched = await guild.fetch_channel(ch.id)
                fresh = fetched if isinstance(fetched, discord.VoiceChannel) else None
            except Exception:
                fresh = None

        if not isinstance(fresh, discord.VoiceChannel):
            _forget_temp_vc(ch.id)
            return

        if fresh.members:
            return

        ok = await _try_delete_voice_channel(fresh, reason=reason)
        if ok:
            _forget_temp_vc(fresh.id)
    except Exception:
        pass


# ==========================================================
# ✅ Bot (길드 슬래시 즉시 반영)
# ==========================================================
class MyBot(commands.Bot):
    async def setup_hook(self):
        # 길드 명령 정리 후 글로벌 복사 -> 길드 sync
        self.tree.clear_commands(guild=MY_GUILD)
        self.tree.copy_global_to(guild=MY_GUILD)
        synced = await self.tree.sync(guild=MY_GUILD)
        print(f"[SLASH] synced {len(synced)}: {[c.name for c in synced]}")


bot = MyBot(command_prefix="!", intents=intents)

# ==========================================================
# ✅ 월초 자동 초기화 (매달 1일 00:00 KST)
# ==========================================================
@tasks.loop(minutes=1)
async def monthly_reset_loop():
    now = datetime.now(KST)
    yyyymm = now.strftime("%Y-%m")
    if now.day == 1 and now.hour == 0 and now.minute == 0 and data.get("last_monthly_reset") != yyyymm:
        data["voice_log"] = []
        data["voice_join_ts"] = {}
        data["last_monthly_reset"] = yyyymm
        save_data(data)
        print(f"[VOICE] monthly reset: {yyyymm}")


@monthly_reset_loop.before_loop
async def before_monthly_reset_loop():
    await bot.wait_until_ready()


# ==========================================================
# ✅ 임시 통화방 GC (20초마다)
# ==========================================================
@tasks.loop(seconds=20)
async def temp_voice_gc_loop():
    await bot.wait_until_ready()

    guild = bot.get_guild(GUILD_ID)
    if not guild:
        return

    hub = guild.get_channel(VOICE_HUB_CHANNEL_ID)
    hub_category_id = hub.category_id if isinstance(hub, discord.VoiceChannel) else None

    # 1) 기록된 temp 채널들 청소
    for ch_id in list(map(int, data.get("temp_voice_channels", []))):
        ch = guild.get_channel(ch_id)
        if not isinstance(ch, discord.VoiceChannel):
            try:
                fetched = await guild.fetch_channel(ch_id)
                ch = fetched if isinstance(fetched, discord.VoiceChannel) else None
            except Exception:
                ch = None

        if not isinstance(ch, discord.VoiceChannel):
            _forget_temp_vc(ch_id)
            continue

        if not ch.members:
            ok = await _try_delete_voice_channel(ch, reason="temp voice GC cleanup")
            if ok:
                _forget_temp_vc(ch_id)

    # 2) 안정장치: 허브 카테고리의 "님의 통화방" 패턴도 청소(재시작/꼬임 대비)
    if hub_category_id:
        for vc in list(guild.voice_channels):
            try:
                if vc.category_id != hub_category_id:
                    continue
                if not vc.name.endswith("님의 통화방"):
                    continue
                if vc.members:
                    continue

                ok = await _try_delete_voice_channel(vc, reason="temp voice category sweep")
                if ok:
                    _forget_temp_vc(vc.id)
            except Exception:
                continue


# ==========================================================
# ✅ 에러 핸들러
# ==========================================================
@bot.event
async def on_error(event, *args, **kwargs):
    print("[ERROR] event:", event)
    traceback.print_exc()


@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    print("[SLASH-ERROR]", repr(error))
    try:
        if interaction.response.is_done():
            await interaction.followup.send(f"오류: {error}", ephemeral=True)
        else:
            await interaction.response.send_message(f"오류: {error}", ephemeral=True)
    except Exception:
        pass


# ==========================================================
# ✅ on_ready
# ==========================================================
@bot.event
async def on_ready():
    print("==================================================")
    print("[READY] user:", bot.user)
    print("[READY] guilds:", [(g.name, g.id) for g in bot.guilds])
    print("[READY] DATA_FILE:", str(DATA_FILE))
    print("==================================================")

    if not monthly_reset_loop.is_running():
        monthly_reset_loop.start()

    if not temp_voice_gc_loop.is_running():
        temp_voice_gc_loop.start()

    # 봇 켤 때 이미 음성에 있는 멤버도 포함(현재 접속중 실시간 합산용)
    try:
        now_ts = _now_ts()
        for g in bot.guilds:
            for vc in g.voice_channels:
                for m in vc.members:
                    uid = str(m.id)
                    if uid not in data["voice_join_ts"]:
                        data["voice_join_ts"][uid] = now_ts
        save_data(data)
    except Exception as e:
        print("[WARN] voice init:", repr(e))


# ==========================================================
# ✅ 부스트 감지 → 감사 메시지 + 이미지 자동 전송
# ==========================================================
@bot.event
async def on_member_update(before: discord.Member, after: discord.Member):
    try:
        if after.guild.id != GUILD_ID:
            return

        # 부스트 시작: premium_since None -> datetime
        if before.premium_since is None and after.premium_since is not None:
            ch = after.guild.get_channel(int(BOOST_THANKS_CHANNEL_ID)) if BOOST_THANKS_CHANNEL_ID else None
            if not isinstance(ch, discord.TextChannel):
                print("[WARN] boost channel invalid")
                return

            embed = discord.Embed(
                title="서버 부스트 감사합니다!",
                description=f"{after.mention} 님이 서버를 부스트해주셨어요!\n정말 감사합니다!",
                color=discord.Color.purple()
            )
            if BOOST_THANKS_IMAGE_URL:
                embed.set_image(url=BOOST_THANKS_IMAGE_URL)

            await ch.send(embed=embed)

    except Exception as e:
        print("[WARN] on_member_update(boost):", repr(e))


# ==========================================================
# ✅ 메시지 삭제 로그
# ==========================================================
@bot.event
async def on_message_delete(message: discord.Message):
    try:
        if not message.guild:
            return
        if message.channel.id == LOG_CHANNEL_ID:
            return
        if message.author and message.author.bot:
            return

        log_ch = message.guild.get_channel(LOG_CHANNEL_ID)
        if not isinstance(log_ch, discord.TextChannel):
            return

        author = message.author.display_name if isinstance(message.author, discord.Member) else "알 수 없음"
        content = message.content or "(내용 없음)"

        embed = discord.Embed(
            title="메시지 삭제",
            description=f"채널: {message.channel.mention}\n작성자: {author}\n\n{content}",
            color=discord.Color.orange()
        )

        view = discord.ui.View()
        view.add_item(discord.ui.Button(
            label="채팅 채널로 이동",
            url=f"https://discord.com/channels/{message.guild.id}/{message.channel.id}"
        ))

        await log_ch.send(embed=embed, view=view)
    except Exception:
        pass


# ==========================================================
# ✅ 메시지 통계 + 이미지 대신쓰기(1회) + 포스트 이미지 수집(+원본 자동삭제)
# ==========================================================
@bot.event
async def on_message(message: discord.Message):
    if not message.author.bot:
        uid = str(message.author.id)
        data["msg_count"][uid] = data["msg_count"].get(uid, 0) + 1
        save_data(data)

    # ✅ 포스트생성 이미지 수집: 첨부 올리면 "삭제 전에" 파일로 저장
    if (
        not message.author.bot
        and message.author.id in pending_post_create
        and message.attachments
    ):
        st = pending_post_create.get(message.author.id)
        if st and int(st.get("channel_id")) == int(message.channel.id):
            st.setdefault("files", [])
            for a in message.attachments:
                try:
                    st["files"].append(await a.to_file())
                except Exception:
                    continue

            # 원본 자동 삭제
            try:
                await message.delete()
            except Exception:
                pass
            return

    # ✅ 이미지 대신쓰기 처리 (다음 첨부 1회)
    if (
        not message.author.bot
        and message.author.id in pending_image_say_as
        and message.attachments
    ):
        target = pending_image_say_as.pop(message.author.id)
        files = []
        for a in message.attachments:
            try:
                files.append(await a.to_file())
            except Exception:
                pass

        if files:
            sent = await target.send(files=files)
            if hasattr(target, "id"):
                _set_last_proxy_message_id(int(target.id), int(sent.id))

        # 원본 삭제
        try:
            await message.delete()
        except Exception:
            pass
        return

    await bot.process_commands(message)


# ==========================================================
# ✅ 음성 체류 기록 + 허브 개인방 생성 + 빈 방 삭제(즉시/재시도)
# ==========================================================
@bot.event
async def on_voice_state_update(member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
    uid = str(member.id)
    now_ts = _now_ts()

    # 입장
    if before.channel is None and after.channel is not None:
        data["voice_join_ts"][uid] = now_ts
        save_data(data)

    # 이동
    if before.channel is not None and after.channel is not None and before.channel.id != after.channel.id:
        ts = data["voice_join_ts"].get(uid)
        if ts:
            dur = int(now_ts - float(ts))
            if dur > 0:
                data["voice_log"].append({"user_id": uid, "duration": dur, "ts": int(now_ts)})
        data["voice_join_ts"][uid] = now_ts
        save_data(data)

    # 퇴장
    if before.channel is not None and after.channel is None:
        ts = data["voice_join_ts"].pop(uid, None)
        if ts:
            dur = int(now_ts - float(ts))
            if dur > 0:
                data["voice_log"].append({"user_id": uid, "duration": dur, "ts": int(now_ts)})
        save_data(data)

    # 허브 입장 -> 개인방 생성
    if after.channel and after.channel.id == VOICE_HUB_CHANNEL_ID:
        hub = after.channel
        category = hub.category
        if category is None:
            print("[TEMP-VC] hub has no category -> cannot create")
            return

        try:
            ch = await member.guild.create_voice_channel(
                name=f"{member.display_name}님의 통화방",
                category=category
            )
            _remember_temp_vc(ch.id)
            await member.move_to(ch)
        except Exception as e:
            print("[TEMP-VC] create failed:", repr(e))
            return

    # 누군가 나가거나 이동해서 before.channel이 비면 삭제(즉시 + 2초/8초 재시도)
    if before.channel and isinstance(before.channel, discord.VoiceChannel):
        guild = member.guild
        hub = guild.get_channel(VOICE_HUB_CHANNEL_ID)
        hub_category_id = hub.category_id if isinstance(hub, discord.VoiceChannel) else None

        is_managed = _is_temp_vc(before.channel.id)
        if not is_managed and hub_category_id and before.channel.category_id == hub_category_id:
            if before.channel.name.endswith("님의 통화방"):
                is_managed = True

        if is_managed:
            bot.loop.create_task(_delete_if_empty(before.channel, delay=0.0, reason="temp voice empty immediate"))
            bot.loop.create_task(_delete_if_empty(before.channel, delay=2.0, reason="temp voice empty retry(2s)"))
            bot.loop.create_task(_delete_if_empty(before.channel, delay=8.0, reason="temp voice empty retry(8s)"))


# ==========================================================
# ✅ /대신쓰기 (관리자)
# ==========================================================
class ProxySayModal(discord.ui.Modal, title="대신쓰기"):
    content = discord.ui.TextInput(
        label="대신 보낼 내용",
        style=discord.TextStyle.paragraph,
        max_length=2000
    )

    def __init__(self, author: discord.Member):
        super().__init__()
        self.author = author

    async def on_submit(self, interaction: discord.Interaction):
        embed = discord.Embed(description=str(self.content.value), color=discord.Color.green())
        ch = interaction.channel

        # 포럼에서 실행하면 스레드 생성
        if isinstance(ch, discord.ForumChannel):
            t = await ch.create_thread(
                name=f"{self.author.display_name} 대신쓰기",
                content="관리자 대신쓰기"
            )
            thread = t[0] if isinstance(t, tuple) else t
            target = thread
        else:
            target = ch

        sent = await target.send(embed=embed)
        _set_last_proxy_message_id(int(target.id), int(sent.id))
        await interaction.response.send_message("전송했어요.", ephemeral=True)


@bot.tree.command(name="대신쓰기", description="관리자 전용: 현재 위치에 대신 말하기")
async def cmd_proxy_say(interaction: discord.Interaction):
    if not is_admin(interaction):
        await interaction.response.send_message("관리자 전용", ephemeral=True)
        return
    await interaction.response.send_modal(ProxySayModal(interaction.user))


# ==========================================================
# ✅ /이미지대신쓰기 (관리자)
# ==========================================================
@bot.tree.command(name="이미지대신쓰기", description="관리자 전용: 다음 이미지/파일을 봇이 대신 보냅니다(1회).")
async def cmd_proxy_image(interaction: discord.Interaction):
    if not is_admin(interaction):
        await interaction.response.send_message("관리자 전용", ephemeral=True)
        return

    ch = interaction.channel
    if isinstance(ch, discord.ForumChannel):
        t = await ch.create_thread(
            name=f"{interaction.user.display_name} 이미지 대신쓰기",
            content="관리자 이미지 대신쓰기"
        )
        thread = t[0] if isinstance(t, tuple) else t
        target = thread
    else:
        target = ch

    pending_image_say_as[interaction.user.id] = target
    await interaction.response.send_message("이제 이미지/파일을 올려주세요. (다음 메시지 1회만 인식)", ephemeral=True)


# ==========================================================
# ✅ /환영 (자동 문구)
# ==========================================================
@bot.tree.command(name="환영", description="관리자 전용: 환영 메시지 보내기(자동 문구)")
@app_commands.rename(member="유저")
@app_commands.describe(member="환영할 유저")
async def cmd_welcome(interaction: discord.Interaction, member: discord.Member):
    if not is_admin(interaction):
        await interaction.response.send_message("관리자 전용", ephemeral=True)
        return

    ch = interaction.guild.get_channel(WELCOME_CHANNEL_ID) if interaction.guild else None
    if not isinstance(ch, discord.TextChannel):
        await interaction.response.send_message("환영 채널을 찾지 못했어요.", ephemeral=True)
        return

    msg = f"환영해요 {member.mention} 새로 오신분께 다들 인사 부탁드려요!!"
    await ch.send(embed=discord.Embed(description=msg, color=discord.Color.green()))
    await interaction.response.send_message("완료", ephemeral=True)


# ==========================================================
# ✅ /음성통계 (이번달 + 현재 포함)
# ==========================================================
@bot.tree.command(name="음성통계", description="이번 달(1일~말일) 음성채팅 활동 시간 전체 유저 통계(현재 접속중 포함)")
async def cmd_voice_stats(interaction: discord.Interaction):
    guild = interaction.guild
    if not guild:
        return

    now = datetime.now(KST)
    start_month = _start_of_month_kst(now)

    totals: Dict[str, int] = {}

    # 누적 로그
    for log in data.get("voice_log", []):
        try:
            ts = int(log.get("ts", 0))
            uid = str(log.get("user_id"))
            duration = int(log.get("duration", 0))
            if ts <= 0 or duration <= 0:
                continue
            if datetime.fromtimestamp(ts, KST) >= start_month:
                totals[uid] = totals.get(uid, 0) + duration
        except Exception:
            continue

    # 현재 접속중 합산
    now_ts = _now_ts()
    for uid, join_ts in data.get("voice_join_ts", {}).items():
        try:
            uid = str(uid)
            join_ts = float(join_ts)
            if datetime.fromtimestamp(int(join_ts), KST) < start_month:
                continue
            live = int(now_ts - join_ts)
            if live > 0:
                totals[uid] = totals.get(uid, 0) + live
        except Exception:
            continue

    if not totals:
        await interaction.response.send_message(
            embed=discord.Embed(
                title=f"{now.month}월 음성채팅 통계",
                description="이번 달 음성 활동 기록이 없습니다.",
                color=discord.Color.blue()
            )
        )
        return

    ranked = sorted(totals.items(), key=lambda x: x[1], reverse=True)[:50]
    lines = []
    for i, (uid, sec) in enumerate(ranked, start=1):
        mem = guild.get_member(int(uid)) if uid.isdigit() else None
        name = mem.display_name if mem else uid
        lines.append(f"{i}. {name} - {_fmt_seconds(sec)}")

    await interaction.response.send_message(
        embed=discord.Embed(
            title=f"{now.month}월 음성채팅 활동 통계 (현재 포함)",
            description="\n".join(lines),
            color=discord.Color.blue()
        )
    )


# ==========================================================
# ✅ 포럼 생성 (REST)
#   discord.py에서 guild.create_forum_channel이 없는 환경에서도 생성되게끔
# ==========================================================
async def _create_forum_channel_rest(guild: discord.Guild, name: str) -> discord.ForumChannel:
    forum_type = 15  # Discord API: 15 = GUILD_FORUM
    http = guild._state.http
    try:
        created = await http.create_channel(guild.id, channel_type=forum_type, name=name)
    except TypeError:
        # 일부 버전은 인자 형태가 다름
        created = await http.create_channel(guild.id, forum_type, name=name)

    forum_id = int(created["id"])
    ch = await guild.fetch_channel(forum_id)
    if not isinstance(ch, discord.ForumChannel):
        raise TypeError("created channel is not ForumChannel")
    return ch


@bot.tree.command(name="포럼생성", description="관리자 전용: 포럼 채널 생성(이름만)")
@app_commands.rename(forum_name="포럼이름")
async def cmd_forum_create(interaction: discord.Interaction, forum_name: str):
    if not is_admin(interaction):
        await interaction.response.send_message("관리자 전용", ephemeral=True)
        return
    if not interaction.guild:
        return

    try:
        forum = await _create_forum_channel_rest(interaction.guild, forum_name)
        await interaction.response.send_message(f"포럼 생성 완료: {forum.mention}", ephemeral=True)
    except discord.Forbidden:
        await interaction.response.send_message("권한 부족: 채널 관리 권한 필요", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"실패: {repr(e)}", ephemeral=True)


# ==========================================================
# ✅ /포스트생성 (이미지 먼저 → 내용 모달)
# ==========================================================
class PostCreateStartView(discord.ui.View):
    def __init__(self, user_id: int):
        super().__init__(timeout=300)
        self.user_id = user_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("이 버튼은 명령 실행자만 사용할 수 있어요.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="내용 입력", style=discord.ButtonStyle.green)
    async def open_modal(self, interaction: discord.Interaction, button: discord.ui.Button):
        st = pending_post_create.get(self.user_id)
        if not st:
            await interaction.response.send_message("대기 중인 포스트 생성이 없어요. /포스트생성 다시 실행해줘.", ephemeral=True)
            return

        forum = interaction.guild.get_channel(int(st["forum_id"])) if interaction.guild else None
        if not isinstance(forum, discord.ForumChannel):
            pending_post_create.pop(self.user_id, None)
            await interaction.response.send_message("포럼을 찾지 못했어요. /포스트생성 다시 실행해줘.", ephemeral=True)
            return

        await interaction.response.send_modal(PostCreateModal(forum, self.user_id))

    @discord.ui.button(label="취소", style=discord.ButtonStyle.gray)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        pending_post_create.pop(self.user_id, None)
        await interaction.response.send_message("취소했어요.", ephemeral=True)


class PostCreateModal(discord.ui.Modal, title="포스트 생성"):
    post_title = discord.ui.TextInput(label="제목", placeholder="게시글 제목", max_length=100)
    post_content = discord.ui.TextInput(
        label="내용",
        placeholder="게시글 내용 (Shift+Enter 줄바꿈 가능)",
        style=discord.TextStyle.paragraph,
        max_length=4000
    )

    def __init__(self, forum: discord.ForumChannel, user_id: int):
        super().__init__()
        self.forum = forum
        self.user_id = user_id

    async def on_submit(self, interaction: discord.Interaction):
        try:
            st = pending_post_create.get(self.user_id, {})
            files: List[discord.File] = st.get("files", [])

            title = str(self.post_title.value)
            body = str(self.post_content.value)

            thread: discord.Thread
            last_message_id_for_rolepanel: Optional[int] = None

            # ✅ 1) 이미지가 있으면: 가능한 환경에서는 "첫 글(스타터)에 파일"로 붙이기 시도
            if files:
                created_thread = None
                starter_msg = None

                # (A) create_thread(files=) 지원 시도
                try:
                    t = await self.forum.create_thread(name=title, content=" ", files=files)
                    if isinstance(t, tuple):
                        created_thread = t[0]
                        # discord.py 버전에 따라 starter_msg가 같이 올 수도 있음
                        starter_msg = t[1] if len(t) > 1 else None
                    else:
                        created_thread = t
                        starter_msg = None
                except TypeError:
                    created_thread = None

                # (B) files=가 안되면: 첫 글만 만든 후, 스레드에 파일 메시지 전송
                if created_thread is None:
                    t = await self.forum.create_thread(name=title, content=" ")
                    thread = t[0] if isinstance(t, tuple) else t

                    # 스레드 첫 메시지로 이미지 올리기
                    try:
                        sent_img = await thread.send(files=files)
                        last_message_id_for_rolepanel = int(sent_img.id)
                    except Exception:
                        pass
                else:
                    thread = created_thread
                    # starter_msg가 있으면 그 메시지가 "맨 위" 이미지가 붙은 글일 확률이 큼
                    if starter_msg is not None:
                        last_message_id_for_rolepanel = int(starter_msg.id)

                # 본문은 그 다음 메시지로
                if body.strip():
                    sent_body = await thread.send(body)
                    last_message_id_for_rolepanel = int(sent_body.id)  # 역할패널 대상은 마지막으로 보낸 글로 잡음(원하면 여기만 바꾸면 됨)

            # ✅ 2) 이미지가 없으면: 그냥 content로 생성
            else:
                t = await self.forum.create_thread(name=title, content=body)
                if isinstance(t, tuple):
                    thread = t[0]
                    starter_msg = t[1] if len(t) > 1 else None
                else:
                    thread = t
                    starter_msg = None

                last_message_id_for_rolepanel = int(starter_msg.id) if starter_msg else int(thread.id)

            # /역할패널이 이 포스트에도 확실히 붙도록 저장
            if last_message_id_for_rolepanel:
                _set_last_proxy_message_id(int(thread.id), int(last_message_id_for_rolepanel))
                _set_last_forum_post(int(self.forum.id), int(thread.id), int(last_message_id_for_rolepanel))

            pending_post_create.pop(self.user_id, None)
            await interaction.response.send_message(f"생성 완료: {thread.mention}", ephemeral=True)

        except discord.Forbidden:
            pending_post_create.pop(self.user_id, None)
            await interaction.response.send_message("권한 부족: 포럼/스레드 생성 권한 필요", ephemeral=True)
        except Exception as e:
            pending_post_create.pop(self.user_id, None)
            await interaction.response.send_message(f"실패: {repr(e)}", ephemeral=True)


@bot.tree.command(name="포스트생성", description="관리자 전용: 포럼에 포스트 생성 (이미지 먼저 업로드 -> 내용 입력)")
@app_commands.rename(forum="포럼")
@app_commands.describe(forum="포스트를 작성할 포럼 채널")
async def cmd_post_create(interaction: discord.Interaction, forum: discord.ForumChannel):
    if not is_admin(interaction):
        await interaction.response.send_message("관리자 전용", ephemeral=True)
        return

    pending_post_create[interaction.user.id] = {
        "forum_id": int(forum.id),
        "channel_id": int(interaction.channel.id),
        "files": [],
        "created_at": _now_ts()
    }

    view = PostCreateStartView(interaction.user.id)
    await interaction.response.send_message(
        "**포스트 맨 위에 올릴 이미지/파일을 지금 이 채널에 먼저 올려주세요.** (여러 장 가능)\n"
        "업로드하면 원본 메시지는 자동 삭제됩니다.\n"
        "업로드가 끝났으면 아래 [내용 입력] 버튼을 눌러서 제목/내용을 작성하면 됩니다.\n"
        "이미지 없이 글만 올릴 거면 그냥 [내용 입력] 누르면 돼요.",
        ephemeral=True,
        view=view
    )


# ==========================================================
# ✅ /역할패널 (포스트생성으로 만든 글에도 작동)
# ==========================================================
@bot.tree.command(name="역할패널", description="관리자 전용: 마지막 포스트/대신쓰기 메시지에 (이모지→역할) 추가")
@app_commands.rename(emoji="이모지", role="역할")
@app_commands.describe(emoji="예: ✅ 또는 <:name:id> 또는 <a:name:id>", role="지급할 역할")
async def cmd_role_panel(interaction: discord.Interaction, emoji: str, role: discord.Role):
    if not is_admin(interaction):
        await interaction.response.send_message("관리자 전용", ephemeral=True)
        return

    ch = interaction.channel
    msg = None

    # 텍스트채널/스레드: 해당 채널의 last_proxy_message_id 사용
    if isinstance(ch, (discord.TextChannel, discord.Thread)):
        msg_id = _get_last_proxy_message_id(int(ch.id))
        if not msg_id:
            await interaction.response.send_message("이 채널에서 최근 대상 메시지를 찾지 못했어요.", ephemeral=True)
            return
        try:
            msg = await ch.fetch_message(int(msg_id))
        except Exception:
            await interaction.response.send_message("대상 메시지를 찾지 못했어요.", ephemeral=True)
            return

    # 포럼채널: last_forum_post_by_forum에서 thread+message 가져오기
    elif isinstance(ch, discord.ForumChannel):
        info = data.get("last_forum_post_by_forum", {}).get(str(ch.id))
        if not info:
            await interaction.response.send_message("이 포럼에서 최근 /포스트생성으로 만든 포스트가 없어요.", ephemeral=True)
            return

        thread_id = int(info.get("thread_id", 0))
        msg_id = int(info.get("message_id", 0))
        if thread_id <= 0 or msg_id <= 0:
            await interaction.response.send_message("저장된 포스트 정보가 이상해요. /포스트생성 다시 해줘.", ephemeral=True)
            return

        thread = interaction.guild.get_channel(thread_id) if interaction.guild else None
        if not isinstance(thread, discord.Thread):
            try:
                thread = await interaction.guild.fetch_channel(thread_id)  # type: ignore
            except Exception:
                thread = None

        if not isinstance(thread, discord.Thread):
            await interaction.response.send_message("포스트(스레드)를 찾지 못했어요. 삭제됐을 수 있어요.", ephemeral=True)
            return

        try:
            msg = await thread.fetch_message(msg_id)
        except Exception:
            await interaction.response.send_message("포스트 메시지를 찾지 못했어요. 삭제됐을 수 있어요.", ephemeral=True)
            return

    else:
        await interaction.response.send_message("이 채널에서는 사용할 수 없어요.", ephemeral=True)
        return

    # 리액션 추가
    try:
        await msg.add_reaction(emoji)
    except Exception as e:
        await interaction.response.send_message(f"이모지 반응 추가 실패: {repr(e)}", ephemeral=True)
        return

    rr = data.setdefault("reaction_roles", {})
    rr.setdefault(str(msg.id), {})
    rr[str(msg.id)][str(emoji)] = int(role.id)
    save_data(data)

    await interaction.response.send_message(f"{emoji} → {role.name} 연결 완료", ephemeral=True)


@bot.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
    if bot.user and payload.user_id == bot.user.id:
        return

    mapping = data.get("reaction_roles", {}).get(str(payload.message_id))
    if not mapping:
        return

    role_id = mapping.get(str(payload.emoji))
    if not role_id:
        return

    guild = bot.get_guild(payload.guild_id) if payload.guild_id else None
    if not guild:
        return

    member = guild.get_member(payload.user_id)
    role = guild.get_role(int(role_id))
    if member and role and not member.bot:
        try:
            await member.add_roles(role, reason="Reaction role add")
        except Exception:
            pass


@bot.event
async def on_raw_reaction_remove(payload: discord.RawReactionActionEvent):
    mapping = data.get("reaction_roles", {}).get(str(payload.message_id))
    if not mapping:
        return

    role_id = mapping.get(str(payload.emoji))
    if not role_id:
        return

    guild = bot.get_guild(payload.guild_id) if payload.guild_id else None
    if not guild:
        return

    member = guild.get_member(payload.user_id)
    role = guild.get_role(int(role_id))
    if member and role and not member.bot:
        try:
            await member.remove_roles(role, reason="Reaction role remove")
        except Exception:
            pass


# ==========================================================
# ✅ 실행
# ==========================================================
if __name__ == "__main__":
    if not TOKEN:
        print("TOKEN이 비어있어요.")
        sys.exit(1)

    try:
        bot.run(TOKEN)
    except Exception:
        traceback.print_exc()
        input("엔터를 누르면 종료됩니다...")
