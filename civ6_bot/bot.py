import asyncio
import sys
import traceback
import logging
import subprocess

try:
    import discord
except ImportError:
    subprocess.check_call([sys.executable, "-m", "pip", "install", "discord.py", "python-dotenv", "Pillow"])
    import discord

try:
    from PIL import Image  # noqa: F401 — ensure Pillow is installed at startup
except ImportError:
    subprocess.check_call([sys.executable, "-m", "pip", "install", "Pillow"])
from discord import app_commands
from discord.ext import commands
import random
import string
import re
from collections import Counter
import os
from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

from leaders import CIVS, LEADERS_BY_CIV, image_url
from civ_emojis import CIV_EMOJIS
from leader_emojis import LEADER_EMOJI_NAMES
import database as db

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

# ---------------------------------------------------------------------------
# All leaders flat list
# ---------------------------------------------------------------------------
ALL_LEADERS: list[tuple[str, str]] = sorted(
    [
        (civ, leader)
        for civ, leaders in LEADERS_BY_CIV.items()
        for leader in leaders
    ],
    key=lambda x: x[1],  # lider adına göre alfabetik
)

# Civ pages for select menus (max 25 options)
_CIV_PAGES: list[list[str]] = [CIVS[i : i + 25] for i in range(0, len(CIVS), 25)]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
VICTORY_TYPES: list[tuple[str, str]] = [
    ("🚀", "Bilim"),
    ("🎭", "Kültür"),
    ("⚔️", "Askeri"),
    ("🕌", "Din"),
    ("🕊️", "Diplomatik"),
    ("🏆", "Puan"),
    ("🏳️", "Teslim (CC)"),
]

MAPS = [
    ("Lakes",                      "🏞️"),
    ("Pangea Ultima",              "🌍"),
    ("Rich Highlands",             "⛰️"),
    ("Tilted Axis (Wraparound)",   "🌐"),
    ("Continents and Islands",     "🏝️"),
    ("Seven Seas",                 "🌊"),
    ("Primordial",                 "🌋"),
]

PLAYER_COLORS = [
    discord.Color.gold(),
    discord.Color.blue(),
    discord.Color.red(),
    discord.Color.green(),
    discord.Color.purple(),
    discord.Color.orange(),
    discord.Color.teal(),
    discord.Color.magenta(),
    discord.Color.from_rgb(255, 165, 0),
    discord.Color.from_rgb(0, 206, 209),
    discord.Color.from_rgb(220, 20, 60),
    discord.Color.from_rgb(50, 205, 50),
]

TEAM_COLORS = [
    discord.Color.red(),
    discord.Color.blue(),
    discord.Color.yellow(),
    discord.Color.green(),
    discord.Color.purple(),
    discord.Color.orange(),
]
TEAM_EMOJIS = ["🔴", "🔵", "🟡", "🟢", "🟣", "🟠"]

# Active FFA games per channel
active_ffa_games: dict[int, "FFAGame"] = {}

# ---------------------------------------------------------------------------
# Bot setup
# ---------------------------------------------------------------------------
intents = discord.Intents.default()
intents.members = True
intents.voice_states = True
intents.reactions = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

# ---------------------------------------------------------------------------
# Interaction error helper
# ---------------------------------------------------------------------------

async def _safe_send(interaction: discord.Interaction, content: str, ephemeral: bool = True):
    """Etkileşim süresi dolmuş veya zaten yanıtlanmışsa sessizce geç."""
    try:
        if interaction.response.is_done():
            await interaction.followup.send(content, ephemeral=ephemeral)
        else:
            await interaction.response.send_message(content, ephemeral=ephemeral)
    except (discord.NotFound, discord.InteractionResponded, discord.HTTPException):
        pass


@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    logging.error("App command error: %s", error, exc_info=error)
    await _safe_send(interaction, "❌ Bir hata oluştu, lütfen tekrar dene.")


# View hataları @bot.tree.error'a düşmez, ayrıca yakala
_orig_view_on_error = discord.ui.View.on_error

async def _global_view_on_error(self, interaction: discord.Interaction, error: Exception, item):
    logging.error("View error in item=%s: %s", item, error)
    traceback.print_exception(type(error), error, error.__traceback__)
    try:
        if not interaction.response.is_done():
            await interaction.response.send_message("❌ Bir hata oluştu, tekrar dene.", ephemeral=True)
        else:
            await interaction.followup.send("❌ Bir hata oluştu, tekrar dene.", ephemeral=True)
    except Exception:
        pass

discord.ui.View.on_error = _global_view_on_error

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def get_voice_members(interaction: discord.Interaction) -> list[discord.Member]:
    member = interaction.guild.get_member(interaction.user.id)
    if member is None or member.voice is None or member.voice.channel is None:
        return []
    return [m for m in member.voice.channel.members if not m.bot]


def _make_match_id(prefix: str) -> str:
    """Generate a short random match ID like FFA-A3K7X2B9C1D4E5."""
    code = "".join(random.choices(string.ascii_uppercase + string.digits, k=16))
    return f"{prefix}-{code}"


def emoji_to_civ(emoji_str: str) -> str | None:
    """Return the civ name that matches this emoji string, or None."""
    for civ, emoji in CIV_EMOJIS.items():
        if emoji and str(emoji) == emoji_str:
            return civ
    return None


def civ_emoji_str(civ: str) -> str:
    """Return the configured emoji string for a civ, or empty string."""
    return CIV_EMOJIS.get(civ) or ""


def leader_emoji_str(leader: str, guild: discord.Guild | None = None) -> str:
    """Return the Discord emoji string for a leader, resolved live from the guild."""
    name = LEADER_EMOJI_NAMES.get(leader)
    if not name:
        return ""
    # Önce guild'e bak, sonra botun eriştiği tüm sunuculara
    emoji = (discord.utils.get(guild.emojis, name=name) if guild else None) \
            or discord.utils.get(bot.emojis, name=name)
    return str(emoji) if emoji else ""


def build_pool_embed(
    member: discord.Member,
    pool: list[tuple[str, str]],
    color: discord.Color,
    guild: discord.Guild | None = None,
) -> discord.Embed:
    lines = []
    for civ, leader in sorted(pool, key=lambda x: x[1]):
        emoji = leader_emoji_str(leader, guild)
        label = f"{emoji} {leader}".strip() if emoji else leader
        lines.append(f"{label} — {civ}")
    embed = discord.Embed(
        title=f"🎴 {member.display_name}  ({len(pool)} lider)",
        description="\n".join(lines) or "—",
        color=color,
    )
    if pool:
        embed.set_thumbnail(url=image_url(*pool[0]))
    return embed


async def send_embeds(
    channel: discord.TextChannel,
    embeds: list[discord.Embed],
    content: str = "",
):
    """Send embeds in chunks of 10 (Discord limit)."""
    first, rest = embeds[:10], embeds[10:]
    await channel.send(content=content or None, embeds=first)
    for i in range(0, len(rest), 10):
        await channel.send(embeds=rest[i : i + 10])


def _distribute_leaders(
    members: list[discord.Member],
    banned_civs: set[str] = frozenset(),
    banned_pairs: set[tuple[str, str]] | None = None,
) -> dict[discord.Member, list[tuple[str, str]]]:
    """Shuffle remaining leaders and deal them as evenly as possible."""
    if banned_pairs is not None:
        remaining = [p for p in ALL_LEADERS if p not in banned_pairs]
    else:
        remaining = [(c, l) for c, l in ALL_LEADERS if c not in banned_civs]
    random.shuffle(remaining)
    n = len(members)
    per_player = len(remaining) // n
    remaining = remaining[:n * per_player]  # discard remainder so all players get equal pools
    pools = {m: remaining[i * per_player : (i + 1) * per_player] for i, m in enumerate(members)}
    return pools


# ===========================================================================
# FFA GAME — new flow: map vote → per-player civ ban → pool distribution
# ===========================================================================

class FFAGame:
    def __init__(self, players: list[discord.Member]):
        self.players = players
        self.map_votes: dict[int, str] = {}   # player_id -> map_name
        self.selected_map: str | None = None
        self.bans: dict[int, tuple[str, str]] = {}  # player_id -> (civ, leader)

    # ---- map phase ----

    def record_map_vote(self, player_id: int, map_name: str):
        self.map_votes[player_id] = map_name

    def all_map_votes_done(self) -> bool:
        return len(self.map_votes) == len(self.players)

    def get_winning_map(self) -> str:
        counts = Counter(self.map_votes.values())
        max_votes = max(counts.values())
        tied = [m for m, v in counts.items() if v == max_votes]
        return random.choice(tied)

    # ---- ban phase ----

    def record_ban(self, player_id: int, pair: tuple[str, str]):
        self.bans[player_id] = pair

    def all_bans_done(self) -> bool:
        return len(self.bans) == len(self.players)

    def get_banned_pairs(self) -> set[tuple[str, str]]:
        return set(self.bans.values())

    # ---- pool distribution ----

    def distribute_pools(self) -> dict[discord.Member, list[tuple[str, str]]]:
        return _distribute_leaders(self.players, banned_pairs=self.get_banned_pairs())


# ---------------------------------------------------------------------------
# Map Selection View
# ---------------------------------------------------------------------------

class MapSelectionView(discord.ui.View):
    def __init__(self, game: FFAGame):
        super().__init__(timeout=None)
        self.game = game
        for map_name, emoji in MAPS:
            btn = discord.ui.Button(
                label=map_name,
                emoji=emoji,
                style=discord.ButtonStyle.primary,
            )
            btn.callback = self._make_vote_cb(map_name)
            self.add_item(btn)

    def build_embed(self) -> discord.Embed:
        counts = Counter(self.game.map_votes.values())
        n = len(self.game.players)
        lines = []
        for map_name, emoji in MAPS:
            c = counts.get(map_name, 0)
            bar = "█" * c + "░" * (n - c)
            lines.append(f"{emoji} **{map_name}** — {c} oy  `{bar}`")

        embed = discord.Embed(
            title="🗺️ Harita Seçimi",
            description="\n".join(lines),
            color=discord.Color.blue(),
        )
        not_voted = [p for p in self.game.players if p.id not in self.game.map_votes]
        if not_voted:
            embed.add_field(
                name="⏳ Oy Bekleniyorlar",
                value=" ".join(m.mention for m in not_voted),
                inline=False,
            )
        return embed

    def _make_vote_cb(self, map_name: str):
        async def callback(interaction: discord.Interaction):
            if not any(p.id == interaction.user.id for p in self.game.players):
                await interaction.response.send_message(
                    "Bu oyuna dahil değilsin!", ephemeral=True
                )
                return

            self.game.record_map_vote(interaction.user.id, map_name)
            embed = self.build_embed()

            if self.game.all_map_votes_done():
                self.game.selected_map = self.game.get_winning_map()
                for item in self.children:
                    item.disabled = True
                embed.color = discord.Color.green()
                embed.title = f"✅ Harita Seçildi: **{self.game.selected_map}**"
                await interaction.response.edit_message(embed=embed, view=self)
                await _start_ban_phase(interaction.channel, self.game)
            else:
                await interaction.response.edit_message(embed=embed, view=self)

        return callback


# ---------------------------------------------------------------------------
# Shared Ban Phase View  (tek mesaj, herkes aynı butona basar)
# ---------------------------------------------------------------------------

def _find_leader(name: str) -> tuple[str, str] | None:
    """Case-insensitive leader name lookup. Returns (civ, leader) or None."""
    name_lower = name.strip().lower()
    for civ, leader in ALL_LEADERS:
        if leader.lower() == name_lower:
            return civ, leader
    matches = [(c, l) for c, l in ALL_LEADERS if name_lower in l.lower()]
    return matches[0] if len(matches) == 1 else None


class LeaderSelectView(discord.ui.View):
    """Paginated leader select. Calls on_pick(inter, civ, leader) on selection."""

    def __init__(self, available: list[tuple[str, str]], on_pick, page: int = 0):
        super().__init__(timeout=600)   # 10 dk — uzun sessionlar için
        self.available = available
        self.on_pick = on_pick
        self.page = page
        self._rebuild()

    def _rebuild(self):
        self.clear_items()
        pages = [self.available[i:i+25] for i in range(0, len(self.available), 25)]
        if not pages:
            return
        page_leaders = pages[self.page]
        sel = discord.ui.Select(
            placeholder=f"Lider seç — Sayfa {self.page+1}/{len(pages)}",
            options=[
                discord.SelectOption(label=l, value=f"{c}||{l}", description=c)
                for c, l in page_leaders
            ],
        )
        sel.callback = self._on_select
        self.add_item(sel)
        if self.page > 0:
            btn = discord.ui.Button(label="◀", style=discord.ButtonStyle.secondary)
            btn.callback = self._prev
            self.add_item(btn)
        if self.page < len(pages) - 1:
            btn = discord.ui.Button(label="▶", style=discord.ButtonStyle.secondary)
            btn.callback = self._next
            self.add_item(btn)

    async def _safe_respond(self, interaction: discord.Interaction, msg: str):
        """Herhangi bir hata durumunda interaction'a sessizce yanıt ver."""
        try:
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)
        except Exception:
            pass

    async def on_error(self, interaction: discord.Interaction, error: Exception, item):
        """Herhangi bir exception interaction'ı cevapsız bırakmasın."""
        await self._safe_respond(interaction, "❌ Bir hata oluştu, tekrar dene.")

    async def on_timeout(self):
        """Süre dolunca view'i mesajdan kaldır."""
        if self.message:
            try:
                await self.message.edit(view=None)
            except Exception:
                pass

    async def _on_select(self, interaction: discord.Interaction):
        try:
            val = interaction.data["values"][0]
            civ, leader = val.split("||", 1)
            await self.on_pick(interaction, civ, leader)
        except (discord.NotFound, discord.InteractionResponded, discord.HTTPException):
            pass
        except Exception:
            await self._safe_respond(interaction, "❌ Bir hata oluştu, tekrar dene.")

    async def _prev(self, interaction: discord.Interaction):
        try:
            self.page -= 1
            self._rebuild()
            await interaction.response.edit_message(view=self)
        except (discord.NotFound, discord.InteractionResponded, discord.HTTPException):
            pass
        except Exception:
            await self._safe_respond(interaction, "❌ Bir hata oluştu.")

    async def _next(self, interaction: discord.Interaction):
        try:
            self.page += 1
            self._rebuild()
            await interaction.response.edit_message(view=self)
        except (discord.NotFound, discord.InteractionResponded, discord.HTTPException):
            pass
        except Exception:
            await self._safe_respond(interaction, "❌ Bir hata oluştu.")


class BanPhaseView(discord.ui.View):
    """Single message shared by all players. Each clicks Ban, types leader name in modal."""

    def __init__(self, game: FFAGame):
        super().__init__(timeout=None)
        self.game = game
        self.message: discord.Message | None = None

    def build_embed(self) -> discord.Embed:
        status_lines = []
        for player in self.game.players:
            if player.id in self.game.bans:
                civ, leader = self.game.bans[player.id]
                status_lines.append(f"✅ {player.mention} → **{leader}** ({civ})")
            else:
                status_lines.append(f"⏳ {player.mention}")

        used_pairs = self.game.get_banned_pairs()
        available = [(c, l) for c, l in ALL_LEADERS if (c, l) not in used_pairs]
        leader_list = "\n".join(f"`{l}` — {c}" for c, l in available[:50])
        if len(available) > 50:
            leader_list += f"\n*...ve {len(available)-50} lider daha*"

        embed = discord.Embed(
            title="🚫 Lider Ban Aşaması",
            description="**🚫 Ban Yap** butonuna bas, listeden lider seç.",
            color=discord.Color.orange(),
        )
        embed.add_field(name="Durum", value="\n".join(status_lines), inline=False)
        embed.add_field(name="Mevcut Liderler", value=leader_list or "—", inline=False)
        return embed

    @discord.ui.button(label="🚫 Ban Yap", style=discord.ButtonStyle.danger)
    async def ban_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        player = next((p for p in self.game.players if p.id == interaction.user.id), None)
        if not player:
            await interaction.response.send_message("Bu oyuna dahil değilsin!", ephemeral=True)
            return
        if player.id in self.game.bans:
            await interaction.response.send_message("Zaten ban yaptın!", ephemeral=True)
            return

        game_ref = self.game
        view_ref = self
        used_pairs = game_ref.get_banned_pairs()
        available = [(c, l) for c, l in ALL_LEADERS if (c, l) not in used_pairs]

        async def on_pick(inter: discord.Interaction, civ: str, leader: str):
            try:
                if (civ, leader) in game_ref.get_banned_pairs():
                    await inter.response.edit_message(content=f"**{leader}** zaten banlandı!", view=None)
                    return
                game_ref.record_ban(player.id, (civ, leader))
                await inter.response.edit_message(content="\u200b", view=None)
                embed = view_ref.build_embed()
                if view_ref.message:
                    if game_ref.all_bans_done():
                        await view_ref.message.edit(embed=embed, view=view_ref)
                        await _finalize_ffa_pools(inter.channel, game_ref, ban_message=view_ref.message)
                    else:
                        await view_ref.message.edit(embed=embed, view=view_ref)
            except (discord.NotFound, discord.InteractionResponded, discord.HTTPException):
                pass

        try:
            await interaction.response.send_message(
                "Lideri seç:", view=LeaderSelectView(available, on_pick), ephemeral=True
            )
        except (discord.NotFound, discord.InteractionResponded, discord.HTTPException):
            pass


# ---------------------------------------------------------------------------
# FFA phase helpers
# ---------------------------------------------------------------------------

async def _start_ban_phase(channel: discord.TextChannel, game: FFAGame):
    view = BanPhaseView(game)
    msg = await channel.send(embed=view.build_embed(), view=view)
    view.message = msg


async def _finalize_ffa_pools(
    channel: discord.TextChannel,
    game: FFAGame,
    ban_message: discord.Message | None = None,
):
    pools = game.distribute_pools()
    mentions = " ".join(m.mention for m in game.players)

    banned_pairs = game.get_banned_pairs()
    ban_summary = "  ·  ".join(
        f"**{l}** ({c})" for c, l in sorted(banned_pairs)
    ) or "Yok"

    match_id = _make_match_id("FFA")
    header = discord.Embed(
        title=f"🗺️ {game.selected_map}  ·  ⚔️ FFA Draft Tamamlandı!",
        description=f"**Banlanan Liderler:** {ban_summary}",
        color=discord.Color.gold(),
    )
    header.set_footer(text=f"Maç ID: {match_id}")
    await channel.send(content=mentions, embed=header)

    for i, player in enumerate(game.players):
        embed = build_pool_embed(player, pools[player], PLAYER_COLORS[i % len(PLAYER_COLORS)], channel.guild)
        await channel.send(content=player.mention, embed=embed)

    # Delete the ban phase message to keep the channel clean
    if ban_message:
        try:
            await ban_message.delete()
        except discord.HTTPException:
            pass

    active_ffa_games.pop(channel.id, None)


class LeaderBanView(discord.ui.View):
    def __init__(self, session, civ: str, ban_view: "TeamBanPhaseView", guild: discord.Guild | None = None):
        super().__init__(timeout=120)
        self.session = session
        self.civ = civ
        self.ban_view = ban_view

        def _opt(l: str) -> discord.SelectOption:
            emoji_name = LEADER_EMOJI_NAMES.get(l)
            guild_emoji = discord.utils.get(guild.emojis, name=emoji_name) if guild and emoji_name else None
            return discord.SelectOption(
                label=l,
                value=l,
                emoji=guild_emoji,
                description="BANLI" if (civ, l) in session.banned else "",
                default=(civ, l) in session.banned,
            )

        options = [_opt(l) for l in LEADERS_BY_CIV[civ]]
        sel = discord.ui.Select(
            placeholder=f"{civ} — ban etmek istediklerini seç",
            options=options,
            min_values=0,
            max_values=len(options),
        )
        sel.callback = self._on_select
        self.add_item(sel)

        back = discord.ui.Button(label="◀ Geri", style=discord.ButtonStyle.secondary)
        back.callback = self._go_back
        self.add_item(back)

    async def _on_select(self, interaction: discord.Interaction):
        selected = set(interaction.data["values"])
        self.session.banned = {p for p in self.session.banned if p[0] != self.civ}
        for l in selected:
            self.session.banned.add((self.civ, l))
        self.ban_view._rebuild()
        await interaction.response.edit_message(content=self.session.ban_status(), view=self.ban_view)

    async def _go_back(self, interaction: discord.Interaction):
        self.ban_view._rebuild()
        await interaction.response.edit_message(content=self.session.ban_status(), view=self.ban_view)


class TeamBanPhaseView(discord.ui.View):
    def __init__(self, session):
        super().__init__(timeout=600)
        self.session = session
        self.page = 0
        self._rebuild()

    def _rebuild(self):
        self.clear_items()
        civs = _CIV_PAGES[self.page]
        sel = discord.ui.Select(
            placeholder=f"Lider seç — Sayfa {self.page + 1}/{len(_CIV_PAGES)}",
            options=[discord.SelectOption(label=c, value=c) for c in civs],
        )
        sel.callback = self._civ_chosen
        self.add_item(sel)

        if self.page > 0:
            prev = discord.ui.Button(label="◀", style=discord.ButtonStyle.secondary)
            prev.callback = self._prev
            self.add_item(prev)

        if self.page < len(_CIV_PAGES) - 1:
            nxt = discord.ui.Button(label="▶", style=discord.ButtonStyle.secondary)
            nxt.callback = self._next
            self.add_item(nxt)

        remaining = len(ALL_LEADERS) - len(self.session.banned)
        start = discord.ui.Button(
            label=f"✅ Draftı Başlat ({remaining} lider)",
            style=discord.ButtonStyle.success,
        )
        start.callback = self._start
        self.add_item(start)

    async def _prev(self, interaction: discord.Interaction):
        self.page -= 1
        self._rebuild()
        await interaction.response.edit_message(content=self.session.ban_status(), view=self)

    async def _next(self, interaction: discord.Interaction):
        self.page += 1
        self._rebuild()
        await interaction.response.edit_message(content=self.session.ban_status(), view=self)

    async def _civ_chosen(self, interaction: discord.Interaction):
        civ = interaction.data["values"][0]
        await interaction.response.edit_message(
            content=f"**{civ}** liderlerinden ban etmek istediklerini seç:",
            view=LeaderBanView(self.session, civ, self, interaction.guild),
        )

    async def _start(self, interaction: discord.Interaction):
        self.stop()
        await self.session.finalize(interaction)



# ===========================================================================
# /team GAME — harita ban → civ ban → civ seçim (2 takım, sıralı aksiyonlar)
# ===========================================================================

active_team_games: dict[int, "TeamGame"] = {}


def _build_team_action_queue(team_size: int) -> list[tuple[str, int]]:
    """Return the full ordered sequence of (action_type, team_number) for a team game.

    Ban/Pick sırası:
    - 6 harita ban: T1,T2 sırayla
    - Lider ban aşaması 1: T1,T2,T1,T2,T1,T2 (6 ban)
    - Lider seçim aşaması 1: T1(1), T2(2), T1(1)
    - Lider ban aşaması 2: T2,T1,T2,T1 (4 ban)
    - Lider seçim aşaması 2: T2(1), T1(2), T2(2), T1(2)... bitene kadar
    """
    queue: list[tuple[str, int]] = []

    # 6 harita ban
    for i in range(6):
        queue.append(("map_ban", 1 if i % 2 == 0 else 2))

    # Lider ban aşaması 1: T1,T2,T1,T2,T1,T2
    for i in range(6):
        queue.append(("civ_ban", 1 if i % 2 == 0 else 2))

    # Lider seçim aşaması 1: T1(1), T2(2), T1(1)
    queue += [("civ_pick", 1), ("civ_pick", 2), ("civ_pick", 2), ("civ_pick", 1)]

    if team_size > 2:
        # Lider ban aşaması 2: T2,T1,T2,T1
        queue += [("civ_ban", 2), ("civ_ban", 1), ("civ_ban", 2), ("civ_ban", 1)]

        # Lider seçim aşaması 2: T2(1), sonra T1(2),T2(2) çiftleri halinde bitene kadar
        t1 = 2  # already picked in phase 1
        t2 = 2
        if t2 < team_size:
            queue.append(("civ_pick", 2))
            t2 += 1
        while t1 < team_size or t2 < team_size:
            for _ in range(min(2, team_size - t1)):
                queue.append(("civ_pick", 1))
                t1 += 1
            for _ in range(min(2, team_size - t2)):
                queue.append(("civ_pick", 2))
                t2 += 1

    return queue


class TeamGame:
    def __init__(
        self,
        all_players: list[discord.Member],
        rep1: discord.Member,   # /team komutunu açan
        rep2: discord.Member,   # etiketlenen kişi (takım seçer)
    ):
        self.all_players = all_players
        self.rep1 = rep1
        self.rep2 = rep2

        self.team1: list[discord.Member] = []
        self.team2: list[discord.Member] = []
        self.team1_rep: discord.Member | None = None
        self.team2_rep: discord.Member | None = None

        self.available_maps: list[str] = [name for name, _ in MAPS]
        self.selected_map: str | None = None
        self.map_bans: list[tuple[int, str]] = []   # (takım, harita)

        self.banned_leaders: list[tuple[int, str]] = []  # (takım, lider)
        self.picked_leaders: list[tuple[int, str]] = []  # (takım, lider)

        self.action_queue: list[tuple[str, int]] = []
        self.action_index: int = 0
        self.summary_msg: discord.Message | None = None
        self.prompt_msg: discord.Message | None = None

    @property
    def team_size(self) -> int:
        return len(self.all_players) // 2

    def get_rep(self, team: int) -> discord.Member:
        return self.team1_rep if team == 1 else self.team2_rep  # type: ignore

    def current_action(self) -> tuple[str, int] | None:
        if self.action_index < len(self.action_queue):
            return self.action_queue[self.action_index]
        return None

    def assign_teams(self, rep2_team: int):
        """rep2 hangi takımı seçtiyse o takıma gider, rep1 diğerine."""
        if rep2_team == 1:
            self.team1_rep, self.team2_rep = self.rep2, self.rep1
        else:
            self.team1_rep, self.team2_rep = self.rep1, self.rep2
        self.team1 = [self.team1_rep]
        self.team2 = [self.team2_rep]
        # action_queue is set after player draft completes

    def build_summary_embed(self) -> discord.Embed:
        def names(members: list[discord.Member]) -> str:
            return ", ".join(m.display_name for m in members) or "—"

        # Harita durumu
        if self.selected_map:
            map_str = f"✅ **{self.selected_map}**"
        elif self.map_bans:
            banned_str = ", ".join(f"~~{m}~~" for _, m in self.map_bans)
            map_str = f"{len(self.map_bans)}/6 ban  ·  Kalan: {', '.join(self.available_maps)}\n{banned_str}"
        else:
            map_str = "Başlamadı"

        t1_bans  = [f"~~{l}~~" for t, l in self.banned_leaders if t == 1]
        t2_bans  = [f"~~{l}~~" for t, l in self.banned_leaders if t == 2]
        t1_picks = [f"**{l}**" for t, l in self.picked_leaders  if t == 1]
        t2_picks = [f"**{l}**" for t, l in self.picked_leaders  if t == 2]

        action = self.current_action()
        if action:
            at, team = action
            labels = {"map_ban": "🗺️ Harita Banlıyor", "civ_ban": "🚫 Lider Banlıyor", "civ_pick": "✅ Lider Seçiyor"}
            next_str = f"**Takım {team}** — {self.get_rep(team).display_name}  {labels[at]}"
        else:
            next_str = "✅ Draft tamamlandı!"

        embed = discord.Embed(title="🤝 Civilization VI — Takım Draft", color=discord.Color.blurple())
        embed.add_field(name="🔴 Takım 1", value=names(self.team1), inline=True)
        embed.add_field(name="🔵 Takım 2", value=names(self.team2), inline=True)
        embed.add_field(name="🗺️ Harita", value=map_str, inline=False)
        embed.add_field(
            name="🚫 Banlar",
            value=f"T1: {', '.join(t1_bans) or '—'}\nT2: {', '.join(t2_bans) or '—'}",
            inline=True,
        )
        embed.add_field(
            name="✅ Seçimler",
            value=f"T1: {', '.join(t1_picks) or '—'}\nT2: {', '.join(t2_picks) or '—'}",
            inline=True,
        )
        embed.add_field(name="⏭️ Sıradaki", value=next_str, inline=False)
        return embed

    async def _start_action_queue(self, channel: discord.TextChannel):
        """Kuyruğu başlat — action_index artırmadan ilk eylemi işle."""
        if self.summary_msg:
            try:
                await self.summary_msg.edit(embed=self.build_summary_embed())
            except discord.HTTPException:
                pass
        action = self.current_action()
        if action is None:
            await self._finalize(channel)
        else:
            await self._prompt_action(channel, action)

    async def advance(self, channel: discord.TextChannel):
        if self.prompt_msg:
            try:
                await self.prompt_msg.delete()
            except discord.HTTPException:
                pass
            self.prompt_msg = None

        self.action_index += 1

        if self.summary_msg:
            try:
                await self.summary_msg.edit(embed=self.build_summary_embed())
            except discord.HTTPException:
                pass

        action = self.current_action()
        if action is None:
            await self._finalize(channel)
        else:
            await self._prompt_action(channel, action)

    async def _prompt_action(self, channel: discord.TextChannel, action: tuple[str, int]):
        at, team = action
        rep = self.get_rep(team)
        color = discord.Color.red() if team == 1 else discord.Color.blue()

        if at == "map_ban":
            embed = discord.Embed(
                title=f"🗺️ Takım {team} — Harita Banlıyor",
                description=f"{rep.mention} banlamak istediğin haritayı seç.",
                color=color,
            )
            view = TeamMapBanView(self, team, rep)
        else:
            verb  = "banlamak" if at == "civ_ban" else "seçmek"
            title = "Lider Banlıyor" if at == "civ_ban" else "Lider Seçiyor"
            embed = discord.Embed(
                title=f"Takım {team} — {title}",
                description=f"{rep.mention} {verb} istediğin lideri seç.",
                color=color,
            )
            view = TeamCivActionView(self, at, team, rep)

        self.prompt_msg = await channel.send(embed=embed, view=view)

    async def _finalize(self, channel: discord.TextChannel):
        t1_picks = [l for t, l in self.picked_leaders if t == 1]
        t2_picks = [l for t, l in self.picked_leaders if t == 2]

        match_id = _make_match_id("TEAM")
        embed = discord.Embed(
            title=f"🗺️ {self.selected_map} — Draft Tamamlandı!",
            color=discord.Color.gold(),
        )
        embed.set_footer(text=f"Maç ID: {match_id}")
        embed.add_field(
            name="🔴 Takım 1",
            value="\n".join(f"**{l}**" for l in t1_picks) or "—",
            inline=True,
        )
        embed.add_field(
            name="🔵 Takım 2",
            value="\n".join(f"**{l}**" for l in t2_picks) or "—",
            inline=True,
        )
        mentions = " ".join(m.mention for m in self.all_players)
        await channel.send(content=mentions, embed=embed)
        active_team_games.pop(channel.id, None)


class PlayerDraftView(discord.ui.View):
    """Reps take turns picking players for their teams (snake draft)."""

    def __init__(self, game: TeamGame):
        super().__init__(timeout=None)
        self.game = game
        self._add_button()

    def _add_button(self):
        self.clear_items()
        rep, team = self._current_rep()
        label = f"👤 Oyuncu Seç (Takım {team} — {rep.display_name})"
        btn = discord.ui.Button(label=label, style=discord.ButtonStyle.primary)
        btn.callback = self._pick
        self.add_item(btn)

    def _current_rep(self) -> tuple[discord.Member, int]:
        """Snake draft: T1(1), T2(2), T1(2), T2(2)...
        Pick index derived from team counts (reps start at count=1 each)."""
        t1_count = len(self.game.team1)
        t2_count = len(self.game.team2)
        pick_index = (t1_count - 1) + (t2_count - 1)  # total picks done so far
        if pick_index == 0 or (pick_index >= 1 and (pick_index - 1) // 2 % 2 != 0):
            return self.game.team1_rep, 1
        else:
            return self.game.team2_rep, 2

    def build_embed(self) -> discord.Embed:
        rep, team = self._current_rep()
        picked_ids = {p.id for p in self.game.team1 + self.game.team2}
        remaining = [p for p in self.game.all_players if p.id not in picked_ids]
        embed = discord.Embed(
            title="👥 Oyuncu Seçim Aşaması",
            description=f"Sıra: **Takım {team}** — {rep.mention}",
            color=discord.Color.blurple(),
        )
        embed.add_field(
            name="🔴 Takım 1",
            value="\n".join(m.display_name for m in self.game.team1) or "—",
            inline=True,
        )
        embed.add_field(
            name="🔵 Takım 2",
            value="\n".join(m.display_name for m in self.game.team2) or "—",
            inline=True,
        )
        embed.add_field(
            name="⏳ Kalan",
            value=", ".join(m.display_name for m in remaining) or "—",
            inline=False,
        )
        return embed

    async def _pick(self, interaction: discord.Interaction):
        try:
            rep, team = self._current_rep()
            if interaction.user.id != rep.id:
                await interaction.response.send_message(
                    f"Şu an Takım {team}'nin ({rep.display_name}) sırası!", ephemeral=True
                )
                return

            picked_ids = {p.id for p in self.game.team1 + self.game.team2}
            available = [p for p in self.game.all_players if p.id not in picked_ids]

            if not available:
                await interaction.response.defer()
                return

            select = discord.ui.Select(
                placeholder="Oyuncu seç...",
                options=[
                    discord.SelectOption(label=p.display_name, value=str(p.id))
                    for p in available
                ],
            )

            async def on_select(inter: discord.Interaction):
                try:
                    player_id = int(select.values[0])
                    player = next((p for p in available if p.id == player_id), None)
                    if not player:
                        await inter.response.defer()
                        return

                    if team == 1:
                        self.game.team1.append(player)
                    else:
                        self.game.team2.append(player)

                    picked_ids_new = {p.id for p in self.game.team1 + self.game.team2}
                    remaining_new = [p for p in self.game.all_players if p.id not in picked_ids_new]

                    if remaining_new:
                        self._add_button()
                        await inter.response.edit_message(embed=self.build_embed(), view=self)
                    else:
                        await inter.response.edit_message(
                            embed=self.game.build_summary_embed(), view=None
                        )
                        self.game.action_queue = _build_team_action_queue(self.game.team_size)
                        self.game.action_index = 0
                        await self.game._start_action_queue(inter.channel)
                except (discord.NotFound, discord.InteractionResponded, discord.HTTPException):
                    pass

            select.callback = on_select
            select_view = discord.ui.View(timeout=60)
            select_view.add_item(select)
            await interaction.response.edit_message(embed=self.build_embed(), view=select_view)
        except (discord.NotFound, discord.InteractionResponded, discord.HTTPException):
            pass


class TeamSelectionView(discord.ui.View):
    def __init__(self, game: TeamGame):
        super().__init__(timeout=60)
        self.game = game

    @discord.ui.button(label="🔴 Takım 1", style=discord.ButtonStyle.danger)
    async def team1_btn(self, interaction: discord.Interaction, _btn):
        await self._select(interaction, 1)

    @discord.ui.button(label="🔵 Takım 2", style=discord.ButtonStyle.primary)
    async def team2_btn(self, interaction: discord.Interaction, _btn):
        await self._select(interaction, 2)

    async def _select(self, interaction: discord.Interaction, chosen_team: int):
        if interaction.user.id != self.game.rep2.id:
            await interaction.response.send_message(
                f"Bu seçim {self.game.rep2.display_name}'e ait!", ephemeral=True
            )
            return
        self.game.assign_teams(chosen_team)
        self.stop()
        draft_view = PlayerDraftView(self.game)
        await interaction.response.edit_message(embed=draft_view.build_embed(), view=draft_view)
        self.game.summary_msg = await interaction.original_response()


class TeamMapBanView(discord.ui.View):
    def __init__(self, game: TeamGame, team: int, rep: discord.Member):
        super().__init__(timeout=None)
        self.game = game
        self.team = team
        self.rep = rep

        for map_name in game.available_maps:
            emoji = next((e for n, e in MAPS if n == map_name), "🗺️")
            btn = discord.ui.Button(label=map_name, emoji=emoji, style=discord.ButtonStyle.danger)
            btn.callback = self._make_cb(map_name)
            self.add_item(btn)

    def _make_cb(self, map_name: str):
        async def callback(interaction: discord.Interaction):
            if interaction.user.id != self.rep.id:
                await interaction.response.send_message(
                    f"Şu an Takım {self.team}'in ({self.rep.display_name}) sırası!", ephemeral=True
                )
                return

            self.game.available_maps.remove(map_name)
            self.game.map_bans.append((self.team, map_name))
            if len(self.game.available_maps) == 1:
                self.game.selected_map = self.game.available_maps[0]

            for item in self.children:
                item.disabled = True
            await interaction.response.edit_message(view=self)
            await self.game.advance(interaction.channel)

        return callback


class TeamCivActionView(discord.ui.View):
    def __init__(self, game: TeamGame, action_type: str, team: int, rep: discord.Member):
        super().__init__(timeout=None)
        self.game = game
        self.action_type = action_type
        self.team = team
        self.rep = rep

        label = "🚫 Banla" if action_type == "civ_ban" else "✅ Seç"
        style = discord.ButtonStyle.danger if action_type == "civ_ban" else discord.ButtonStyle.success
        btn = discord.ui.Button(label=label, style=style)
        btn.callback = self._process
        self.add_item(btn)

    async def _process(self, interaction: discord.Interaction):
        if interaction.user.id != self.rep.id:
            await interaction.response.send_message(
                f"Şu an Takım {self.team}'in ({self.rep.display_name}) sırası!", ephemeral=True
            )
            return

        game_ref = self.game
        action_type = self.action_type
        team = self.team
        view_ref = self

        used_leaders = {l for _, l in game_ref.banned_leaders} | {l for _, l in game_ref.picked_leaders}
        available = [(c, l) for c, l in ALL_LEADERS if l not in used_leaders]

        async def on_pick(inter: discord.Interaction, civ: str, leader: str):
            try:
                used_now = {l for _, l in game_ref.banned_leaders} | {l for _, l in game_ref.picked_leaders}
                if leader in used_now:
                    status = "banlandı" if leader in {l for _, l in game_ref.banned_leaders} else "seçildi"
                    await inter.response.edit_message(content=f"**{leader}** zaten {status}!", view=None)
                    return
                if action_type == "civ_ban":
                    game_ref.banned_leaders.append((team, leader))
                else:
                    game_ref.picked_leaders.append((team, leader))
                await inter.response.edit_message(content="\u200b", view=None)
                for item in view_ref.children:
                    item.disabled = True
                await game_ref.advance(inter.channel)
            except (discord.NotFound, discord.InteractionResponded, discord.HTTPException):
                pass

        action_word = "Banlamak" if action_type == "civ_ban" else "Seçmek"
        try:
            await interaction.response.send_message(
                f"{action_word} istediğin lideri seç:",
                view=LeaderSelectView(available, on_pick),
                ephemeral=True,
            )
        except (discord.NotFound, discord.InteractionResponded, discord.HTTPException):
            pass


# ===========================================================================
# /autodraftffa — oyuncu sayısı seç + ban → liderler oyunculara havuz olarak düşer
# ===========================================================================

class AutoDraftFfaSession:
    def __init__(self, player_count: int):
        self.player_count = player_count
        self.banned: set[tuple[str, str]] = set()

    def ban_status(self) -> str:
        banned_text = (
            ", ".join(f"{c} — {l}" for c, l in sorted(self.banned))
            if self.banned
            else "Henüz ban yok"
        )
        return (
            f"🚫 **Ban Aşaması** — {self.player_count} Oyuncu\n"
            f"Toplam lider: **{len(ALL_LEADERS)}**  |  "
            f"Banlanan: **{len(self.banned)}**  |  "
            f"Kalan: **{len(ALL_LEADERS) - len(self.banned)}**\n"
            f"Banlananlar: {banned_text}"
        )

    async def finalize(self, interaction: discord.Interaction):
        remaining = [p for p in ALL_LEADERS if p not in self.banned]
        random.shuffle(remaining)
        n = self.player_count
        per_player = len(remaining) // n
        remaining = remaining[:n * per_player]  # discard remainder so all players get equal pools
        pools = [remaining[i * per_player : (i + 1) * per_player] for i in range(n)]

        guild = interaction.guild
        embeds = []
        for i, pool in enumerate(pools):
            lines = []
            for civ, leader in pool:
                emoji = leader_emoji_str(leader, guild)
                label = f"{emoji} {leader}".strip() if emoji else leader
                lines.append(f"{label} — {civ}")
            embed = discord.Embed(
                title=f"🎴 Oyuncu {i + 1}  ({len(pool)} lider)",
                description="\n".join(lines) or "—",
                color=PLAYER_COLORS[i % len(PLAYER_COLORS)],
            )
            embeds.append(embed)

        first, rest = embeds[:10], embeds[10:]
        await interaction.response.edit_message(content=None, embeds=first, view=None)
        for chunk in [rest[j : j + 10] for j in range(0, len(rest), 10)]:
            await interaction.followup.send(embeds=chunk)


def _parse_ban_text(text: str, guild: discord.Guild | None = None) -> tuple[list[tuple[str,str]], list[str]]:
    """Metindeki emoji / lider adı / civ adı tokenlarından (civ, leader) listesi çıkarır.
    Döndürür: (bulunanlar, bulunamayanlar)"""
    _rev_leader: dict[str, str] = {v: k for k, v in LEADER_EMOJI_NAMES.items() if v}
    _civ_leaders: dict[str, list[tuple[str,str]]] = {}
    for c, l in ALL_LEADERS:
        _civ_leaders.setdefault(c.lower(), []).append((c, l))

    found: list[tuple[str,str]] = []
    not_found: list[str] = []
    seen: set[tuple[str,str]] = set()

    # 1) Tüm Discord emoji'lerini teker teker bul (aynı satırda boşlukla ayrılmış olsa bile)
    for ename in re.findall(r"<a?:(\w+):\d+>", text):
        if ename in _rev_leader:
            leader = _rev_leader[ename]
            for pair in ALL_LEADERS:
                if pair[1] == leader and pair not in seen:
                    found.append(pair)
                    seen.add(pair)
        else:
            for civ_name, emoji_val in CIV_EMOJIS.items():
                if emoji_val and ename in str(emoji_val):
                    for pair in _civ_leaders.get(civ_name.lower(), []):
                        if pair not in seen:
                            found.append(pair)
                            seen.add(pair)
                    break

    # 2) Emoji'leri metinden çıkar, kalan düz isimleri virgül/satır ile böl
    plain = re.sub(r"<a?:\w+:\d+>", "", text)
    for token in [t.strip() for t in re.split(r"[,\n]+", plain) if t.strip()]:
        tl = token.lower()
        matched = False
        for c, l in ALL_LEADERS:
            if l.lower() == tl and (c, l) not in seen:
                found.append((c, l))
                seen.add((c, l))
                matched = True
                break
        if not matched:
            for pair in _civ_leaders.get(tl, []):
                if pair not in seen:
                    found.append(pair)
                    seen.add(pair)
                    matched = True
        if not matched:
            not_found.append(token[:30])

    return found, not_found


class _MultiBanSelectView(discord.ui.View):
    """Sayfaları çoklu seçimli ban dropdown'ı."""

    def __init__(self, available: list[tuple[str,str]], session, ban_view_ref, page: int = 0):
        super().__init__(timeout=300)
        self.available = available
        self.session = session
        self.ban_view_ref = ban_view_ref
        self.page = page
        self._rebuild()

    def _rebuild(self):
        self.clear_items()
        pages = [self.available[i:i+25] for i in range(0, len(self.available), 25)]
        if not pages:
            return
        page_leaders = pages[self.page]
        sel = discord.ui.Select(
            placeholder=f"Banlanacakları seç — Sayfa {self.page+1}/{len(pages)}",
            min_values=1,
            max_values=len(page_leaders),
            options=[
                discord.SelectOption(label=l, value=f"{c}||{l}", description=c)
                for c, l in page_leaders
            ],
        )
        sel.callback = self._on_select
        self.add_item(sel)
        if self.page > 0:
            b = discord.ui.Button(label="◀", style=discord.ButtonStyle.secondary)
            b.callback = self._prev
            self.add_item(b)
        if self.page < len(pages) - 1:
            b = discord.ui.Button(label="▶", style=discord.ButtonStyle.secondary)
            b.callback = self._next
            self.add_item(b)

    async def _on_select(self, interaction: discord.Interaction):
        newly = []
        for val in interaction.data["values"]:
            civ, leader = val.split("||", 1)
            pair = (civ, leader)
            if pair not in self.session.banned:
                self.session.banned.add(pair)
                newly.append(leader)
        # Seçilenleri available'dan çıkar, view'i yenile
        self.available = [(c, l) for c, l in ALL_LEADERS if (c, l) not in self.session.banned]
        total_pages = max(1, (len(self.available) + 24) // 25)
        self.page = min(self.page, total_pages - 1)
        self._rebuild()
        msg = f"✅ **{len(newly)}** lider banlandı: {', '.join(newly[:8])}{'...' if len(newly)>8 else ''}\nBaşka seçmek istersen devam edebilirsin."
        await interaction.response.edit_message(content=msg, view=self)
        if self.ban_view_ref.message:
            await self.ban_view_ref.message.edit(embed=self.ban_view_ref.build_embed())

    async def _prev(self, interaction: discord.Interaction):
        self.page -= 1
        self._rebuild()
        await interaction.response.edit_message(view=self)

    async def _next(self, interaction: discord.Interaction):
        self.page += 1
        self._rebuild()
        await interaction.response.edit_message(view=self)


class _BulkBanModal(discord.ui.Modal, title="Toplu Ban"):
    ban_input = discord.ui.TextInput(
        label="Lider adı veya emojisi (virgülle ayır)",
        placeholder="Hammurabi, Saladin, Alexander ...",
        style=discord.TextStyle.paragraph,
        max_length=1000,
        required=True,
    )

    def __init__(self, session, ban_view_ref):
        super().__init__()
        self._session = session
        self._view_ref = ban_view_ref

    async def on_submit(self, interaction: discord.Interaction):
        pairs, not_found = _parse_ban_text(self.ban_input.value, interaction.guild)
        newly = [p for p in pairs if p not in self._session.banned]
        for p in newly:
            self._session.banned.add(p)

        parts = []
        if newly:
            parts.append(f"✅ **{len(newly)}** lider banlandı: " +
                         ", ".join(l for _, l in newly[:10]) +
                         ("..." if len(newly) > 10 else ""))
        if not_found:
            parts.append(f"❌ Tanımlanamadı: {', '.join(not_found[:5])}")
        if not newly and not not_found:
            parts.append("⚠️ Hiçbir yeni lider bulunamadı.")

        await interaction.response.send_message("\n".join(parts) or "—", ephemeral=True)
        if self._view_ref.message:
            await self._view_ref.message.edit(embed=self._view_ref.build_embed())


class AutoBanView(discord.ui.View):
    """Ban phase for autodraft sessions: paginated leader list, no civ-first flow."""

    def __init__(self, session):
        super().__init__(timeout=None)
        self.session = session
        self.message: discord.Message | None = None

    def build_embed(self) -> discord.Embed:
        banned_text = (
            "\n".join(f"~~{l}~~" for c, l in sorted(self.session.banned))
            if self.session.banned else "Henüz ban yok"
        )
        embed = discord.Embed(
            title="🚫 Ban Aşaması",
            description=(
                f"Toplam: **{len(ALL_LEADERS)}**  |  "
                f"Banlanan: **{len(self.session.banned)}**  |  "
                f"Kalan: **{len(ALL_LEADERS) - len(self.session.banned)}**"
            ),
            color=discord.Color.orange(),
        )
        embed.add_field(name="Banlananlar", value=banned_text or "—", inline=False)
        return embed

    @discord.ui.button(label="🚫 Ban Ekle", style=discord.ButtonStyle.danger)
    async def ban_btn(self, interaction: discord.Interaction, _btn):
        session = self.session
        view_ref = self
        available = [(c, l) for c, l in ALL_LEADERS if (c, l) not in session.banned]

        async def on_pick(inter: discord.Interaction, civ: str, leader: str):
            if (civ, leader) in session.banned:
                await inter.response.edit_message(content=f"**{leader}** zaten banlandı!", view=None)
                return
            session.banned.add((civ, leader))
            await inter.response.edit_message(content="\u200b", view=None)
            if view_ref.message:
                await view_ref.message.edit(embed=view_ref.build_embed())

        await interaction.response.send_message(
            "Lideri seç:",
            view=LeaderSelectView(available, on_pick),
            ephemeral=True,
        )

    @discord.ui.button(label="📋 Listeden Toplu Ban", style=discord.ButtonStyle.secondary)
    async def multi_ban_btn(self, interaction: discord.Interaction, _btn):
        available = [(c, l) for c, l in ALL_LEADERS if (c, l) not in self.session.banned]
        await interaction.response.send_message(
            "Liderleri seç:",
            view=_MultiBanSelectView(available, self.session, self),
            ephemeral=True,
        )

    @discord.ui.button(label="✏️ Emoji ile Ban", style=discord.ButtonStyle.secondary)
    async def emoji_ban_btn(self, interaction: discord.Interaction, _btn):
        await interaction.response.send_message(
            "Banlanacak liderleri yaz (her satıra bir tane):",
            ephemeral=True,
        )
        try:
            msg = await bot.wait_for("message", check=_msg_check(interaction), timeout=120)
        except asyncio.TimeoutError:
            await interaction.followup.send("⏰ Süre doldu.", ephemeral=True)
            return
        pairs, not_found = _parse_ban_text(msg.content, interaction.guild)
        newly = [p for p in pairs if p not in self.session.banned]
        for p in newly:
            self.session.banned.add(p)
        parts = []
        if newly:
            parts.append(
                f"✅ **{len(newly)}** lider banlandı: "
                + ", ".join(l for _, l in newly[:10])
                + ("..." if len(newly) > 10 else "")
            )
        if not_found:
            parts.append(f"❌ Tanımlanamadı: {', '.join(not_found[:5])}")
        if not newly and not not_found:
            parts.append("⚠️ Hiçbir yeni lider bulunamadı.")
        await interaction.followup.send("\n".join(parts) or "—", ephemeral=True)
        if self.message:
            await self.message.edit(embed=self.build_embed())

    @discord.ui.button(label="✅ Draftı Başlat", style=discord.ButtonStyle.success)
    async def start_btn(self, interaction: discord.Interaction, _btn):
        self.stop()
        try:
            await self.session.finalize(interaction)
        except Exception as exc:
            logging.error("AutoBanView.start_btn finalize error: %s", exc, exc_info=True)
            await _safe_send(interaction, "❌ Draft başlatılamadı, tekrar dene.")


class AutoDraftFfaCountView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=60)
        sel = discord.ui.Select(
            placeholder="Kaç oyuncu?",
            options=[
                discord.SelectOption(label=f"{n} Oyuncu", value=str(n))
                for n in range(2, 13)
            ],
        )
        sel.callback = self._on_select
        self.add_item(sel)

    async def _on_select(self, interaction: discord.Interaction):
        self.stop()
        n = int(interaction.data["values"][0])
        session = AutoDraftFfaSession(n)
        ban_view = AutoBanView(session)
        await interaction.response.edit_message(embed=ban_view.build_embed(), view=ban_view)
        ban_view.message = await interaction.original_response()


# ===========================================================================
# /autodraftteam — takım sayısı seç + ban → liderler takımlara otomatik düşer
# ===========================================================================

class AutoDraftSession:
    """TeamBanPhaseView ile uyumlu duck-type session: sadece ban + dağıtım."""

    def __init__(self, team_count: int):
        self.team_count = team_count
        self.banned: set[tuple[str, str]] = set()

    def ban_status(self) -> str:
        banned_text = (
            ", ".join(f"{c} — {l}" for c, l in sorted(self.banned))
            if self.banned
            else "Henüz ban yok"
        )
        return (
            f"🚫 **Ban Aşaması** — {self.team_count} Takım\n"
            f"Toplam lider: **{len(ALL_LEADERS)}**  |  "
            f"Banlanan: **{len(self.banned)}**  |  "
            f"Kalan: **{len(ALL_LEADERS) - len(self.banned)}**\n"
            f"Banlananlar: {banned_text}"
        )

    async def finalize(self, interaction: discord.Interaction):
        remaining = [p for p in ALL_LEADERS if p not in self.banned]
        random.shuffle(remaining)
        n = self.team_count
        per_team = len(remaining) // n
        remaining = remaining[:n * per_team]  # discard remainder so all teams get equal pools
        teams = [remaining[i * per_team : (i + 1) * per_team] for i in range(n)]

        guild = interaction.guild
        embeds = []
        for i, leaders in enumerate(teams):
            lines = []
            for civ, leader in sorted(leaders, key=lambda x: x[1]):
                emoji = leader_emoji_str(leader, guild)
                label = f"{emoji} {leader}".strip() if emoji else leader
                lines.append(f"{label} — {civ}")
            embeds.append(discord.Embed(
                title=f"{TEAM_EMOJIS[i % len(TEAM_EMOJIS)]} Takım {i + 1}  ({len(leaders)} lider)",
                description="\n".join(lines) or "—",
                color=TEAM_COLORS[i % len(TEAM_COLORS)],
            ))

        first, rest = embeds[:10], embeds[10:]
        await interaction.response.edit_message(content=None, embeds=first, view=None)
        for chunk in [rest[j : j + 10] for j in range(0, len(rest), 10)]:
            await interaction.followup.send(embeds=chunk)


class AutoDraftCountView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=60)
        for n in range(2, 7):
            btn = discord.ui.Button(
                label=f"{TEAM_EMOJIS[n - 2]} {n} Takım",
                style=discord.ButtonStyle.primary,
            )
            btn.callback = self._make_cb(n)
            self.add_item(btn)

    def _make_cb(self, n: int):
        async def cb(interaction: discord.Interaction):
            self.stop()
            session = AutoDraftSession(n)
            ban_view = AutoBanView(session)
            await interaction.response.edit_message(embed=ban_view.build_embed(), view=ban_view)
            ban_view.message = await interaction.original_response()
        return cb


# ===========================================================================
# Slash Commands
# ===========================================================================

@bot.tree.command(name="team", description="Ses kanalındaki oyuncularla iki takım oluşturup harita ve lider draftı yap")
@app_commands.describe(opponent="Rakip takımın temsilcisini etiketle")
async def team_command(interaction: discord.Interaction, opponent: discord.Member):
    if interaction.channel_id in active_team_games:
        await interaction.response.send_message("Bu kanalda zaten aktif bir takım oyunu var!", ephemeral=True)
        return

    if opponent.bot or opponent.id == interaction.user.id:
        await interaction.response.send_message("Geçerli bir oyuncu etiketle!", ephemeral=True)
        return

    players = get_voice_members(interaction)
    if not players:
        await interaction.response.send_message("❌ Bir ses kanalında olman gerekiyor!", ephemeral=True)
        return

    if len(players) % 2 != 0:
        await interaction.response.send_message(
            f"❌ Oyuncu sayısı çift olmalı! Şu an **{len(players)}** kişi var.", ephemeral=True
        )
        return

    caller = interaction.guild.get_member(interaction.user.id)
    if caller not in players or opponent not in players:
        await interaction.response.send_message(
            "Her iki oyuncu da aynı ses kanalında olmalı!", ephemeral=True
        )
        return

    game = TeamGame(players, caller, opponent)
    active_team_games[interaction.channel_id] = game

    embed = discord.Embed(
        title="🤝 Takım Seçimi",
        description=(
            f"{caller.mention} bir oyun kurdu.\n"
            f"{opponent.mention} hangi takımda olmak istiyorsun?"
        ),
        color=discord.Color.blurple(),
    )
    await interaction.response.send_message(embed=embed, view=TeamSelectionView(game))


@bot.tree.command(name="ffa", description="Ses kanalındaki oyuncularla harita oyu yap ve herkese lider havuzu dağıt")
async def ffa_command(interaction: discord.Interaction):
    if interaction.channel_id in active_ffa_games:
        await interaction.response.send_message(
            "Bu kanalda zaten aktif bir FFA oyunu var!", ephemeral=True
        )
        return

    players = get_voice_members(interaction)
    if not players:
        await interaction.response.send_message(
            "❌ Bir ses kanalında olman gerekiyor!", ephemeral=True
        )
        return

    game = FFAGame(players)
    active_ffa_games[interaction.channel_id] = game

    view = MapSelectionView(game)
    embed = view.build_embed()
    embed.description = "Oynamak istediğin haritaya oy ver!\n\n" + (embed.description or "")

    await interaction.response.send_message(
        content=" ".join(p.mention for p in players),
        embed=embed,
        view=view,
    )


# ---------------------------------------------------------------------------
# /id — maç sonucu kayıt
# ---------------------------------------------------------------------------

def _parse_mention_id(text: str) -> str | None:
    m = re.search(r"<@!?(\d+)>", text)
    return m.group(1) if m else None


def _parse_line(guild: discord.Guild, line: str) -> tuple[str | None, str, str | None]:
    """Parse a result line into (player_id, display_name, civ_name).
    Expected format: '@Mention <civ_emoji>'  or  '@Mention CivName'
    Tries emoji_to_civ() on the remainder first; falls back to treating it as a civ name.
    player_id is None if no Discord mention found.
    civ_name is None if nothing follows the mention.
    """
    pid = _parse_mention_id(line)
    if pid:
        member = guild.get_member(int(pid))
        name = str(member) if member else f"<@{pid}>"
        remainder = re.sub(r"<@!?\d+>", "", line).strip() or None
        if remainder:
            civ = emoji_to_civ(remainder) or remainder
        else:
            civ = None
    else:
        parts = line.split(None, 1)
        name  = parts[0] if parts else line
        civ   = parts[1].strip() if len(parts) > 1 else None
        pid   = None
    return pid, name, civ



class FfaResultModal(discord.ui.Modal, title="FFA Maç Sonucu"):
    results = discord.ui.TextInput(
        label="Sıralama — her satıra bir oyuncu + lider",
        placeholder="@Oyuncu1 America\n@Oyuncu2 Greece\n@Oyuncu3 Japan",
        style=discord.TextStyle.paragraph,
        max_length=1500,
    )

    async def on_submit(self, interaction: discord.Interaction):
        match_id = _make_match_id("FFA")
        lines = [l.strip() for l in self.results.value.strip().splitlines() if l.strip()]

        # Build ordered player list for ELO calculation + civ tracking
        ordered: list[tuple[str, str]] = []
        for line in lines:
            pid, name, civ = _parse_line(interaction.guild, line)
            if pid:
                ordered.append((pid, name))
                if civ:
                    db.record_civ_play(pid, name, civ, "ffa")

        elo_results = db.record_ffa(ordered) if ordered else []
        elo_by_id   = {r.player_id: r for r in elo_results}

        ranking_parts = []
        for i, line in enumerate(lines):
            pid, _, _ = _parse_line(interaction.guild, line)
            suffix = ""
            if pid and pid in elo_by_id:
                r     = elo_by_id[pid]
                sign  = "+" if r.delta >= 0 else ""
                suffix = f"  `{sign}{r.delta} puan`"
            ranking_parts.append(f"**{i + 1}.** {line}{suffix}")

        embed = discord.Embed(
            title="⚔️ FFA Maç Sonucu",
            description="\n".join(ranking_parts) or "—",
            color=discord.Color.gold(),
        )
        embed.set_footer(text=f"Maç ID: {match_id}")
        await interaction.response.send_message(embed=embed)


class TeamerResultModal(discord.ui.Modal, title="Teamer Maç Sonucu"):
    winners = discord.ui.TextInput(
        label="Kazanan Takım",
        placeholder="@Oyuncu1 America\n@Oyuncu2 Greece",
        style=discord.TextStyle.paragraph,
        max_length=700,
    )
    losers = discord.ui.TextInput(
        label="Kaybeden Takım",
        placeholder="@Oyuncu3 Japan\n@Oyuncu4 China",
        style=discord.TextStyle.paragraph,
        max_length=700,
    )

    async def on_submit(self, interaction: discord.Interaction):
        match_id = _make_match_id("TEAM")
        w_lines = [l.strip() for l in self.winners.value.strip().splitlines() if l.strip()]
        l_lines = [l.strip() for l in self.losers.value.strip().splitlines() if l.strip()]

        w_players = []
        for line in w_lines:
            pid, name, civ = _parse_line(interaction.guild, line)
            if pid:
                w_players.append((pid, name))
                if civ:
                    db.record_civ_play(pid, name, civ, "team")
        l_players = []
        for line in l_lines:
            pid, name, civ = _parse_line(interaction.guild, line)
            if pid:
                l_players.append((pid, name))
                if civ:
                    db.record_civ_play(pid, name, civ, "team")

        w_results, l_results = (
            db.record_team(w_players, l_players)
            if w_players and l_players
            else ([], [])
        )
        elo_by_id = {r.player_id: r for r in w_results + l_results}

        def fmt_lines(raw_lines: list[str]) -> str:
            out = []
            for line in raw_lines:
                pid, _, _ = _parse_line(interaction.guild, line)
                suffix = ""
                if pid and pid in elo_by_id:
                    r    = elo_by_id[pid]
                    sign = "+" if r.delta >= 0 else ""
                    suffix = f"  `{r.old_rating} → {r.new_rating} ({sign}{r.delta})`"
                out.append(f"{line}{suffix}")
            return "\n".join(out) or "—"

        embed = discord.Embed(title="🤝 Teamer Maç Sonucu", color=discord.Color.green())
        embed.add_field(name="🏆 Kazanan Takım", value=fmt_lines(w_lines), inline=False)
        embed.add_field(name="💀 Kaybeden Takım", value=fmt_lines(l_lines), inline=False)
        embed.set_footer(text=f"Maç ID: {match_id}")
        await interaction.response.send_message(embed=embed)


# ---------------------------------------------------------------------------
# /report modals — match ID provided by user, civ emoji supported
# ---------------------------------------------------------------------------

class FfaReportModal(discord.ui.Modal, title="FFA Maç Sonucu"):
    results = discord.ui.TextInput(
        label="Sıralama (1.→son) — @oyuncu + lider emojisi",
        placeholder="@Oyuncu1 <:america:123>\n@Oyuncu2 <:greece:456>\n@Oyuncu3 <:japan:789>",
        style=discord.TextStyle.paragraph,
        max_length=1500,
    )

    def __init__(self, match_id: str):
        super().__init__()
        self.match_id = match_id

    async def on_submit(self, interaction: discord.Interaction):
        lines = [l.strip() for l in self.results.value.strip().splitlines() if l.strip()]

        ordered: list[tuple[str, str]] = []
        for line in lines:
            pid, name, civ = _parse_line(interaction.guild, line)
            if pid:
                ordered.append((pid, name))
                if civ:
                    db.record_civ_play(pid, name, civ, "ffa")

        elo_results = db.record_ffa(ordered) if ordered else []
        elo_by_id   = {r.player_id: r for r in elo_results}

        ranking_parts = []
        for i, line in enumerate(lines):
            pid, _, _ = _parse_line(interaction.guild, line)
            suffix = ""
            if pid and pid in elo_by_id:
                r    = elo_by_id[pid]
                sign = "+" if r.delta >= 0 else ""
                suffix = f"  `{sign}{r.delta} puan`"
            ranking_parts.append(f"**{i + 1}.** {line}{suffix}")

        embed = discord.Embed(
            title="⚔️ FFA Maç Sonucu",
            description="\n".join(ranking_parts) or "—",
            color=discord.Color.gold(),
        )
        embed.set_footer(text=f"Maç ID: {self.match_id}")
        await interaction.response.send_message(embed=embed)


class TeamerReportModal(discord.ui.Modal, title="Teamer Maç Sonucu"):
    winners = discord.ui.TextInput(
        label="Kazanan Takım — @oyuncu + lider emojisi",
        placeholder="@Oyuncu1 <:america:123>\n@Oyuncu2 <:greece:456>",
        style=discord.TextStyle.paragraph,
        max_length=700,
    )
    losers = discord.ui.TextInput(
        label="Kaybeden Takım — @oyuncu + lider emojisi",
        placeholder="@Oyuncu3 <:japan:789>\n@Oyuncu4 <:china:012>",
        style=discord.TextStyle.paragraph,
        max_length=700,
    )

    def __init__(self, match_id: str):
        super().__init__()
        self.match_id = match_id

    async def on_submit(self, interaction: discord.Interaction):
        w_lines = [l.strip() for l in self.winners.value.strip().splitlines() if l.strip()]
        l_lines = [l.strip() for l in self.losers.value.strip().splitlines() if l.strip()]

        w_players, l_players = [], []
        for line in w_lines:
            pid, name, civ = _parse_line(interaction.guild, line)
            if pid:
                w_players.append((pid, name))
                if civ:
                    db.record_civ_play(pid, name, civ, "team")
        for line in l_lines:
            pid, name, civ = _parse_line(interaction.guild, line)
            if pid:
                l_players.append((pid, name))
                if civ:
                    db.record_civ_play(pid, name, civ, "team")

        w_results, l_results = (
            db.record_team(w_players, l_players)
            if w_players and l_players
            else ([], [])
        )
        elo_by_id = {r.player_id: r for r in w_results + l_results}

        def fmt_lines(raw_lines: list[str]) -> str:
            out = []
            for line in raw_lines:
                pid, _, _ = _parse_line(interaction.guild, line)
                suffix = ""
                if pid and pid in elo_by_id:
                    r    = elo_by_id[pid]
                    sign = "+" if r.delta >= 0 else ""
                    suffix = f"  `{r.old_rating} → {r.new_rating} ({sign}{r.delta})`"
                out.append(f"{line}{suffix}")
            return "\n".join(out) or "—"

        embed = discord.Embed(title="🤝 Teamer Maç Sonucu", color=discord.Color.green())
        embed.add_field(name="🏆 Kazanan Takım", value=fmt_lines(w_lines), inline=False)
        embed.add_field(name="💀 Kaybeden Takım", value=fmt_lines(l_lines), inline=False)
        embed.set_footer(text=f"Maç ID: {self.match_id}")
        await interaction.response.send_message(embed=embed)


class ResultEntryView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=60)

    @discord.ui.button(label="⚔️ FFA Sonucu Gir", style=discord.ButtonStyle.primary)
    async def ffa_btn(self, interaction: discord.Interaction, _btn):
        await interaction.response.send_modal(FfaResultModal())

    @discord.ui.button(label="🤝 Teamer Sonucu Gir", style=discord.ButtonStyle.success)
    async def team_btn(self, interaction: discord.Interaction, _btn):
        await interaction.response.send_modal(TeamerResultModal())


@bot.tree.command(name="id", description="Kendi puanını ve oynadığın liderlerin istatistiklerini görüntüle")
async def id_command(interaction: discord.Interaction):
    uid  = str(interaction.user.id)
    name = interaction.user.display_name

    ffa       = db.ffa_player(uid)
    team      = db.team_player(uid)
    ffa_civs  = db.player_most_played(uid, "ffa",  limit=5)
    team_civs = db.player_most_played(uid, "team", limit=5)

    def civ_lines(rows) -> str:
        return "\n".join(f"`{r['civ']}` — {r['plays']}x" for r in rows) or "—"

    embed = discord.Embed(
        title=f"📊 {name} — İstatistikler",
        color=discord.Color.blurple(),
    )

    if ffa:
        win_pct = round(100 * ffa["wins"] / ffa["games"], 1) if ffa["games"] else 0
        embed.add_field(
            name="⚔️ FFA",
            value=(
                f"Puan: **{ffa['rating']}**\n"
                f"Maç: {ffa['games']}  ·  1. sıra: {ffa['wins']}  ·  %{win_pct}\n"
                f"En çok oynadıkları:\n{civ_lines(ffa_civs)}"
            ),
            inline=True,
        )
    else:
        embed.add_field(name="⚔️ FFA", value=f"Henüz kayıt yok.\nBaşlangıç puanı: **{db.FFA_START}**", inline=True)

    if team:
        win_pct = round(100 * team["wins"] / team["games"], 1) if team["games"] else 0
        embed.add_field(
            name="🤝 Teamer",
            value=(
                f"Puan: **{team['rating']}**\n"
                f"Galibiyet/Mağlubiyet: {team['wins']}/{team['losses']}  ·  %{win_pct}\n"
                f"En çok oynadıkları:\n{civ_lines(team_civs)}"
            ),
            inline=True,
        )
    else:
        embed.add_field(name="🤝 Teamer", value=f"Henüz kayıt yok.\nBaşlangıç puanı: **{db.TEAM_START}**", inline=True)

    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="reportwithid", description="Maç ID'si ile sonucu kaydet")
@app_commands.describe(match_id="Maç ID'si (örnek: FFA-A3K7X2B9C1D4E5)")
async def reportwithid_command(interaction: discord.Interaction, match_id: str):
    mid = match_id.upper().strip()
    if mid.startswith("FFA-"):
        await interaction.response.send_modal(FfaReportModal(mid))
    elif mid.startswith("TEAM-"):
        await interaction.response.send_modal(TeamerReportModal(mid))
    else:
        await interaction.response.send_message(
            "❌ Geçersiz maç ID'si! `FFA-XXXXXX` veya `TEAM-XXXXXX` formatında olmalı.",
            ephemeral=True,
        )


@bot.tree.command(name="quickffa", description="Ban yap, liderleri oyunculara otomatik dağıt")
async def quickffa_command(interaction: discord.Interaction):
    await interaction.response.send_message("Kaç oyuncu?", view=AutoDraftFfaCountView())


@bot.tree.command(name="quickteam", description="Ban yap, liderleri takımlara otomatik dağıt")
async def quickteam_command(interaction: discord.Interaction):
    await interaction.response.send_message("Kaç takım?", view=AutoDraftCountView())


_LB_PER_PAGE = 10


class LeaderboardView(discord.ui.View):
    def __init__(self, mode: str = "ffa"):
        super().__init__(timeout=180)
        self.mode = mode
        self.page = 0
        self._data: list = []
        self._refresh_data()
        self._rebuild()

    def _refresh_data(self):
        self._data = db.ffa_leaderboard() if self.mode == "ffa" else db.team_leaderboard()

    def _total_pages(self) -> int:
        return max(1, (len(self._data) + _LB_PER_PAGE - 1) // _LB_PER_PAGE)

    def build_embed(self) -> discord.Embed:
        start = self.page * _LB_PER_PAGE
        rows  = self._data[start : start + _LB_PER_PAGE]
        medals = ["🥇", "🥈", "🥉"]

        lines = []
        for i, row in enumerate(rows):
            rank   = start + i + 1
            prefix = medals[rank - 1] if rank <= 3 else f"**{rank}.**"
            if self.mode == "ffa":
                lines.append(
                    f"{prefix} {row['player_tag']} — "
                    f"Puan **{row['rating']}** · {row['games']} maç · "
                    f"%{row['win_pct'] or 0} 1.sıra"
                )
            else:
                lines.append(
                    f"{prefix} {row['player_tag']} — "
                    f"Puan **{row['rating']}** · "
                    f"{row['wins']}G/{row['losses']}M · %{row['win_pct'] or 0}"
                )

        title = "⚔️ FFA Liderlik Tablosu" if self.mode == "ffa" else "🤝 Teamer Liderlik Tablosu"
        color = discord.Color.gold() if self.mode == "ffa" else discord.Color.green()
        embed = discord.Embed(
            title=title,
            description="\n".join(lines) if lines else "Henüz kayıt yok.",
            color=color,
        )
        embed.set_footer(text=f"Sayfa {self.page + 1}/{self._total_pages()}  ·  {len(self._data)} oyuncu")
        return embed

    def _rebuild(self):
        self.clear_items()
        total = self._total_pages()

        ffa_btn = discord.ui.Button(
            label="⚔️ FFA",
            style=discord.ButtonStyle.primary if self.mode == "ffa" else discord.ButtonStyle.secondary,
        )
        ffa_btn.callback = self._set_mode("ffa")
        self.add_item(ffa_btn)

        team_btn = discord.ui.Button(
            label="🤝 Teamer",
            style=discord.ButtonStyle.success if self.mode == "team" else discord.ButtonStyle.secondary,
        )
        team_btn.callback = self._set_mode("team")
        self.add_item(team_btn)

        if total > 1:
            prev = discord.ui.Button(
                label="◀", style=discord.ButtonStyle.secondary, disabled=self.page == 0
            )
            prev.callback = self._go_page(-1)
            self.add_item(prev)

            nxt = discord.ui.Button(
                label="▶", style=discord.ButtonStyle.secondary, disabled=self.page >= total - 1
            )
            nxt.callback = self._go_page(1)
            self.add_item(nxt)

    def _set_mode(self, mode: str):
        async def cb(interaction: discord.Interaction):
            self.mode = mode
            self.page = 0
            self._refresh_data()
            self._rebuild()
            await interaction.response.edit_message(embed=self.build_embed(), view=self)
        return cb

    def _go_page(self, delta: int):
        async def cb(interaction: discord.Interaction):
            self.page += delta
            self._rebuild()
            await interaction.response.edit_message(embed=self.build_embed(), view=self)
        return cb


@bot.tree.command(name="leaderboard", description="Sunucudaki oyuncuların puan sıralamasını FFA veya Teamer modunda göster")
async def leaderboard_command(interaction: discord.Interaction):
    view  = LeaderboardView("ffa")
    await interaction.response.send_message(embed=view.build_embed(), view=view)


class MostPlayedTypeView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=60)

    @discord.ui.button(label="⚔️ FFA", style=discord.ButtonStyle.primary)
    async def ffa_btn(self, interaction: discord.Interaction, _btn):
        await self._show(interaction, "ffa")

    @discord.ui.button(label="🤝 Teamer", style=discord.ButtonStyle.success)
    async def team_btn(self, interaction: discord.Interaction, _btn):
        await self._show(interaction, "team")

    async def _show(self, interaction: discord.Interaction, game_type: str):
        rows = db.most_played_civs(game_type)
        if not rows:
            await interaction.response.edit_message(
                content="Henüz kayıt yok.", view=None
            )
            return

        medals = ["🥇", "🥈", "🥉"]
        lines = []
        for i, row in enumerate(rows):
            prefix = medals[i] if i < 3 else f"**{i + 1}.**"
            lines.append(
                f"{prefix} {row['civ']} — "
                f"**{row['plays']}** kez  ·  {row['unique_players']} farklı oyuncu"
            )

        title = "⚔️ FFA" if game_type == "ffa" else "🤝 Teamer"
        color = discord.Color.gold() if game_type == "ffa" else discord.Color.green()
        embed = discord.Embed(
            title=f"{title} — En Çok Oynanan Liderler",
            description="\n".join(lines),
            color=color,
        )
        await interaction.response.edit_message(embed=embed, view=None)


@bot.tree.command(name="mostplayed", description="Sunucuda en çok oynanan liderleri FFA veya Teamer modunda listele")
async def mostplayed_command(interaction: discord.Interaction):
    await interaction.response.send_message("Hangi mod?", view=MostPlayedTypeView())


@bot.tree.command(name="coinflip", description="Yazı mı tura mı? Madeni parayı havaya at!")
async def coinflip_command(interaction: discord.Interaction):
    result = random.choice(["Yazı", "Tura"])
    await interaction.response.send_message(f"🪙 **{result}!**")


# ---------------------------------------------------------------------------
# /createreportid — Chat mesajıyla tagleme, dropdown ile lider
# ---------------------------------------------------------------------------

async def _wizard_edit(
    interaction: discord.Interaction,
    content: str,
    view: discord.ui.View | None,
    embed: discord.Embed | None = None,
):
    kwargs: dict = {"content": content, "view": view}
    if embed is not None:
        kwargs["embed"] = embed
    try:
        if interaction.response.is_done():
            await interaction.edit_original_response(**kwargs)
        else:
            await interaction.response.edit_message(**kwargs)
    except (discord.NotFound, discord.InteractionResponded, discord.HTTPException):
        pass


def _parse_mentions(text: str, guild: discord.Guild) -> list[discord.Member]:
    return [
        m for pid in re.findall(r"<@!?(\d+)>", text)
        if (m := guild.get_member(int(pid)))
    ]


def _ordered_mentions(msg: discord.Message) -> list[discord.Member]:
    """msg.content'teki sıraya göre Member listesi döndürür.
    msg.mentions resolved verisi kullanılır (cache bağımsız),
    sıra ise content'teki <@id> regex sırasından alınır."""
    member_map = {str(m.id): m for m in msg.mentions if isinstance(m, discord.Member)}
    seen: set[str] = set()
    result: list[discord.Member] = []
    for pid in re.findall(r"<@!?(\d+)>", msg.content):
        if pid not in seen and pid in member_map:
            result.append(member_map[pid])
            seen.add(pid)
    return result


# ---- FFA Wizard ----

class FfaReportWizard:
    def __init__(self, members: list[discord.Member]):
        self.members = members
        self.player_count = len(members)
        self.match_id = _make_match_id("FFA")
        self.civs: list[str] = []
        self.victory: str | None = None

    def _progress_header(self) -> str:
        lines = []
        for i, m in enumerate(self.members):
            civ_txt = f" — {self.civs[i]}" if i < len(self.civs) else " — ..."
            lines.append(f"**{i+1}.** {m.display_name}{civ_txt}")
        return "\n".join(lines)

    def _civ_content(self, idx: int) -> str:
        member = self.members[idx]
        return (
            f"**⚔️ FFA — {idx+1}/{self.player_count}**\n"
            f"{member.display_name} ({idx+1}. yer):\n\n"
            + self._progress_header()
        )

    def _make_civ_view(self, idx: int) -> "LeaderSelectView":
        wizard = self
        used = set(self.civs)
        available = [(c, l) for c, l in ALL_LEADERS if l not in used]

        async def on_pick(inter: discord.Interaction, _civ: str, leader: str):
            wizard.civs.append(leader)
            nxt = len(wizard.civs)
            if nxt >= wizard.player_count:
                await _wizard_edit(inter, "Zafer türü:", _VictoryView(wizard))
            else:
                await _wizard_edit(inter, wizard._civ_content(nxt), wizard._make_civ_view(nxt))

        return LeaderSelectView(available, on_pick)

    async def send_first(self, followup: discord.Webhook):
        await followup.send(content=self._civ_content(0), view=self._make_civ_view(0))

    async def finalize(self, interaction: discord.Interaction, turn: str | None = None):
        ordered = [(str(m.id), str(m)) for m in self.members]
        for member, leader in zip(self.members, self.civs):
            db.record_civ_play(str(member.id), str(member), leader, "ffa")

        elo_results = db.record_ffa(ordered) if ordered else []
        elo_by_id = {r.player_id: r for r in elo_results}

        lines = []
        for i, (member, leader) in enumerate(zip(self.members, self.civs)):
            suffix = ""
            pid = str(member.id)
            if pid in elo_by_id:
                r = elo_by_id[pid]
                sign = "+" if r.delta >= 0 else ""
                suffix = f"  `{sign}{r.delta} puan`"
            lines.append(f"**{i+1}.** {member.mention} — {leader}{suffix}")

        embed = discord.Embed(
            title="⚔️ FFA Maç Sonucu",
            description="\n".join(lines) or "—",
            color=discord.Color.gold(),
        )
        footer = f"Maç ID: {self.match_id}"
        if self.victory:
            footer += f"  ·  Zafer: {self.victory}"
        if turn:
            footer += f"  ·  Tur: {turn}"
        embed.set_footer(text=footer)
        try:
            await interaction.response.send_message(embed=embed)
        except discord.InteractionResponded:
            await interaction.followup.send(embed=embed)


# ---- Team Wizard ----

class TeamReportWizard:
    def __init__(self, team_members: list[list[discord.Member]]):
        self.team_members = team_members
        self.team_count = len(team_members)
        self.match_id = _make_match_id("TEAM")
        self.civs: list[list[str]] = [[] for _ in team_members]
        self.victory: str | None = None

    def _total_players(self) -> int:
        return sum(len(t) for t in self.team_members)

    def _all_civs_flat(self) -> list[str]:
        return [c for tc in self.civs for c in tc]

    def _progress_header(self) -> str:
        lines = []
        for ti, team in enumerate(self.team_members):
            for pi, member in enumerate(team):
                civ_txt = f" — {self.civs[ti][pi]}" if pi < len(self.civs[ti]) else " — ..."
                lines.append(f"{TEAM_EMOJIS[ti]} **{member.display_name}**{civ_txt}")
        return "\n".join(lines)

    def _civ_content(self, team_idx: int, player_idx: int) -> str:
        member = self.team_members[team_idx][player_idx]
        done = sum(len(self.civs[ti]) for ti in range(team_idx)) + player_idx
        return (
            f"**🤝 Takımlı — {done+1}/{self._total_players()}**\n"
            f"{TEAM_EMOJIS[team_idx]} {member.display_name} (Takım {team_idx+1}):\n\n"
            + self._progress_header()
        )

    def _make_civ_view(self, team_idx: int, player_idx: int) -> "LeaderSelectView":
        wizard = self
        used = set(self._all_civs_flat())
        available = [(c, l) for c, l in ALL_LEADERS if l not in used]
        team_size = len(self.team_members[team_idx])

        async def on_pick(inter: discord.Interaction, _civ: str, leader: str):
            wizard.civs[team_idx].append(leader)
            next_p = player_idx + 1
            if next_p < team_size:
                await _wizard_edit(inter, wizard._civ_content(team_idx, next_p),
                                   wizard._make_civ_view(team_idx, next_p))
            elif team_idx + 1 < wizard.team_count:
                await _wizard_edit(inter, wizard._civ_content(team_idx + 1, 0),
                                   wizard._make_civ_view(team_idx + 1, 0))
            else:
                await wizard.show_winner(inter)

        return LeaderSelectView(available, on_pick)

    async def send_first(self, followup: discord.Webhook):
        await followup.send(content=self._civ_content(0, 0), view=self._make_civ_view(0, 0))

    async def show_winner(self, interaction: discord.Interaction):
        wizard = self
        view = discord.ui.View(timeout=300)
        for ti in range(self.team_count):
            btn = discord.ui.Button(
                label=f"{TEAM_EMOJIS[ti]} Takım {ti+1} Kazandı",
                style=discord.ButtonStyle.secondary,
            )
            async def winner_cb(inter: discord.Interaction, t=ti):
                await _wizard_edit(inter, "Zafer türü:", _VictoryView(wizard, winner_team=t))
            btn.callback = winner_cb
            view.add_item(btn)

        lines = []
        for ti, team in enumerate(self.team_members):
            for pi, member in enumerate(team):
                leader = self.civs[ti][pi] if pi < len(self.civs[ti]) else "?"
                lines.append(f"{TEAM_EMOJIS[ti]} **{member.display_name}** — {leader}")

        content = "**Kazanan takım?**\n\n" + "\n".join(lines)
        await _wizard_edit(interaction, content, view)

    async def finalize(self, interaction: discord.Interaction, winner_team: int, turn: str | None = None):
        for ti, team in enumerate(self.team_members):
            for pi, member in enumerate(team):
                leader = self.civs[ti][pi] if pi < len(self.civs[ti]) else ""
                if leader:
                    db.record_civ_play(str(member.id), str(member), leader, "team")

        w_players = [(str(m.id), str(m)) for m in self.team_members[winner_team]]
        l_players = [
            (str(m.id), str(m))
            for ti, team in enumerate(self.team_members) if ti != winner_team
            for m in team
        ]

        if self.team_count == 2 and w_players and l_players:
            w_results, l_results = db.record_team(w_players, l_players)
        else:
            w_results, l_results = [], []

        elo_by_id = {r.player_id: r for r in w_results + l_results}

        embed = discord.Embed(title="🤝 Teamer Maç Sonucu", color=discord.Color.green())
        for ti, team in enumerate(self.team_members):
            field_lines = []
            for pi, member in enumerate(team):
                leader = self.civs[ti][pi] if pi < len(self.civs[ti]) else "?"
                suffix = ""
                pid = str(member.id)
                if pid in elo_by_id:
                    r = elo_by_id[pid]
                    sign = "+" if r.delta >= 0 else ""
                    suffix = f"  `{sign}{r.delta} puan`"
                field_lines.append(f"{member.mention} — {leader}{suffix}")
            label = f"🏆 Takım {ti+1} (Kazanan)" if ti == winner_team else f"💀 Takım {ti+1} (Kaybeden)"
            embed.add_field(name=label, value="\n".join(field_lines) or "—", inline=False)

        footer = f"Maç ID: {self.match_id}"
        if self.victory:
            footer += f"  ·  Zafer: {self.victory}"
        if turn:
            footer += f"  ·  Tur: {turn}"
        embed.set_footer(text=footer)
        await _wizard_edit(interaction, "\u200b", None, embed)


# ---- Turn Modal ----

class _TurnModal(discord.ui.Modal, title="Tur Sayısı"):
    turn_input = discord.ui.TextInput(
        label="Maç kaçıncı turda bitti?",
        placeholder="örn: 250",
        max_length=10,
        required=False,
    )

    def __init__(self, on_done):
        super().__init__()
        self._on_done = on_done

    async def on_submit(self, interaction: discord.Interaction):
        turn = self.turn_input.value.strip() or None
        try:
            await self._on_done(interaction, turn)
        except (discord.NotFound, discord.InteractionResponded, discord.HTTPException):
            pass


# ---- Victory Type View ----

class _VictoryView(discord.ui.View):
    """Zafer türü seçimi; seçimden sonra tur modalını açar."""

    def __init__(self, wizard, winner_team: int | None = None):
        super().__init__(timeout=300)
        self._wizard = wizard
        self._winner_team = winner_team
        for emoji, label in VICTORY_TYPES:
            btn = discord.ui.Button(
                label=f"{emoji} {label}",
                style=discord.ButtonStyle.secondary,
            )
            btn.callback = self._make_cb(label)
            self.add_item(btn)

    def _make_cb(self, victory: str):
        async def cb(interaction: discord.Interaction):
            self._wizard.victory = victory
            if self._winner_team is not None:
                wt = self._winner_team
                await interaction.response.send_modal(
                    _TurnModal(lambda i, turn, _wt=wt: self._wizard.finalize(i, _wt, turn))
                )
            else:
                await interaction.response.send_modal(_TurnModal(self._wizard.finalize))
        return cb


# ---- Selector Views & Command ----

def _msg_check(interaction: discord.Interaction):
    return lambda m: m.author == interaction.user and m.channel == interaction.channel


class _TeamCountView(discord.ui.View):
    """Takım sayısı seçimi (2–5)."""

    def __init__(self):
        super().__init__(timeout=120)
        for n in range(2, 6):
            btn = discord.ui.Button(label=f"{n} Takım", style=discord.ButtonStyle.secondary)
            btn.callback = self._make_cb(n)
            self.add_item(btn)

    def _make_cb(self, count: int):
        async def cb(interaction: discord.Interaction):
            teams: list[list[discord.Member]] = []
            for ti in range(count):
                prompt = f"{TEAM_EMOJIS[ti]} Takım {ti+1} oyuncularını etiketle:"
                if ti == 0:
                    await interaction.response.edit_message(content=prompt, view=None)
                else:
                    await interaction.followup.send(prompt)
                try:
                    msg = await bot.wait_for("message", check=_msg_check(interaction), timeout=120)
                except asyncio.TimeoutError:
                    await interaction.followup.send("⏰ Süre doldu.", ephemeral=True)
                    return
                members = _ordered_mentions(msg)
                if not members:
                    await interaction.followup.send(
                        f"❌ Takım {ti+1}: en az bir oyuncu etiketle.",
                        ephemeral=True,
                    )
                    return
                teams.append(members)
            wizard = TeamReportWizard(teams)
            await wizard.send_first(interaction.followup)
        return cb


class _CreateReportTypeView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=120)

    @discord.ui.button(label="⚔️ FFA", style=discord.ButtonStyle.primary)
    async def ffa_btn(self, interaction: discord.Interaction, _):
        await interaction.response.edit_message(
            content="1. yer önce, herkesi etiketle:", view=None
        )
        try:
            msg = await bot.wait_for("message", check=_msg_check(interaction), timeout=120)
        except asyncio.TimeoutError:
            await interaction.followup.send("⏰ Süre doldu.", ephemeral=True)
            return
        members = _ordered_mentions(msg) or [m for m in msg.mentions if isinstance(m, discord.Member)]
        if len(members) < 2:
            await interaction.followup.send(
                "❌ En az 2 oyuncu etiketle.", ephemeral=True
            )
            return
        wizard = FfaReportWizard(members)
        await wizard.send_first(interaction.followup)

    @discord.ui.button(label="🤝 Takımlı", style=discord.ButtonStyle.success)
    async def team_btn(self, interaction: discord.Interaction, _):
        await interaction.response.edit_message(content="Kaç takım?", view=_TeamCountView())

    @discord.ui.button(label="⚔️ 2v2", style=discord.ButtonStyle.danger)
    async def twovtwo_btn(self, interaction: discord.Interaction, _):
        teams: list[list[discord.Member]] = []
        for ti in range(2):
            prompt = f"{TEAM_EMOJIS[ti]} Takım {ti+1} oyuncularını etiketle:"
            if ti == 0:
                await interaction.response.edit_message(content=prompt, view=None)
            else:
                await interaction.followup.send(prompt)
            try:
                msg = await bot.wait_for("message", check=_msg_check(interaction), timeout=120)
            except asyncio.TimeoutError:
                await interaction.followup.send("⏰ Süre doldu.", ephemeral=True)
                return
            members = _ordered_mentions(msg)
            if not members:
                await interaction.followup.send(
                    f"❌ Takım {ti+1}: en az bir oyuncu etiketle.", ephemeral=True
                )
                return
            teams.append(members)
        wizard = TeamReportWizard(teams)
        await wizard.send_first(interaction.followup)


@bot.tree.command(name="reportwithoutgameid", description="Oyuncuları etiketle ve sonucu kaydet")
async def reportwithoutgameid_command(interaction: discord.Interaction):
    await interaction.response.send_message("Maç türünü seç:", view=_CreateReportTypeView())



@bot.tree.command(name="stop", description="Bu kanaldaki aktif draft oturumunu iptal et")
async def stop_cmd(interaction: discord.Interaction):
    channel_id = interaction.channel_id
    stopped = False
    if channel_id in active_ffa_games:
        del active_ffa_games[channel_id]
        stopped = True
    if channel_id in active_team_games:
        del active_team_games[channel_id]
        stopped = True
    if stopped:
        await interaction.response.send_message("🛑 Aktif oyun iptal edildi.", ephemeral=False)
    else:
        await interaction.response.send_message("Bu kanalda aktif bir oyun yok.", ephemeral=True)


@bot.tree.command(name="uploademojis", description="Eksik lider emojilerini sunucuya yükle")
async def uploademojis_command(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)

    try:
        import aiohttp
    except ImportError:
        await interaction.followup.send("❌ aiohttp kurulu değil.", ephemeral=True)
        return
    try:
        from PIL import Image
    except ImportError:
        await interaction.followup.send("❌ Pillow kurulu değil. Railway yeniden deploy bekleniyor.", ephemeral=True)
        return

    import io as _io
    from leaders import _RAW, image_url as _img_url

    leader_to_civ = {leader: civ for civ, leader in _RAW}
    existing = {e.name for e in interaction.guild.emojis}

    uploaded, skipped, failed = [], [], []

    async with aiohttp.ClientSession() as session:
        for leader, emoji_name in LEADER_EMOJI_NAMES.items():
            if not emoji_name:
                continue
            if emoji_name in existing:
                skipped.append(emoji_name)
                continue
            civ = leader_to_civ.get(leader)
            if not civ:
                failed.append(f"{leader} (civ bulunamadı)")
                continue
            url = _img_url(civ, leader)
            try:
                async with session.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    if resp.status != 200:
                        failed.append(f"{leader} (HTTP {resp.status})")
                        continue
                    webp = await resp.read()
                img = Image.open(_io.BytesIO(webp))
                buf = _io.BytesIO()
                img.save(buf, format="PNG")
                await interaction.guild.create_custom_emoji(name=emoji_name, image=buf.getvalue())
                uploaded.append(emoji_name)
                await asyncio.sleep(1.2)   # Discord rate limit
            except discord.HTTPException as e:
                failed.append(f"{leader}: {e.text}")
            except Exception as e:
                failed.append(f"{leader}: {str(e)[:60]}")

    parts = []
    if uploaded:
        parts.append(f"✅ {len(uploaded)} emoji yüklendi")
    if skipped:
        parts.append(f"⏭️ {len(skipped)} zaten vardı, atlandı")
    if failed:
        parts.append(f"❌ {len(failed)} başarısız:\n" + "\n".join(failed[:15]))
    await interaction.followup.send("\n".join(parts) or "İşlem tamamlandı.", ephemeral=True)


@bot.tree.command(name="backupdb", description="Veritabanı yedeğini indir (sadece sunucu sahibi)")
async def backupdb_command(interaction: discord.Interaction):
    if interaction.user.id != interaction.guild.owner_id:
        await interaction.response.send_message("❌ Bu komut sadece sunucu sahibine açık.", ephemeral=True)
        return
    if not os.path.exists(db.DB_PATH):
        await interaction.response.send_message("❌ Veritabanı bulunamadı.", ephemeral=True)
        return
    await interaction.response.send_message(
        "📦 Veritabanı yedeği:",
        file=discord.File(db.DB_PATH, filename="scores_backup.db"),
        ephemeral=True,
    )


# ===========================================================================
# CPL Draft
# ===========================================================================

_CPL_PICKS = 3  # her oyuncuya verilen seçenek sayısı


class CplDraftSession:
    def __init__(self, players: list[discord.Member]):
        self.players = players
        self.current_idx = 0
        self.chosen: dict[int, str] = {}   # player.id → seçilen lider

        pool = [leader for _, leader in ALL_LEADERS]
        random.shuffle(pool)
        self.picks: dict[int, list[str]] = {
            m.id: pool[i * _CPL_PICKS: (i + 1) * _CPL_PICKS]
            for i, m in enumerate(players)
        }

    def current(self) -> discord.Member:
        return self.players[self.current_idx]

    def done(self) -> bool:
        return self.current_idx >= len(self.players)

    def progress_embed(self) -> discord.Embed:
        embed = discord.Embed(title="🎮 CPL Draft", color=discord.Color.blurple())
        lines = []
        for i, m in enumerate(self.players):
            if m.id in self.chosen:
                lines.append(f"✅ **{m.display_name}** — seçti")
            elif i == self.current_idx:
                lines.append(f"⏳ **{m.display_name}** — sıra sende!")
            else:
                lines.append(f"🕐 {m.display_name} — bekliyor")
        embed.description = "\n".join(lines)
        if not self.done():
            embed.set_footer(text=f"Sıra: {self.current().display_name}")
        return embed

    def result_embed(self) -> discord.Embed:
        embed = discord.Embed(title="🎮 CPL Draft Sonucu", color=discord.Color.blurple())
        for m in self.players:
            embed.add_field(name=m.display_name, value=self.chosen.get(m.id, "—"), inline=True)
        return embed


class CplPickView(discord.ui.View):
    def __init__(self, session: CplDraftSession):
        super().__init__(timeout=180)
        self._s = session
        for leader in session.picks[session.current().id]:
            btn = discord.ui.Button(label=leader, style=discord.ButtonStyle.secondary)
            btn.callback = self._make_cb(session.current(), leader)
            self.add_item(btn)

    def _make_cb(self, member: discord.Member, leader: str):
        async def cb(interaction: discord.Interaction):
            if interaction.user.id != member.id:
                await interaction.response.send_message(
                    f"Sıra {member.display_name}'da!", ephemeral=True
                )
                return
            s = self._s
            s.chosen[member.id] = leader
            s.current_idx += 1
            if s.done():
                await interaction.response.edit_message(embed=s.result_embed(), view=None, content=None)
            else:
                await interaction.response.edit_message(embed=s.progress_embed(), view=CplPickView(s), content=None)
        return cb


@bot.tree.command(name="draft", description="CPL formatında lider drafti başlat")
async def draft_command(interaction: discord.Interaction):
    players = get_voice_members(interaction)
    if not players:
        await interaction.response.send_message("❌ Ses kanalında olman gerekiyor.", ephemeral=True)
        return
    max_players = len(ALL_LEADERS) // _CPL_PICKS
    if len(players) > max_players:
        await interaction.response.send_message(
            f"❌ Çok fazla oyuncu ({len(players)}). Maksimum {max_players}.", ephemeral=True
        )
        return
    random.shuffle(players)
    session = CplDraftSession(players)
    await interaction.response.send_message(embed=session.progress_embed(), view=CplPickView(session))


@bot.tree.command(name="help", description="Tüm komutları listele")
async def help_command(interaction: discord.Interaction):
    embed = discord.Embed(
        title="Komutlar",
        description=(
            "`/coinflip` — Yazı mı tura mı.\n"
            "`/draft` — Ses kanalıyla CPL formatında lider drafti başlatır.\n"
            "`/ffa` — Ses kanalıyla FFA draft başlatır.\n"
            "`/id` — Kendi puan ve lider istatistiklerini gösterir.\n"
            "`/leaderboard` — Puan sıralamasını gösterir.\n"
            "`/mostplayed` — En çok oynanan liderleri listeler.\n"
            "`/quickffa` — Ses kanalı olmadan lider ban yap ve oyunculara dağıt.\n"
            "`/quickteam` — Ses kanalı olmadan lider ban yap ve takımlara dağıt.\n"
            "`/reportwithid` — Maç ID'si ile sonucu kaydet.\n"
            "`/reportwithoutgameid` — FFA, 2v2 veya takımlı maç sonucunu kaydet.\n"
            "`/stop` — Aktif draft oturumunu iptal eder.\n"
            "`/team` — Ses kanalıyla 2 takımlı sıralı draft başlatır."
        ),
        color=discord.Color.blurple(),
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


# ===========================================================================
# Events
# ===========================================================================

@bot.event
async def on_ready():
    db.init_db()
    synced_count = 0
    for guild in bot.guilds:
        try:
            bot.tree.copy_global_to(guild=guild)
            cmds = await bot.tree.sync(guild=guild)
            print(f"✅ {guild.name} — {len(cmds)} komut sync edildi: {[c.name for c in cmds]}")
            synced_count += 1
        except Exception as e:
            print(f"❌ {guild.name} sync hatası: {e}")
    print(f"✅ {bot.user} olarak giriş yapıldı. ({synced_count}/{len(bot.guilds)} sunucu)")
    await bot.change_presence(activity=discord.Game(name="Simfest"))

# ===========================================================================
# Entry point
# ===========================================================================

if __name__ == "__main__":
    if not TOKEN:
        raise RuntimeError("DISCORD_TOKEN ortam değişkeni ayarlanmamış!")
    try:
        bot.run(TOKEN, log_handler=None)
    except discord.LoginFailure:
        print("❌ HATA: Discord token geçersiz. DISCORD_TOKEN değişkenini kontrol et.")
        raise
    except discord.PrivilegedIntentsRequired:
        print("❌ HATA: Ayrıcalıklı intents (members/message_content) Discord Developer Portal'da kapalı.")
        raise
    except Exception as e:
        print(f"❌ HATA: Bot başlatılamadı: {e}")
        raise
