"""
ELO-based score database.
- FFA  : starts at 1000, pairwise ELO (normalised K so total swing ≈ one 1v1 match)
- Team : starts at 100,  team-average ELO (each player updated individually)

ELO formulas
    E_A = 1 / (1 + 10 ^ ((R_B - R_A) / 400))   expected score
    R'  = R + K * (W - E)                         new rating
"""

import sqlite3
import os
from typing import NamedTuple

DB_PATH = os.getenv("DB_PATH", os.path.join(os.path.dirname(__file__), "scores.db"))

# ── Constants ────────────────────────────────────────────────────────────────
FFA_START  = 1000
TEAM_START = 100
K          = 32          # base K-factor (same as standard chess for active players)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _expected(r_a: float, r_b: float) -> float:
    """E_A: probability that A beats B."""
    return 1 / (1 + 10 ** ((r_b - r_a) / 400))


class EloResult(NamedTuple):
    player_id:  str
    player_tag: str
    old_rating: int
    new_rating: int
    delta:      int          # positive = gained, negative = lost


# ── Schema ───────────────────────────────────────────────────────────────────

def init_db() -> None:
    with _conn() as c:
        c.executescript(f"""
            CREATE TABLE IF NOT EXISTS ffa_scores (
                player_id   TEXT PRIMARY KEY,
                player_tag  TEXT NOT NULL,
                rating      INTEGER DEFAULT {FFA_START},
                games       INTEGER DEFAULT 0,
                wins        INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS team_scores (
                player_id   TEXT PRIMARY KEY,
                player_tag  TEXT NOT NULL,
                rating      INTEGER DEFAULT {TEAM_START},
                games       INTEGER DEFAULT 0,
                wins        INTEGER DEFAULT 0,
                losses      INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS civ_plays (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                player_id   TEXT    NOT NULL,
                player_tag  TEXT    NOT NULL,
                civ         TEXT    NOT NULL,
                game_type   TEXT    NOT NULL   -- 'ffa' | 'team'
            );
            CREATE INDEX IF NOT EXISTS idx_civ_game  ON civ_plays(civ, game_type);
            CREATE INDEX IF NOT EXISTS idx_civ_player ON civ_plays(player_id, game_type);
        """)


# ── FFA ELO ──────────────────────────────────────────────────────────────────

def record_ffa(
    players: list[tuple[str, str]]   # [(player_id, player_tag)] 1st → last
) -> list[EloResult]:
    """
    Update FFA ratings using pairwise ELO.
    K is divided by (N-1) so the total rating swing per game ≈ one 1v1 match.
    Returns EloResult for each player in the same order as input.
    """
    n = len(players)
    if n < 2:
        return []

    k_pair = K / (n - 1)   # normalised K per pairwise match

    # Fetch (or default) current ratings
    with _conn() as c:
        ratings: dict[str, int] = {}
        for pid, _ in players:
            row = c.execute(
                "SELECT rating FROM ffa_scores WHERE player_id = ?", (pid,)
            ).fetchone()
            ratings[pid] = int(row["rating"]) if row else FFA_START

    # Accumulate deltas from all pairwise comparisons
    deltas: dict[str, float] = {pid: 0.0 for pid, _ in players}
    for i in range(n):
        for j in range(i + 1, n):
            pid_a = players[i][0]   # A finished above B → A wins
            pid_b = players[j][0]
            e_a = _expected(ratings[pid_a], ratings[pid_b])
            deltas[pid_a] += k_pair * (1 - e_a)
            deltas[pid_b] += k_pair * (0 - (1 - e_a))

    # Write to DB and build results
    results: list[EloResult] = []
    with _conn() as c:
        for rank, (pid, tag) in enumerate(players):
            old  = ratings[pid]
            new  = max(1, round(old + deltas[pid]))  # floor at 1
            won  = 1 if rank == 0 else 0
            c.execute("""
                INSERT INTO ffa_scores (player_id, player_tag, rating, games, wins)
                VALUES (?, ?, ?, 1, ?)
                ON CONFLICT(player_id) DO UPDATE SET
                    player_tag = excluded.player_tag,
                    rating     = ?,
                    games      = games + 1,
                    wins       = wins + excluded.wins
            """, (pid, tag, new, won, new))
            results.append(EloResult(pid, tag, old, new, new - old))

    return results


def ffa_leaderboard() -> list[sqlite3.Row]:
    with _conn() as c:
        return c.execute("""
            SELECT player_tag, rating, games, wins,
                   ROUND(100.0 * wins / NULLIF(games, 0), 1) AS win_pct
            FROM ffa_scores
            ORDER BY rating DESC
        """).fetchall()


def ffa_player(player_id: str) -> sqlite3.Row | None:
    with _conn() as c:
        return c.execute(
            "SELECT * FROM ffa_scores WHERE player_id = ?", (player_id,)
        ).fetchone()


# ── Team ELO ─────────────────────────────────────────────────────────────────

def record_team(
    winners: list[tuple[str, str]],   # [(player_id, player_tag)]
    losers:  list[tuple[str, str]],
) -> tuple[list[EloResult], list[EloResult]]:
    """
    Update team ratings using team-average ELO.
    E is calculated from average team ratings; each player is updated individually.
    Returns (winner_results, loser_results).
    """
    all_players = winners + losers

    with _conn() as c:
        ratings: dict[str, int] = {}
        for pid, _ in all_players:
            row = c.execute(
                "SELECT rating FROM team_scores WHERE player_id = ?", (pid,)
            ).fetchone()
            ratings[pid] = int(row["rating"]) if row else TEAM_START

    avg_w = sum(ratings[pid] for pid, _ in winners) / len(winners)
    avg_l = sum(ratings[pid] for pid, _ in losers)  / len(losers)
    e_win = _expected(avg_w, avg_l)   # expected win prob for winning team
    e_los = 1 - e_win

    winner_results: list[EloResult] = []
    loser_results:  list[EloResult] = []

    with _conn() as c:
        for pid, tag in winners:
            old = ratings[pid]
            new = max(1, round(old + K * (1 - e_win)))
            c.execute("""
                INSERT INTO team_scores (player_id, player_tag, rating, games, wins, losses)
                VALUES (?, ?, ?, 1, 1, 0)
                ON CONFLICT(player_id) DO UPDATE SET
                    player_tag = excluded.player_tag,
                    rating     = ?,
                    games      = games + 1,
                    wins       = wins + 1
            """, (pid, tag, new, new))
            winner_results.append(EloResult(pid, tag, old, new, new - old))

        for pid, tag in losers:
            old = ratings[pid]
            new = max(1, round(old + K * (0 - e_los)))
            c.execute("""
                INSERT INTO team_scores (player_id, player_tag, rating, games, wins, losses)
                VALUES (?, ?, ?, 1, 0, 1)
                ON CONFLICT(player_id) DO UPDATE SET
                    player_tag = excluded.player_tag,
                    rating     = ?,
                    games      = games + 1,
                    losses     = losses + 1
            """, (pid, tag, new, new))
            loser_results.append(EloResult(pid, tag, old, new, new - old))

    return winner_results, loser_results


def team_leaderboard() -> list[sqlite3.Row]:
    with _conn() as c:
        return c.execute("""
            SELECT player_tag, rating, games, wins, losses,
                   ROUND(100.0 * wins / NULLIF(games, 0), 1) AS win_pct
            FROM team_scores
            ORDER BY rating DESC
        """).fetchall()


def team_player(player_id: str) -> sqlite3.Row | None:
    with _conn() as c:
        return c.execute(
            "SELECT * FROM team_scores WHERE player_id = ?", (player_id,)
        ).fetchone()


# ── Civ plays ─────────────────────────────────────────────────────────────────

def record_civ_play(player_id: str, player_tag: str, civ: str, game_type: str) -> None:
    with _conn() as c:
        c.execute(
            "INSERT INTO civ_plays (player_id, player_tag, civ, game_type) VALUES (?, ?, ?, ?)",
            (player_id, player_tag, civ, game_type),
        )


def most_played_civs(game_type: str, limit: int = 15) -> list[sqlite3.Row]:
    with _conn() as c:
        return c.execute("""
            SELECT civ,
                   COUNT(*)                    AS plays,
                   COUNT(DISTINCT player_id)   AS unique_players
            FROM civ_plays
            WHERE game_type = ?
            GROUP BY civ
            ORDER BY plays DESC
            LIMIT ?
        """, (game_type, limit)).fetchall()


def player_most_played(player_id: str, game_type: str, limit: int = 3) -> list[sqlite3.Row]:
    with _conn() as c:
        return c.execute("""
            SELECT civ, COUNT(*) AS plays
            FROM civ_plays
            WHERE player_id = ? AND game_type = ?
            GROUP BY civ
            ORDER BY plays DESC
            LIMIT ?
        """, (player_id, game_type, limit)).fetchall()
