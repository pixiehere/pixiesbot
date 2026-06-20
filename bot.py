"""
PixiesMusic - Discord Music Bot
Source: JioSaavn API (search by name) - 320kbps, no bot detection, no YouTube
Commands: !play, !pause, !resume, !skip, !queue, !stop, !leave, !nowplaying, !loop, !shuffle
"""

import os
import random
import asyncio
import aiohttp
from collections import deque
from datetime import datetime

import discord
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# ── Colors ──
COLOR_NOW_PLAYING = 0x6C63FF
COLOR_QUEUED      = 0x22D3EE
COLOR_QUEUE_LIST  = 0x818CF8
COLOR_SUCCESS     = 0x4ADE80
COLOR_WARNING     = 0xFBBF24
COLOR_DANGER      = 0xF87171

BOT_DISPLAY_NAME  = "PixiesMusic"
BOT_ICON_URL      = "https://cdn.discordapp.com/embed/avatars/0.png"
MUSIC_NOTES       = ["🎵", "🎶", "🎸", "🎹", "🎺", "🎻", "🥁"]

FFMPEG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ffmpeg.exe")
if not os.path.isfile(FFMPEG_PATH):
    FFMPEG_PATH = "ffmpeg"

FFMPEG_OPTS = {
    "executable": FFMPEG_PATH,
    "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
    "options": "-vn",
}

SAAVN_API = "https://saavn.dev/api"


# ════════════════════════════════════════════
#  JioSaavn Search & Stream
# ════════════════════════════════════════════

async def search_jiosaavn(query: str) -> dict | None:
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{SAAVN_API}/search/songs",
                params={"query": query, "limit": 1},
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()

        results = data.get("data", {}).get("results", [])
        if not results:
            return None

        song = results[0]

        # Get best quality stream URL
        stream_url = None
        for quality in ["320kbps", "160kbps", "96kbps", "48kbps"]:
            for d in song.get("downloadUrl", []):
                if d.get("quality") == quality and d.get("url"):
                    stream_url = d["url"]
                    break
            if stream_url:
                break

        if not stream_url:
            urls = song.get("downloadUrl", [])
            if urls:
                stream_url = urls[-1].get("url")

        if not stream_url:
            return None

        # Thumbnail
        thumbnail = None
        for img in reversed(song.get("image", [])):
            if img.get("url"):
                thumbnail = img["url"]
                break

        # Artists
        artists = song.get("artists", {}).get("primary", [])
        artist_str = ", ".join(a["name"] for a in artists if a.get("name")) or "Unknown"

        return {
            "url":         stream_url,
            "title":       song.get("name", "Unknown"),
            "webpage_url": song.get("url", ""),
            "duration":    int(song.get("duration", 0)),
            "uploader":    artist_str,
            "thumbnail":   thumbnail,
        }

    except Exception as e:
        print(f"[JioSaavn] Error: {e}")
        return None


async def fetch_song(query: str, requester: discord.Member) -> dict:
    result = await search_jiosaavn(query)
    if result is None:
        raise ValueError(f"No results found for **{query}** on JioSaavn. Try a different song name.")
    result["requester"] = requester
    return result


# ════════════════════════════════════════════
#  Helpers
# ════════════════════════════════════════════

def format_duration(seconds: int) -> str:
    if not seconds:
        return "Live"
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def progress_bar(position: int, total: int, length: int = 18) -> str:
    if not total:
        return "▬" * length
    ratio = max(0.0, min(1.0, position / total))
    filled = int(ratio * length)
    bar = "━" * filled + "●" + "─" * (length - filled)
    return f"`{bar}`  {format_duration(position)} / {format_duration(total)}"


def timestamp_now() -> str:
    return datetime.utcnow().strftime("%H:%M UTC")


# ════════════════════════════════════════════
#  Per-guild State
# ════════════════════════════════════════════

class MusicState:
    def __init__(self, guild_id: int):
        self.guild_id   = guild_id
        self.queue: deque[dict] = deque()
        self.current: dict | None = None
        self.loop_current: bool = False
        self.now_playing_message: discord.Message | None = None
        self.text_channel: discord.abc.Messageable | None = None
        self.start_time: float = 0.0


guild_states: dict[int, MusicState] = {}


def get_state(guild_id: int) -> MusicState:
    if guild_id not in guild_states:
        guild_states[guild_id] = MusicState(guild_id)
    return guild_states[guild_id]


# ════════════════════════════════════════════
#  Embeds
# ════════════════════════════════════════════

def build_now_playing_embed(song: dict, state: MusicState) -> discord.Embed:
    note       = random.choice(MUSIC_NOTES)
    loop_badge = "  `🔁 LOOP`" if state.loop_current else ""
    elapsed    = int(asyncio.get_event_loop().time() - state.start_time) if state.start_time else 0
    elapsed    = min(elapsed, song["duration"]) if song["duration"] else elapsed

    embed = discord.Embed(
        title       = f"{note}  Now Playing{loop_badge}",
        description = f"### [{song['title']}]({song['webpage_url']})",
        color       = COLOR_NOW_PLAYING,
        timestamp   = datetime.utcnow(),
    )
    embed.add_field(name="Progress",      value=progress_bar(elapsed, song["duration"]), inline=False)
    embed.add_field(name="⏱  Duration",   value=f"`{format_duration(song['duration'])}`", inline=True)
    embed.add_field(name="🎤  Artist",    value=f"`{song.get('uploader', 'Unknown')}`",   inline=True)
    embed.add_field(name="📥  Requested", value=song["requester"].mention,                 inline=True)
    embed.add_field(name="📋  In Queue",  value=f"`{len(state.queue)} tracks`",            inline=True)
    embed.add_field(name="🎵  Source",    value="JioSaavn 320kbps",                        inline=True)

    if song.get("thumbnail"):
        embed.set_thumbnail(url=song["thumbnail"])

    embed.set_author(name=f"{BOT_DISPLAY_NAME}  •  Music Player", icon_url=BOT_ICON_URL)
    embed.set_footer(text=f"Buttons below to control  •  {timestamp_now()}")
    return embed


def build_added_embed(song: dict, position: int) -> discord.Embed:
    embed = discord.Embed(
        title       = "🎵  Added to Queue",
        description = f"**[{song['title']}]({song['webpage_url']})**",
        color       = COLOR_QUEUED,
        timestamp   = datetime.utcnow(),
    )
    embed.add_field(name="⏱  Duration",   value=f"`{format_duration(song['duration'])}`", inline=True)
    embed.add_field(name="🎤  Artist",    value=f"`{song.get('uploader', 'Unknown')}`",   inline=True)
    embed.add_field(name="🔢  Position",  value=f"`#{position}`",                          inline=True)
    embed.add_field(name="📥  Requested", value=song["requester"].mention,                 inline=False)

    if song.get("thumbnail"):
        embed.set_thumbnail(url=song["thumbnail"])

    embed.set_author(name=BOT_DISPLAY_NAME, icon_url=BOT_ICON_URL)
    embed.set_footer(text=f"Queue position {position}  •  {timestamp_now()}")
    return embed


def build_queue_embed(state: MusicState) -> discord.Embed:
    embed = discord.Embed(title="📋  Current Queue", color=COLOR_QUEUE_LIST, timestamp=datetime.utcnow())

    if state.current:
        cur = state.current
        embed.add_field(
            name   = "▶️  Now Playing",
            value  = f"[{cur['title']}]({cur['webpage_url']})  •  `{format_duration(cur['duration'])}`",
            inline = False,
        )

    if state.queue:
        total = sum(s.get("duration", 0) for s in state.queue)
        lines = [
            f"`{i:>2}.`  **{s['title']}**  •  `{format_duration(s['duration'])}`"
            for i, s in enumerate(list(state.queue)[:15], 1)
        ]
        if len(state.queue) > 15:
            lines.append(f"*… and {len(state.queue) - 15} more*")
        embed.add_field(
            name   = f"⏩  Up Next — {len(state.queue)} tracks  •  `{format_duration(total)}` total",
            value  = "\n".join(lines),
            inline = False,
        )
    else:
        embed.add_field(name="Up Next", value="*Empty — use* `!play <song name>`", inline=False)

    embed.set_author(name=BOT_DISPLAY_NAME, icon_url=BOT_ICON_URL)
    embed.set_footer(text=f"Loop: {'ON 🔁' if state.loop_current else 'OFF'}  •  {timestamp_now()}")
    return embed


def build_status_embed(title: str, desc: str, color: int = COLOR_SUCCESS, emoji: str = "✅") -> discord.Embed:
    embed = discord.Embed(title=f"{emoji}  {title}", description=desc, color=color, timestamp=datetime.utcnow())
    embed.set_author(name=BOT_DISPLAY_NAME, icon_url=BOT_ICON_URL)
    embed.set_footer(text=timestamp_now())
    return embed


def build_error_embed(desc: str) -> discord.Embed:
    return build_status_embed("Oops!", desc, COLOR_WARNING, "⚠️")


# ════════════════════════════════════════════
#  Controls View
# ════════════════════════════════════════════

class MusicControls(discord.ui.View):
    def __init__(self, guild_id: int):
        super().__init__(timeout=None)
        self.guild_id = guild_id

    def _vc(self, i: discord.Interaction):
        return i.guild.voice_client

    @discord.ui.button(label="Pause / Resume", emoji="⏯️", style=discord.ButtonStyle.primary, row=0)
    async def pause_resume(self, interaction: discord.Interaction, button: discord.ui.Button):
        vc = self._vc(interaction)
        if not vc:
            await interaction.response.send_message(embed=build_error_embed("Not in a voice channel."), ephemeral=True)
        elif vc.is_playing():
            vc.pause()
            await interaction.response.send_message(embed=build_status_embed("Paused", "Playback paused.", COLOR_WARNING, "⏸️"), ephemeral=True)
        elif vc.is_paused():
            vc.resume()
            await interaction.response.send_message(embed=build_status_embed("Resumed", "Playback resumed.", COLOR_SUCCESS, "▶️"), ephemeral=True)
        else:
            await interaction.response.send_message(embed=build_error_embed("Nothing playing."), ephemeral=True)

    @discord.ui.button(label="Skip", emoji="⏭️", style=discord.ButtonStyle.secondary, row=0)
    async def skip(self, interaction: discord.Interaction, button: discord.ui.Button):
        vc = self._vc(interaction)
        if vc and (vc.is_playing() or vc.is_paused()):
            vc.stop()
            await interaction.response.send_message(embed=build_status_embed("Skipped", "Track skipped.", COLOR_SUCCESS, "⏭️"), ephemeral=True)
        else:
            await interaction.response.send_message(embed=build_error_embed("Nothing playing."), ephemeral=True)

    @discord.ui.button(label="Stop", emoji="⏹️", style=discord.ButtonStyle.danger, row=0)
    async def stop(self, interaction: discord.Interaction, button: discord.ui.Button):
        state = get_state(self.guild_id)
        state.queue.clear()
        state.current = None
        vc = self._vc(interaction)
        if vc:
            vc.stop()
        await interaction.response.send_message(embed=build_status_embed("Stopped", "Queue cleared.", COLOR_DANGER, "⏹️"), ephemeral=True)

    @discord.ui.button(label="Loop", emoji="🔁", style=discord.ButtonStyle.secondary, row=1)
    async def loop_toggle(self, interaction: discord.Interaction, button: discord.ui.Button):
        state = get_state(self.guild_id)
        state.loop_current = not state.loop_current
        status = "enabled" if state.loop_current else "disabled"
        color  = COLOR_SUCCESS if state.loop_current else COLOR_WARNING
        await interaction.response.send_message(embed=build_status_embed("Loop", f"Loop **{status}**.", color, "🔁"), ephemeral=True)
        if state.now_playing_message and state.current:
            await state.now_playing_message.edit(embed=build_now_playing_embed(state.current, state))

    @discord.ui.button(label="Shuffle", emoji="🔀", style=discord.ButtonStyle.secondary, row=1)
    async def shuffle(self, interaction: discord.Interaction, button: discord.ui.Button):
        state = get_state(self.guild_id)
        if len(state.queue) < 2:
            await interaction.response.send_message(embed=build_error_embed("Need 2+ songs to shuffle."), ephemeral=True)
            return
        items = list(state.queue)
        random.shuffle(items)
        state.queue = deque(items)
        await interaction.response.send_message(embed=build_status_embed("Shuffled", f"{len(items)} tracks shuffled.", COLOR_SUCCESS, "🔀"), ephemeral=True)

    @discord.ui.button(label="Queue", emoji="📋", style=discord.ButtonStyle.secondary, row=1)
    async def show_queue(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(embed=build_queue_embed(get_state(self.guild_id)), ephemeral=True)


# ════════════════════════════════════════════
#  Playback Engine
# ════════════════════════════════════════════

def play_next(guild: discord.Guild):
    state        = get_state(guild.id)
    voice_client = guild.voice_client

    if voice_client is None or not voice_client.is_connected():
        return

    if state.loop_current and state.current:
        song = state.current
    elif state.queue:
        song          = state.queue.popleft()
        state.current = song
    else:
        state.current = None
        async def notify_done():
            if state.text_channel:
                await state.text_channel.send(
                    embed=build_status_embed("Queue Finished", "All tracks played! Add more with `!play`.", COLOR_QUEUE_LIST, "🎶")
                )
        asyncio.run_coroutine_threadsafe(notify_done(), bot.loop)
        return

    source = discord.FFmpegPCMAudio(song["url"], **FFMPEG_OPTS)

    def after_play(error):
        if error:
            print(f"[Playback error] {error}")
        play_next(guild)

    try:
        voice_client.play(source, after=after_play)
        state.start_time = asyncio.get_event_loop().time()
    except Exception as e:
        print(f"[play_next error] {e}")
        return

    async def send_np():
        if state.text_channel:
            msg = await state.text_channel.send(
                embed=build_now_playing_embed(song, state),
                view=MusicControls(guild.id),
            )
            state.now_playing_message = msg

    asyncio.run_coroutine_threadsafe(send_np(), bot.loop)


# ════════════════════════════════════════════
#  Events
# ════════════════════════════════════════════

@bot.event
async def on_ready():
    print(f"{BOT_DISPLAY_NAME} online as {bot.user}")
    await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.listening, name="!play"))


# ════════════════════════════════════════════
#  Commands
# ════════════════════════════════════════════

@bot.command(name="play", help="Play a song by name")
async def play(ctx, *, query: str):
    if not ctx.author.voice:
        await ctx.send(embed=build_error_embed("Join a voice channel first."))
        return

    vc = ctx.guild.voice_client
    try:
        if vc is None:
            vc = await ctx.author.voice.channel.connect()
            await asyncio.sleep(1.5)
        elif vc.channel != ctx.author.voice.channel:
            await vc.move_to(ctx.author.voice.channel)
            await asyncio.sleep(1)
    except Exception as e:
        await ctx.send(embed=build_error_embed(f"Could not connect: {e}"))
        return

    state              = get_state(ctx.guild.id)
    state.text_channel = ctx.channel

    msg = await ctx.send(embed=discord.Embed(
        title="🔎  Searching…",
        description=f"Looking up **{query}** on JioSaavn",
        color=COLOR_QUEUED,
    ).set_author(name=BOT_DISPLAY_NAME, icon_url=BOT_ICON_URL))

    try:
        song = await fetch_song(query, ctx.author)
    except Exception as e:
        await msg.edit(embed=build_error_embed(str(e)))
        return

    state.queue.append(song)
    await msg.edit(embed=build_added_embed(song, len(state.queue)))

    if not vc.is_playing() and not vc.is_paused():
        play_next(ctx.guild)


@bot.command(name="pause")
async def pause(ctx):
    vc = ctx.guild.voice_client
    if vc and vc.is_playing():
        vc.pause()
        await ctx.send(embed=build_status_embed("Paused", "Playback paused.", COLOR_WARNING, "⏸️"))
    else:
        await ctx.send(embed=build_error_embed("Nothing is playing."))


@bot.command(name="resume")
async def resume(ctx):
    vc = ctx.guild.voice_client
    if vc and vc.is_paused():
        vc.resume()
        await ctx.send(embed=build_status_embed("Resumed", "Playback resumed.", COLOR_SUCCESS, "▶️"))
    else:
        await ctx.send(embed=build_error_embed("Nothing is paused."))


@bot.command(name="skip")
async def skip(ctx):
    vc = ctx.guild.voice_client
    if vc and (vc.is_playing() or vc.is_paused()):
        vc.stop()
        await ctx.send(embed=build_status_embed("Skipped", "Track skipped.", COLOR_SUCCESS, "⏭️"))
    else:
        await ctx.send(embed=build_error_embed("Nothing is playing."))


@bot.command(name="queue", aliases=["q"])
async def show_queue(ctx):
    await ctx.send(embed=build_queue_embed(get_state(ctx.guild.id)))


@bot.command(name="nowplaying", aliases=["np"])
async def now_playing(ctx):
    state = get_state(ctx.guild.id)
    vc    = ctx.guild.voice_client
    if vc and (vc.is_playing() or vc.is_paused()) and state.current:
        await ctx.send(embed=build_now_playing_embed(state.current, state), view=MusicControls(ctx.guild.id))
    else:
        await ctx.send(embed=build_error_embed("Nothing is playing."))


@bot.command(name="loop")
async def loop_cmd(ctx):
    state              = get_state(ctx.guild.id)
    state.loop_current = not state.loop_current
    status = "enabled" if state.loop_current else "disabled"
    color  = COLOR_SUCCESS if state.loop_current else COLOR_WARNING
    await ctx.send(embed=build_status_embed("Loop", f"Loop **{status}**.", color, "🔁"))


@bot.command(name="shuffle")
async def shuffle_cmd(ctx):
    state = get_state(ctx.guild.id)
    if len(state.queue) < 2:
        await ctx.send(embed=build_error_embed("Need 2+ songs to shuffle."))
        return
    items = list(state.queue)
    random.shuffle(items)
    state.queue = deque(items)
    await ctx.send(embed=build_status_embed("Shuffled", f"{len(items)} tracks shuffled.", COLOR_SUCCESS, "🔀"))


@bot.command(name="stop")
async def stop(ctx):
    state         = get_state(ctx.guild.id)
    state.queue.clear()
    state.current = None
    vc = ctx.guild.voice_client
    if vc:
        vc.stop()
    await ctx.send(embed=build_status_embed("Stopped", "Playback stopped and queue cleared.", COLOR_DANGER, "⏹️"))


@bot.command(name="leave")
async def leave(ctx):
    state = get_state(ctx.guild.id)
    vc    = ctx.guild.voice_client
    if vc:
        state.queue.clear()
        state.current = None
        await vc.disconnect()
        await ctx.send(embed=build_status_embed("Disconnected", "Left the voice channel. 👋", COLOR_QUEUE_LIST, "👋"))
    else:
        await ctx.send(embed=build_error_embed("Not in a voice channel."))


if __name__ == "__main__":
    if not TOKEN:
        raise RuntimeError("DISCORD_TOKEN not found. Check your .env file.")
    bot.run(TOKEN)