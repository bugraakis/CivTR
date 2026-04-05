import asyncio
import sys
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
import discord
from discord import app_commands
from discord.ext import commands
import random
import string
import re
from collections import Counter
import os
from dotenv import load_dotenv

from leaders import CIVS, LEADERS_BY_CIV, image_url
from civ_emojis import CIV_EMOJIS
from leader_emojis import LEADER_EMOJI_NAMES
import database as db

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

# ---------------------------------------------------------------------------
# All leaders flat list
# ---------------------------------------------------------------------------
ALL_LEADERS: list[tuple[str, str]] = [
    (civ, leader)
    for civ, leaders in LEADERS_BY_CIV.items()
    for leader in leaders
]

# Civ pages for select menus (max 25 options)
_CIV_PAGES: list[list[str]] = [CIVS[i : i + 25] for i in range(0, len(CIVS), 25)]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
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

bot = commands.Bot(command_prefix="!", intents=intents)

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def get_voice_members(interaction: discord.Interaction) -> list[discord.Member]:
    member = interaction.guild.get_member(interaction.user.id)
    if member is None or member.voice is None or member.voice.channel is None:
        return []
    return [m for m in member.voice.channel.members if not m.bot]


def _make_match_id(prefix: str) -> str:
    """Generate a short random match ID like FFA-A3K7X2."""
    code = "".join(random.choices(string.ascii_uppercase + string.digits, k=6))
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
    if not name or not guild:
        return ""
    emoji = discord.utils.get(guild.emojis, name=name)
    return str(emoji) if emoji else ""


def build_pool_embed(
    member: discord.Member,
    pool: list[tuple[str, str]],
    color: discord.Color,
    guild: discord.Guild | None = None,
) -> discord.Embed:
    embed = discord.Embed(
        title=f"🎴 {member.display_name}",
        description=f"{len(pool)} lider",
        color=color,
    )
    for civ, leader in pool:
        emoji = leader_emoji_str(leader, guild)
        label = f"{emoji} {leader}".strip() if emoji else leader
        embed.add_field(name=label, value=civ, inline=True)
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
    pools = {m: remaining[i * per_player : (i + 1) * per_player] for i, m in enumerate(members)}
    for i, pair in enumerate(remaining[n * per_player :]):
        pools[members[i]].append(pair)
    return pools


# ===========================================================================
# FFA GAME — new flow: map vote → per-player civ ban → pool distribution
# ===========================================================================

class FFAGame:
    def __init__(self, players: list[discord.Member]):
        self.players = players
        self.map_votes: dict[int, str] = {}   # player_id -> map_name
        self.selected_map: str | None = None
        self.bans: dict[int, str] = {}        # player_id -> civ_name

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

    def record_ban(self, player_id: int, civ: str):
        self.bans[player_id] = civ

    def all_bans_done(self) -> bool:
        return len(self.bans) == len(self.players)

    def get_banned_civs(self) -> set[str]:
        return set(self.bans.values())

    # ---- pool distribution ----

    def distribute_pools(self) -> dict[discord.Member, list[tuple[str, str]]]:
        return _distribute_leaders(self.players, banned_civs=self.get_banned_civs())


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

class BanPhaseView(discord.ui.View):
    """Single message shared by all players. Each reacts with a civ emoji then clicks Confirm."""

    def __init__(self, game: FFAGame):
        super().__init__(timeout=None)
        self.game = game
        self.message: discord.Message | None = None  # set after send

    def build_embed(self) -> discord.Embed:
        status_lines = []
        for player in self.game.players:
            if player.id in self.game.bans:
                civ = self.game.bans[player.id]
                status_lines.append(f"✅ {player.mention} → {civ_emoji_str(civ)} **{civ}**")
            else:
                status_lines.append(f"⏳ {player.mention}")

        civ_ref = "  ".join(
            f"{civ_emoji_str(c)}`{c}`" for c in CIVS if CIV_EMOJIS.get(c)
        ) or "*(civ_emojis.py henüz doldurulmadı)*"

        embed = discord.Embed(
            title="🚫 Medeniyet Ban Aşaması",
            description=(
                "Banlamak istediğin medeniyetin emojisini **bu mesaja** ekle, "
                "ardından **✅ Onayla**'ya bas. Herkes aynı anda yapabilir."
            ),
            color=discord.Color.orange(),
        )
        embed.add_field(name="Durum", value="\n".join(status_lines), inline=False)
        embed.add_field(name="Medeniyet Emojileri", value=civ_ref[:1024], inline=False)
        return embed

    @discord.ui.button(label="✅ Onayla", style=discord.ButtonStyle.green)
    async def confirm_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        player = next((p for p in self.game.players if p.id == interaction.user.id), None)
        if not player:
            await interaction.response.send_message("Bu oyuna dahil değilsin!", ephemeral=True)
            return

        if player.id in self.game.bans:
            await interaction.response.send_message("Zaten ban yaptın!", ephemeral=True)
            return

        # Read this specific player's reactions on the shared message
        message = await interaction.channel.fetch_message(interaction.message.id)
        banned_civ: str | None = None
        for reaction in message.reactions:
            async for user in reaction.users():
                if user.id == player.id:
                    banned_civ = emoji_to_civ(str(reaction.emoji))
                    break
            if banned_civ:
                break

        if not banned_civ:
            await interaction.response.send_message(
                "Önce banlamak istediğin medeniyetin emojisini bu mesaja ekle, sonra Onayla'ya bas.",
                ephemeral=True,
            )
            return

        self.game.record_ban(player.id, banned_civ)
        embed = self.build_embed()

        if self.game.all_bans_done():
            await interaction.response.edit_message(embed=embed, view=self)
            await _finalize_ffa_pools(interaction.channel, self.game, ban_message=self.message)
        else:
            await interaction.response.edit_message(embed=embed, view=self)


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
    banned = game.get_banned_civs()
    pools = game.distribute_pools()
    mentions = " ".join(m.mention for m in game.players)

    ban_summary = "  ·  ".join(
        f"{civ_emoji_str(c)} **{c}**" for c in sorted(banned)
    ) or "Yok"

    match_id = _make_match_id("FFA")
    header = discord.Embed(
        title=f"🗺️ {game.selected_map}  ·  ⚔️ FFA Draft Tamamlandı!",
        description=f"**Banlanan Medeniyetler:** {ban_summary}",
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
            placeholder=f"Medeniyet seç — Sayfa {self.page + 1}/{len(_CIV_PAGES)}",
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
    """Return the full ordered sequence of (action_type, team_number) for a team game."""
    queue: list[tuple[str, int]] = []

    # 6 harita ban: T1, T2, T1, T2, T1, T2  →  son harita seçildi
    for i in range(6):
        queue.append(("map_ban", 1 if i % 2 == 0 else 2))

    # Civ ban aşaması 1: T1, T2, T1
    queue += [("civ_ban", 1), ("civ_ban", 2), ("civ_ban", 1)]

    # Civ seçim aşaması 1: T1:1, T2:2, T1:1  →  her takım 2 seçime sahip
    queue += [("civ_pick", 1), ("civ_pick", 2), ("civ_pick", 2), ("civ_pick", 1)]

    if team_size > 2:
        # Civ ban aşaması 2: T2, T1, T2, T1
        queue += [("civ_ban", 2), ("civ_ban", 1), ("civ_ban", 2), ("civ_ban", 1)]

        # Devam eden seçimler: önce T2:1, sonra T1:2, T2:2, T1:2... dolu olana kadar
        t1, t2 = 2, 2
        if t2 < team_size:
            queue.append(("civ_pick", 2))
            t2 += 1
        turn = 1
        while t1 < team_size or t2 < team_size:
            for _ in range(2):
                if turn == 1 and t1 < team_size:
                    queue.append(("civ_pick", 1))
                    t1 += 1
                elif turn == 2 and t2 < team_size:
                    queue.append(("civ_pick", 2))
                    t2 += 1
            turn = 2 if turn == 1 else 1

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

        self.banned_civs: list[tuple[int, str]] = []  # (takım, civ)
        self.picked_civs: list[tuple[int, str]] = []  # (takım, civ)

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

        others = [p for p in self.all_players if p not in (self.rep1, self.rep2)]
        random.shuffle(others)
        half = len(others) // 2
        self.team1 = [self.team1_rep] + others[:half]
        self.team2 = [self.team2_rep] + others[half:]
        self.action_queue = _build_team_action_queue(self.team_size)

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

        t1_bans  = [f"{civ_emoji_str(c)}{c}" for t, c in self.banned_civs if t == 1]
        t2_bans  = [f"{civ_emoji_str(c)}{c}" for t, c in self.banned_civs if t == 2]
        t1_picks = [f"{civ_emoji_str(c)}{c}" for t, c in self.picked_civs  if t == 1]
        t2_picks = [f"{civ_emoji_str(c)}{c}" for t, c in self.picked_civs  if t == 2]

        action = self.current_action()
        if action:
            at, team = action
            labels = {"map_ban": "🗺️ Harita Banlıyor", "civ_ban": "🚫 Civ Banlıyor", "civ_pick": "✅ Civ Seçiyor"}
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
            title = "Civ Banlıyor" if at == "civ_ban" else "Civ Seçiyor"
            embed = discord.Embed(
                title=f"Takım {team} — {title}",
                description=f"{rep.mention} {verb} istediğin medeniyetin emojisini bu mesaja ekle, ardından butona bas.",
                color=color,
            )
            view = TeamCivActionView(self, at, team, rep)

        self.prompt_msg = await channel.send(embed=embed, view=view)

    async def _finalize(self, channel: discord.TextChannel):
        t1_picks = [c for t, c in self.picked_civs if t == 1]
        t2_picks = [c for t, c in self.picked_civs if t == 2]

        match_id = _make_match_id("TEAM")
        embed = discord.Embed(
            title=f"🗺️ {self.selected_map} — Draft Tamamlandı!",
            color=discord.Color.gold(),
        )
        embed.set_footer(text=f"Maç ID: {match_id}")
        embed.add_field(
            name="🔴 Takım 1",
            value="\n".join(f"{civ_emoji_str(c)} {c}" for c in t1_picks) or "—",
            inline=True,
        )
        embed.add_field(
            name="🔵 Takım 2",
            value="\n".join(f"{civ_emoji_str(c)} {c}" for c in t2_picks) or "—",
            inline=True,
        )
        mentions = " ".join(m.mention for m in self.all_players)
        await channel.send(content=mentions, embed=embed)
        active_team_games.pop(channel.id, None)


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
        await interaction.response.edit_message(embed=self.game.build_summary_embed(), view=None)
        self.game.summary_msg = await interaction.original_response()
        await self.game._prompt_action(interaction.channel, self.game.current_action())


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

        message = await interaction.channel.fetch_message(interaction.message.id)
        civ: str | None = None
        for reaction in message.reactions:
            async for user in reaction.users():
                if user.id == self.rep.id:
                    civ = emoji_to_civ(str(reaction.emoji))
                    break
            if civ:
                break

        if not civ:
            await interaction.response.send_message(
                "Önce medeniyetin emojisini bu mesaja ekle, sonra butona bas.", ephemeral=True
            )
            return

        used = {c for _, c in self.game.banned_civs} | {c for _, c in self.game.picked_civs}
        if civ in used:
            status = "banlandı" if civ in {c for _, c in self.game.banned_civs} else "seçildi"
            await interaction.response.send_message(f"**{civ}** zaten {status}!", ephemeral=True)
            return

        if self.action_type == "civ_ban":
            self.game.banned_civs.append((self.team, civ))
        else:
            self.game.picked_civs.append((self.team, civ))

        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(view=self)
        await self.game.advance(interaction.channel)


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
        pools = [remaining[i * per_player : (i + 1) * per_player] for i in range(n)]
        for i, pair in enumerate(remaining[n * per_player :]):
            pools[i].append(pair)

        guild = interaction.guild
        embeds = []
        for i, pool in enumerate(pools):
            embed = discord.Embed(
                title=f"🎴 Oyuncu {i + 1}",
                description=f"{len(pool)} lider",
                color=PLAYER_COLORS[i % len(PLAYER_COLORS)],
            )
            for civ, leader in pool:
                emoji = leader_emoji_str(leader, guild)
                label = f"{emoji} {leader}".strip() if emoji else leader
                embed.add_field(name=label, value=civ, inline=True)
            embeds.append(embed)

        first, rest = embeds[:10], embeds[10:]
        await interaction.response.edit_message(content=None, embeds=first, view=None)
        for chunk in [rest[j : j + 10] for j in range(0, len(rest), 10)]:
            await interaction.followup.send(embeds=chunk)


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
        await interaction.response.edit_message(
            content=session.ban_status(), view=TeamBanPhaseView(session)
        )


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
        teams = [remaining[i * per_team : (i + 1) * per_team] for i in range(n)]
        for i, pair in enumerate(remaining[n * per_team :]):
            teams[i].append(pair)

        embeds = [
            discord.Embed(
                title=f"{TEAM_EMOJIS[i % len(TEAM_EMOJIS)]} Takım {i + 1}",
                description=f"{len(leaders)} lider",
                color=TEAM_COLORS[i % len(TEAM_COLORS)],
            )
            for i, leaders in enumerate(teams)
        ]
        guild = interaction.guild
        for i, leaders in enumerate(teams):
            for civ, leader in leaders:
                emoji = leader_emoji_str(leader, guild)
                label = f"{emoji} {leader}".strip() if emoji else leader
                embeds[i].add_field(name=label, value=civ, inline=True)

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
            await interaction.response.edit_message(
                content=session.ban_status(), view=TeamBanPhaseView(session)
            )
        return cb


# ===========================================================================
# Slash Commands
# ===========================================================================

@bot.tree.command(name="team", description="Ses kanalıyla 2 takımlı sıralı draft: harita ban → civ ban → civ seçim")
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


@bot.tree.command(name="ffa", description="Ses kanalıyla FFA: harita oyu → civ ban (emoji) → lider havuzu dağıtımı")
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
        name = member.display_name if member else f"<@{pid}>"
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
        label="Sıralama — her satıra bir oyuncu + medeniyet",
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
                suffix = f"  `{r.old_rating} → {r.new_rating} ({sign}{r.delta})`"
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
        label="Sıralama (1.→son) — @oyuncu + medeniyet emojisi",
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
                suffix = f"  `{r.old_rating} → {r.new_rating} ({sign}{r.delta})`"
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
        label="Kazanan Takım — @oyuncu + medeniyet emojisi",
        placeholder="@Oyuncu1 <:america:123>\n@Oyuncu2 <:greece:456>",
        style=discord.TextStyle.paragraph,
        max_length=700,
    )
    losers = discord.ui.TextInput(
        label="Kaybeden Takım — @oyuncu + medeniyet emojisi",
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


class IdTypeView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=60)

    @discord.ui.button(label="⚔️ FFA Sonucu Gir", style=discord.ButtonStyle.primary)
    async def ffa_btn(self, interaction: discord.Interaction, _btn):
        await interaction.response.send_modal(FfaResultModal())

    @discord.ui.button(label="🤝 Teamer Sonucu Gir", style=discord.ButtonStyle.success)
    async def team_btn(self, interaction: discord.Interaction, _btn):
        await interaction.response.send_modal(TeamerResultModal())

    @discord.ui.button(label="📊 İstatistiklerim", style=discord.ButtonStyle.secondary)
    async def stats_btn(self, interaction: discord.Interaction, _btn):
        uid = str(interaction.user.id)
        name = interaction.user.display_name

        ffa  = db.ffa_player(uid)
        team = db.team_player(uid)

        embed = discord.Embed(
            title=f"📊 {name} — İstatistikler",
            color=discord.Color.blurple(),
        )

        ffa_civs  = db.player_most_played(uid, "ffa",  limit=3)
        team_civs = db.player_most_played(uid, "team", limit=3)

        def civ_line(rows) -> str:
            return ", ".join(f"{r['civ']} ({r['plays']}x)" for r in rows) or "—"

        if ffa:
            win_pct = round(100 * ffa["wins"] / ffa["games"], 1) if ffa["games"] else 0
            embed.add_field(
                name="⚔️ FFA",
                value=(
                    f"ELO: **{ffa['rating']}**\n"
                    f"Maç: {ffa['games']}  ·  1. bitiş: {ffa['wins']}  ·  %{win_pct}\n"
                    f"En çok: {civ_line(ffa_civs)}"
                ),
                inline=True,
            )
        else:
            embed.add_field(name="⚔️ FFA", value=f"Kayıt yok (başlangıç: {db.FFA_START})", inline=True)

        if team:
            win_pct = round(100 * team["wins"] / team["games"], 1) if team["games"] else 0
            embed.add_field(
                name="🤝 Teamer",
                value=(
                    f"ELO: **{team['rating']}**\n"
                    f"G/M: {team['wins']}/{team['losses']}  ·  %{win_pct} kazanma\n"
                    f"En çok: {civ_line(team_civs)}"
                ),
                inline=True,
            )
        else:
            embed.add_field(name="🤝 Teamer", value=f"Kayıt yok (başlangıç: {db.TEAM_START})", inline=True)

        await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="id", description="Maç sonucu gir ve ELO kaydet, veya kendi istatistiklerine bak")
async def id_command(interaction: discord.Interaction):
    await interaction.response.send_message(
        "Ne yapmak istiyorsun?", view=IdTypeView(), ephemeral=True
    )


@bot.tree.command(name="report", description="Maç ID'si ile sonuç raporla — FFA veya takım otomatik algılanır")
@app_commands.describe(match_id="Maç ID'si (örnek: FFA-A3K7X2 veya TEAM-B5K8X1)")
async def report_command(interaction: discord.Interaction, match_id: str):
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


@bot.tree.command(name="autodraftffa", description="Oyuncu sayısı seç, lider ban et → havuzlar otomatik dağıtılır (ses kanalı gerekmez)")
async def autodraftffa_command(interaction: discord.Interaction):
    await interaction.response.send_message("Kaç oyuncu?", view=AutoDraftFfaCountView())


@bot.tree.command(name="autodraftteam", description="Takım sayısı seç, lider ban et → her takıma otomatik dağıtılır (ses kanalı gerekmez)")
async def autodraftteam_command(interaction: discord.Interaction):
    await interaction.response.send_message("Kaç takım olsun?", view=AutoDraftCountView())


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
                    f"ELO **{row['rating']}** · {row['games']} maç · "
                    f"%{row['win_pct'] or 0} 1.sıra"
                )
            else:
                lines.append(
                    f"{prefix} {row['player_tag']} — "
                    f"ELO **{row['rating']}** · "
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


@bot.tree.command(name="leaderboard", description="FFA ve teamer ELO sıralaması — butonlarla mod ve sayfa değiştirilebilir")
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
            title=f"{title} — En Çok Oynanan Medeniyetler",
            description="\n".join(lines),
            color=color,
        )
        await interaction.response.edit_message(embed=embed, view=None)


@bot.tree.command(name="mostplayed", description="En çok oynanan medeniyetleri FFA veya teamer modunda sıralı göster")
async def mostplayed_command(interaction: discord.Interaction):
    await interaction.response.send_message("Hangi mod?", view=MostPlayedTypeView())


@bot.tree.command(name="help", description="Tüm bot komutlarını ve açıklamalarını listele")
async def help_command(interaction: discord.Interaction):
    embed = discord.Embed(title="📖 Civ6 Bot Commands", color=discord.Color.blurple())
    embed.add_field(
        name="/ffa",
        value="Ses kanalındaki oyuncularla harita oyu → civ ban (emoji reaksiyon) → lider havuzu dağıtımı.",
        inline=False,
    )
    embed.add_field(
        name="/team @rakip",
        value="Ses kanalıyla 2 takımlı sıralı draft: 6 harita ban → civ ban turu → sıralı civ seçim.",
        inline=False,
    )
    embed.add_field(
        name="/autodraftffa",
        value="Oyuncu sayısını seç, lider ban et → havuzlar otomatik dağıtılır (ses kanalı gerekmez).",
        inline=False,
    )
    embed.add_field(
        name="/autodraftteam",
        value="Takım sayısını seç, lider ban et → her takıma otomatik dağıtılır (ses kanalı gerekmez).",
        inline=False,
    )
    embed.add_field(
        name="/id",
        value="Yeni maç sonucu gir (otomatik ID üretilir, ELO kaydedilir) · Kendi istatistiklerine bak.",
        inline=False,
    )
    embed.add_field(
        name="/report <maç_id>",
        value="Draft'ta üretilen ID ile sonucu raporla. `FFA-XXXXXX` → FFA sıralaması, `TEAM-XXXXXX` → takım sonucu. Her satıra `@oyuncu <medeniyet_emojisi>` yaz.",
        inline=False,
    )
    embed.add_field(
        name="/leaderboard",
        value="FFA ve teamer ELO sıralaması. ⚔️ FFA / 🤝 Teamer butonuyla mod, ◀ ▶ ile sayfa değiştir.",
        inline=False,
    )
    embed.add_field(
        name="/mostplayed",
        value="En çok oynanan medeniyetleri göster. FFA veya teamer modunu butonla seç.",
        inline=False,
    )
    embed.add_field(
        name="⚙️ Emoji Ayarı",
        value="`civ_emojis.py` dosyasına her medeniyetin Discord emojisini ekle.",
        inline=False,
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


# ===========================================================================
# Events
# ===========================================================================

@bot.event
async def on_ready():
    db.init_db()
    for guild in bot.guilds:
        bot.tree.copy_global_to(guild=guild)
        await bot.tree.sync(guild=guild)
    print(f"✅ {bot.user} olarak giriş yapıldı.")
    print(f"✅ Slash komutları {len(bot.guilds)} sunucuya anında senkronize edildi.")
    await bot.change_presence(activity=discord.Game(name="Civilization VI | /ffa"))


# ===========================================================================
# Entry point
# ===========================================================================

if __name__ == "__main__":
    if not TOKEN:
        raise RuntimeError("DISCORD_TOKEN ortam değişkeni ayarlanmamış!")
    bot.run(TOKEN)
