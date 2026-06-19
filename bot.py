"""
PixiesMusic - A Discord Music Bot
Commands: !play, !pause, !resume, !skip, !queue, !stop, !leave, !nowplaying

Now Playing messages use an embed + interactive buttons:
Pause/Resume, Skip, Stop, Loop toggle, Shuffle queue.
"""

import os
import random
import asyncio
from collections import deque
from datetime import datetime

import discord
from discord.ext import commands
from dotenv import load_dotenv
import yt_dlp

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

# ---------- Intents & Bot setup ----------
intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

# ---------- Embed palette ----------
COLOR_NOW_PLAYING  = 0x6C63FF   # violet — signature track accent
COLOR_QUEUED       = 0x22D3EE   # cyan  — item added
COLOR_QUEUE_LIST   = 0x818CF8   # indigo — list view
COLOR_SUCCESS      = 0x4ADE80   # green  — confirmations
COLOR_WARNING      = 0xFBBF24   # amber  — warnings / errors
COLOR_DANGER       = 0xF87171   # red    — stop / leave

BOT_DISPLAY_NAME   = "PixiesMusic"
BOT_ICON_URL       = "https://cdn.discordapp.com/embed/avatars/0.png"  # fallback; replace with your bot's avatar URL

MUSIC_NOTES        = ["🎵", "🎶", "🎸", "🎹", "🎺", "🎻", "🥁"]

# ---------- yt-dlp / ffmpeg options ----------
YTDL_OPTS = {
    "format": "bestaudio/best",
    "noplaylist": True,
    "quiet": True,
    "default_search": "ytsearch",
    "source_address": "0.0.0.0",
}

FFMPEG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ffmpeg.exe")
if not os.path.isfile(FFMPEG_PATH):
    FFMPEG_PATH = "ffmpeg"

FFMPEG_OPTS = {
    "executable": FFMPEG_PATH,
    "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
    "options": "-vn",
}

ytdl = yt_dlp.YoutubeDL(YTDL_OPTS)


# ════════════════════════════════════════════
#  Helpers
# ════════════════════════════════════════════

def format_duration(seconds: int) -> str:
    """Convert raw seconds → m:ss or h:mm:ss string."""
    if not seconds:
        return "∞  Live"
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s   = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def progress_bar(position: int, total: int, length: int = 18) -> str:
    """
    Render a Unicode progress bar.
    position and total are both in seconds.
    Returns something like: ━━━━━━●────────────  1:23 / 3:45
    """
    if not total:
        return "▬" * length + "  Live"

    ratio    = max(0.0, min(1.0, position / total))
    filled   = int(ratio * length)
    bar      = "━" * filled + "●" + "─" * (length - filled)
    pos_str  = format_duration(position)
    tot_str  = format_duration(total)
    return f"`{bar}`  {pos_str} / {tot_str}"


def timestamp_now() -> str:
    return datetime.utcnow().strftime("%H:%M UTC")


def random_note() -> str:
    return random.choice(MUSIC_NOTES)


# ════════════════════════════════════════════
#  Per-guild state
# ════════════════════════════════════════════

class MusicState:
    def __init__(self, guild_id: int):
        self.guild_id              = guild_id
        self.queue: deque[dict]    = deque()
        self.current: dict | None  = None
        self.loop_current: bool    = False
        self.now_playing_message: discord.Message | None = None
        self.text_channel: discord.abc.Messageable | None = None
        self.start_time: float     = 0.0   # monotonic, for progress bar


guild_states: dict[int, MusicState] = {}


def get_state(guild_id: int) -> MusicState:
    if guild_id not in guild_states:
        guild_states[guild_id] = MusicState(guild_id)
    return guild_states[guild_id]


async def fetch_song(query: str, requester: discord.Member) -> dict:
    loop = asyncio.get_event_loop()

    def extract():
        info = ytdl.extract_info(query, download=False)
        if "entries" in info:
            info = info["entries"][0]
        return info

    info = await loop.run_in_executor(None, extract)
    return {
        "url":         info["url"],
        "title":       info.get("title", "Unknown title"),
        "webpage_url": info.get("webpage_url", ""),
        "duration":    info.get("duration", 0),
        "uploader":    info.get("uploader", "Unknown"),
        "thumbnail":   info.get("thumbnail"),
        "requester":   requester,
        "view_count":  info.get("view_count", 0),
        "like_count":  info.get("like_count", 0),
    }


# ════════════════════════════════════════════
#  Embed builders
# ════════════════════════════════════════════

def build_now_playing_embed(song: dict, state: MusicState) -> discord.Embed:
    """Rich violet Now Playing card with progress bar, metadata, and loop badge."""
    note  = random_note()
    title = song["title"]
    url   = song["webpage_url"]
    dur   = song["duration"]

    # Elapsed time approximation
    elapsed = int(asyncio.get_event_loop().time() - state.start_time) if state.start_time else 0
    elapsed = min(elapsed, dur) if dur else elapsed

    loop_badge  = "  `🔁 LOOP`" if state.loop_current else ""
    queue_count = len(state.queue)

    embed = discord.Embed(
        title       = f"{note}  Now Playing{loop_badge}",
        description = f"### [{title}]({url})",
        color       = COLOR_NOW_PLAYING,
        timestamp   = datetime.utcnow(),
    )

    # Progress bar row
    embed.add_field(
        name   = "Progress",
        value  = progress_bar(elapsed, dur),
        inline = False,
    )

    # Metadata row 1
    embed.add_field(name="⏱  Duration",      value=f"`{format_duration(dur)}`",              inline=True)
    embed.add_field(name="🎤  Artist",         value=f"`{song.get('uploader', 'Unknown')}`",  inline=True)
    embed.add_field(name="📥  Requested by",   value=song["requester"].mention,                inline=True)

    # Metadata row 2
    views = song.get("view_count") or 0
    likes = song.get("like_count") or 0
    embed.add_field(name="👁  Views",  value=f"`{views:,}`" if views else "`—`",  inline=True)
    embed.add_field(name="👍  Likes",  value=f"`{likes:,}`" if likes else "`—`",  inline=True)
    embed.add_field(name="📋  In Queue", value=f"`{queue_count} track{'s' if queue_count != 1 else ''}`", inline=True)

    if song.get("thumbnail"):
        embed.set_thumbnail(url=song["thumbnail"])

    embed.set_author(name=f"{BOT_DISPLAY_NAME}  •  Music Player", icon_url=BOT_ICON_URL)
    embed.set_footer(text=f"Use the buttons below to control playback  •  {timestamp_now()}")
    return embed


def build_added_to_queue_embed(song: dict, position: int) -> discord.Embed:
    """Cyan confirmation card shown when a track is queued."""
    embed = discord.Embed(
        title       = "🎵  Added to Queue",
        description = f"**[{song['title']}]({song['webpage_url']})**",
        color       = COLOR_QUEUED,
        timestamp   = datetime.utcnow(),
    )
    embed.add_field(name="⏱  Duration",    value=f"`{format_duration(song['duration'])}`", inline=True)
    embed.add_field(name="🎤  Artist",      value=f"`{song.get('uploader', 'Unknown')}`",  inline=True)
    embed.add_field(name="🔢  Position",    value=f"`#{position}`",                          inline=True)
    embed.add_field(name="📥  Requested by", value=song["requester"].mention,                inline=False)

    if song.get("thumbnail"):
        embed.set_thumbnail(url=song["thumbnail"])

    embed.set_author(name=BOT_DISPLAY_NAME, icon_url=BOT_ICON_URL)
    embed.set_footer(text=f"Queue position {position}  •  {timestamp_now()}")
    return embed


def build_queue_embed(state: MusicState) -> discord.Embed:
    """Indigo paginated-style queue list."""
    embed = discord.Embed(
        title       = "📋  Current Queue",
        color       = COLOR_QUEUE_LIST,
        timestamp   = datetime.utcnow(),
    )

    if state.current:
        cur = state.current
        embed.add_field(
            name   = "▶️  Now Playing",
            value  = f"[{cur['title']}]({cur['webpage_url']})  •  `{format_duration(cur['duration'])}`",
            inline = False,
        )

    if state.queue:
        total_dur = sum(s.get("duration", 0) for s in state.queue)
        lines = []
        for i, song in enumerate(list(state.queue)[:15], 1):  # cap at 15
            dur_str = format_duration(song["duration"])
            lines.append(f"`{i:>2}.`  **{song['title']}**  •  `{dur_str}`")

        if len(state.queue) > 15:
            lines.append(f"\n*… and {len(state.queue) - 15} more tracks*")

        embed.add_field(
            name   = f"⏩  Up Next  —  {len(state.queue)} track{'s' if len(state.queue) != 1 else ''}  •  `{format_duration(total_dur)}` total",
            value  = "\n".join(lines),
            inline = False,
        )
    else:
        embed.add_field(name="Up Next", value="*Queue is empty — add songs with* `!play`", inline=False)

    embed.set_author(name=BOT_DISPLAY_NAME, icon_url=BOT_ICON_URL)
    embed.set_footer(text=f"Loop: {'ON 🔁' if state.loop_current else 'OFF'}  •  {timestamp_now()}")
    return embed


def build_status_embed(
    title: str,
    description: str,
    color: int = COLOR_SUCCESS,
    emoji: str = "✅",
) -> discord.Embed:
    """Generic one-liner status card (pause, resume, skip, stop, etc.)."""
    embed = discord.Embed(
        title       = f"{emoji}  {title}",
        description = description,
        color       = color,
        timestamp   = datetime.utcnow(),
    )
    embed.set_author(name=BOT_DISPLAY_NAME, icon_url=BOT_ICON_URL)
    embed.set_footer(text=timestamp_now())
    return embed


def build_error_embed(description: str) -> discord.Embed:
    return build_status_embed("Oops!", description, color=COLOR_WARNING, emoji="⚠️")


# ════════════════════════════════════════════
#  Interactive Controls View
# ════════════════════════════════════════════

class MusicControls(discord.ui.View):
    def __init__(self, guild_id: int):
        super().__init__(timeout=None)
        self.guild_id = guild_id

    def _vc(self, interaction: discord.Interaction):
        return interaction.guild.voice_client

    @discord.ui.button(label="Pause / Resume", emoji="⏯️", style=discord.ButtonStyle.primary, row=0)
    async def pause_resume(self, interaction: discord.Interaction, button: discord.ui.Button):
        vc = self._vc(interaction)
        if not vc:
            await interaction.response.send_message(embed=build_error_embed("Not connected to a voice channel."), ephemeral=True)
            return
        if vc.is_playing():
            vc.pause()
            await interaction.response.send_message(embed=build_status_embed("Paused", "Playback paused.", COLOR_WARNING, "⏸️"), ephemeral=True)
        elif vc.is_paused():
            vc.resume()
            await interaction.response.send_message(embed=build_status_embed("Resumed", "Playback resumed.", COLOR_SUCCESS, "▶️"), ephemeral=True)
        else:
            await interaction.response.send_message(embed=build_error_embed("Nothing is currently playing."), ephemeral=True)

    @discord.ui.button(label="Skip", emoji="⏭️", style=discord.ButtonStyle.secondary, row=0)
    async def skip(self, interaction: discord.Interaction, button: discord.ui.Button):
        vc = self._vc(interaction)
        if vc and (vc.is_playing() or vc.is_paused()):
            vc.stop()
            await interaction.response.send_message(embed=build_status_embed("Skipped", "Track skipped — up next shortly.", COLOR_SUCCESS, "⏭️"), ephemeral=True)
        else:
            await interaction.response.send_message(embed=build_error_embed("Nothing is currently playing."), ephemeral=True)

    @discord.ui.button(label="Stop", emoji="⏹️", style=discord.ButtonStyle.danger, row=0)
    async def stop(self, interaction: discord.Interaction, button: discord.ui.Button):
        state = get_state(self.guild_id)
        state.queue.clear()
        state.current = None
        vc = self._vc(interaction)
        if vc:
            vc.stop()
        await interaction.response.send_message(
            embed=build_status_embed("Stopped", "Playback stopped and queue cleared.", COLOR_DANGER, "⏹️"),
            ephemeral=True,
        )

    @discord.ui.button(label="Loop", emoji="🔁", style=discord.ButtonStyle.secondary, row=1)
    async def loop_toggle(self, interaction: discord.Interaction, button: discord.ui.Button):
        state = get_state(self.guild_id)
        state.loop_current = not state.loop_current
        status = "enabled" if state.loop_current else "disabled"
        color  = COLOR_SUCCESS if state.loop_current else COLOR_WARNING
        await interaction.response.send_message(
            embed=build_status_embed("Loop", f"Loop has been **{status}**.", color, "🔁"),
            ephemeral=True,
        )
        if state.now_playing_message and state.current:
            embed = build_now_playing_embed(state.current, state)
            await state.now_playing_message.edit(embed=embed)

    @discord.ui.button(label="Shuffle", emoji="🔀", style=discord.ButtonStyle.secondary, row=1)
    async def shuffle(self, interaction: discord.Interaction, button: discord.ui.Button):
        state = get_state(self.guild_id)
        if len(state.queue) < 2:
            await interaction.response.send_message(embed=build_error_embed("Need at least 2 songs in the queue to shuffle."), ephemeral=True)
            return
        items = list(state.queue)
        random.shuffle(items)
        state.queue = deque(items)
        await interaction.response.send_message(
            embed=build_status_embed("Shuffled", f"Queue of **{len(items)}** tracks shuffled.", COLOR_SUCCESS, "🔀"),
            ephemeral=True,
        )

    @discord.ui.button(label="Queue", emoji="📋", style=discord.ButtonStyle.secondary, row=1)
    async def show_queue_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        state = get_state(self.guild_id)
        await interaction.response.send_message(embed=build_queue_embed(state), ephemeral=True)


# ════════════════════════════════════════════
#  Playback engine
# ════════════════════════════════════════════

def play_next(guild: discord.Guild):
    state        = get_state(guild.id)
    voice_client = guild.voice_client

    if voice_client is None:
        return

    if state.loop_current and state.current is not None:
        song = state.current
    elif state.queue:
        song         = state.queue.popleft()
        state.current = song
    else:
        state.current = None
        # Post a "Queue finished" notice
        async def notify_done():
            if state.text_channel:
                embed = build_status_embed(
                    "Queue Finished",
                    "All tracks have been played. Add more with `!play`!",
                    COLOR_QUEUE_LIST,
                    "🎶",
                )
                await state.text_channel.send(embed=embed)
        asyncio.run_coroutine_threadsafe(notify_done(), bot.loop)
        return

    source = discord.FFmpegPCMAudio(song["url"], **FFMPEG_OPTS)

    def after_play(error):
        if error:
            print(f"Playback error: {error}")
        play_next(guild)

    voice_client.play(source, after=after_play)
    state.start_time = asyncio.get_event_loop().time()

    async def send_now_playing():
        embed = build_now_playing_embed(song, state)
        view  = MusicControls(guild.id)
        if state.text_channel:
            msg = await state.text_channel.send(embed=embed, view=view)
            state.now_playing_message = msg

    asyncio.run_coroutine_threadsafe(send_now_playing(), bot.loop)


# ════════════════════════════════════════════
#  Events
# ════════════════════════════════════════════

@bot.event
async def on_ready():
    print(f"{BOT_DISPLAY_NAME} is online as {bot.user}")
    await bot.change_presence(
        activity=discord.Activity(type=discord.ActivityType.listening, name="!play")
    )


# ════════════════════════════════════════════
#  Commands
# ════════════════════════════════════════════

@bot.command(name="play", help="Play a song by name or YouTube URL")
async def play(ctx, *, query: str):
    if not ctx.author.voice:
        await ctx.send(embed=build_error_embed("You need to join a voice channel first."))
        return

    voice_channel = ctx.author.voice.channel
    voice_client  = ctx.guild.voice_client

    if voice_client is None:
        voice_client = await voice_channel.connect()
    elif voice_client.channel != voice_channel:
        await voice_client.move_to(voice_channel)

    state             = get_state(ctx.guild.id)
    state.text_channel = ctx.channel

    searching_embed = discord.Embed(
        title       = "🔎  Searching…",
        description = f"Looking up **{query}**",
        color       = COLOR_QUEUED,
    )
    searching_embed.set_author(name=BOT_DISPLAY_NAME, icon_url=BOT_ICON_URL)
    searching_msg = await ctx.send(embed=searching_embed)

    try:
        song = await fetch_song(query, ctx.author)
    except Exception as e:
        await searching_msg.edit(embed=build_error_embed(f"Couldn't find that track.\n`{e}`"))
        return

    state.queue.append(song)
    position = len(state.queue)

    await searching_msg.edit(embed=build_added_to_queue_embed(song, position))

    if not voice_client.is_playing() and not voice_client.is_paused():
        play_next(ctx.guild)


@bot.command(name="pause", help="Pause the current song")
async def pause(ctx):
    vc = ctx.guild.voice_client
    if vc and vc.is_playing():
        vc.pause()
        await ctx.send(embed=build_status_embed("Paused", "Playback paused.", COLOR_WARNING, "⏸️"))
    else:
        await ctx.send(embed=build_error_embed("Nothing is playing right now."))


@bot.command(name="resume", help="Resume the paused song")
async def resume(ctx):
    vc = ctx.guild.voice_client
    if vc and vc.is_paused():
        vc.resume()
        await ctx.send(embed=build_status_embed("Resumed", "Playback resumed.", COLOR_SUCCESS, "▶️"))
    else:
        await ctx.send(embed=build_error_embed("Nothing is paused right now."))


@bot.command(name="skip", help="Skip the current song")
async def skip(ctx):
    vc = ctx.guild.voice_client
    if vc and (vc.is_playing() or vc.is_paused()):
        vc.stop()
        await ctx.send(embed=build_status_embed("Skipped", "Track skipped.", COLOR_SUCCESS, "⏭️"))
    else:
        await ctx.send(embed=build_error_embed("Nothing is playing right now."))


@bot.command(name="queue", aliases=["q"], help="Show the current song queue")
async def show_queue(ctx):
    state = get_state(ctx.guild.id)
    await ctx.send(embed=build_queue_embed(state))


@bot.command(name="nowplaying", aliases=["np"], help="Show the currently playing song")
async def now_playing(ctx):
    state = get_state(ctx.guild.id)
    vc    = ctx.guild.voice_client
    if vc and (vc.is_playing() or vc.is_paused()) and state.current:
        await ctx.send(embed=build_now_playing_embed(state.current, state), view=MusicControls(ctx.guild.id))
    else:
        await ctx.send(embed=build_error_embed("Nothing is playing right now."))


@bot.command(name="loop", help="Toggle looping the current song")
async def loop_cmd(ctx):
    state              = get_state(ctx.guild.id)
    state.loop_current = not state.loop_current
    status = "enabled" if state.loop_current else "disabled"
    color  = COLOR_SUCCESS if state.loop_current else COLOR_WARNING
    await ctx.send(embed=build_status_embed("Loop", f"Loop has been **{status}**.", color, "🔁"))


@bot.command(name="shuffle", help="Shuffle the current queue")
async def shuffle_cmd(ctx):
    state = get_state(ctx.guild.id)
    if len(state.queue) < 2:
        await ctx.send(embed=build_error_embed("Need at least 2 songs in the queue to shuffle."))
        return
    items = list(state.queue)
    random.shuffle(items)
    state.queue = deque(items)
    await ctx.send(embed=build_status_embed("Shuffled", f"Queue of **{len(items)}** tracks has been shuffled.", COLOR_SUCCESS, "🔀"))


@bot.command(name="stop", help="Stop playback and clear the queue")
async def stop(ctx):
    state         = get_state(ctx.guild.id)
    state.queue.clear()
    state.current = None
    vc = ctx.guild.voice_client
    if vc:
        vc.stop()
    await ctx.send(embed=build_status_embed("Stopped", "Playback stopped and queue cleared.", COLOR_DANGER, "⏹️"))


@bot.command(name="leave", help="Disconnect the bot from voice")
async def leave(ctx):
    vc    = ctx.guild.voice_client
    state = get_state(ctx.guild.id)
    if vc:
        state.queue.clear()
        state.current = None
        await vc.disconnect()
        await ctx.send(embed=build_status_embed("Disconnected", "Left the voice channel. See you next time! 👋", COLOR_QUEUE_LIST, "👋"))
    else:
        await ctx.send(embed=build_error_embed("I'm not in a voice channel."))


if __name__ == "__main__":
    if not TOKEN:
        raise RuntimeError("DISCORD_TOKEN not found. Check your .env file.")
    bot.run(TOKEN)