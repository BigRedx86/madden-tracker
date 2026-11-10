"""Merge each player's copy of the games into one schedule per season (U8: R24, R25, KTD7).

Pure functions, no database access. The pieces:

- propose(parsed) aligns games and lists the conflicts. Its result is plain
  JSON and is what an import draft stores.
- resolve(conflict, data) checks one commissioner choice.
- acknowledge_missing() accepts "record the result only" for every missing
  score in one season.
- build(proposal, resolutions) applies the choices and returns the final
  schedule and stat lines. It raises NotReady until every conflict is resolved.

Alignment, per season (KTD7):
1. The most common opponent sequence across players is the reference.
2. Every other player's games are matched to it by opponent with a
   longest-common-subsequence, which keeps repeated opponents in order. Among
   equally long matches, the one that agrees on more outcomes wins.
3. Leftover games go to the one reference slot with the same opponent that
   this player has no game in yet (the outcome breaks ties): a "moved" conflict.
   A leftover that is the only one between two matched games, facing the only
   free slot there, is the same game with a different opponent: it goes there
   and the slot gets an "opponent" conflict. Anything else is "unplaced".
4. Each slot's score comes from the players' score text (see
   legacy.parse_score). Disagreeing or unreadable scores are a "score"
   conflict; a slot where nobody recorded a score is a "missing_score"
   conflict, preselected as "record the result only".
"""

from collections import Counter

from . import legacy

SCORE = "score"
MISSING_SCORE = "missing_score"
OPPONENT = "opponent"
MOVED = "moved"
UNPLACED = "unplaced"

MAX_SCORE = 999
MAX_OPPONENT_LENGTH = 100


class NotReady(Exception):
    """The proposal can't be saved yet. ``problems`` lists what's left to do."""

    def __init__(self, problems):
        self.problems = list(problems)
        super().__init__(" ".join(self.problems))


def _key(opponent):
    return opponent.strip().casefold()


def _week(index, opponent):
    return f"Week {index + 1} vs {opponent}"


def _ranked(counter):
    """Counter items, most common first, ties in first-seen order; and the top one only if it's a clear winner."""
    ranked = sorted(counter.items(), key=lambda item: -item[1])
    clear = ranked[0][0] if ranked and (len(ranked) == 1 or ranked[0][1] > ranked[1][1]) else None
    return ranked, clear


# --- Step 2: match one player's games to the reference ---------------------------------


def _match(reference, games):
    """Pairs (reference index, game index): longest match by opponent, then most outcomes agreeing."""
    n, m = len(reference), len(games)
    best = [[0] * (m + 1) for _ in range(n + 1)]

    def weight(i, j):
        if _key(reference[i]["opponent"]) != _key(games[j]["opponent"]):
            return None
        return 1000 + (1 if reference[i]["outcome"] == games[j]["outcome"] else 0)

    for i in range(n - 1, -1, -1):
        for j in range(m - 1, -1, -1):
            value = max(best[i + 1][j], best[i][j + 1])
            w = weight(i, j)
            if w is not None:
                value = max(value, best[i + 1][j + 1] + w)
            best[i][j] = value

    pairs, i, j = [], 0, 0
    while i < n and j < m:
        w = weight(i, j)
        if w is not None and best[i][j] == best[i + 1][j + 1] + w:
            pairs.append((i, j))
            i, j = i + 1, j + 1
        elif best[i + 1][j] >= best[i][j + 1]:
            i += 1
        else:
            j += 1
    return pairs


# --- Building the proposal ---------------------------------------------------------------


def _line(player, game):
    return {"player": player, "did_not_play": game["did_not_play"], "values": game["values"]}


def _who(players, indexes):
    return ", ".join(players[i]["name"] for i in indexes)


class _Season:
    def __init__(self, index, name, games_by_player, players):
        self.index = index
        self.name = name
        self.games_by_player = games_by_player
        self.players = players
        sequences = {p: tuple(_key(g["opponent"]) for g in games) for p, games in games_by_player.items()}
        counts = Counter(sequences.values())
        top = max(counts.values())
        self.reference_player = next(p for p in games_by_player if counts[sequences[p]] == top)
        self.reference = games_by_player[self.reference_player]
        # Per slot: (player, game) pairs whose stats sit there in the proposal.
        self.placed = [[(self.reference_player, game)] for game in self.reference]
        self.floating = []  # moved and unplaced games: (kind, player, game, proposed slot or None)

    def taken_by(self, player):
        taken = {i for i, slot in enumerate(self.placed) if any(p == player for p, _g in slot)}
        taken |= {slot for _k, p, _g, slot in self.floating if p == player and slot is not None}
        return taken

    def align(self):
        for player, games in self.games_by_player.items():
            if player != self.reference_player:
                self._align_player(player, games)

    def _align_player(self, player, games):
        pairs = _match(self.reference, games)
        matched = {j: i for i, j in pairs}
        for i, j in pairs:
            self.placed[i].append((player, games[j]))
        leftovers = [j for j in range(len(games)) if j not in matched]

        remaining = []
        for j in leftovers:
            game = games[j]
            taken = self.taken_by(player)
            same = [
                i for i, ref in enumerate(self.reference)
                if i not in taken and _key(ref["opponent"]) == _key(game["opponent"])
            ]
            if len(same) > 1:
                same = [i for i in same if self.reference[i]["outcome"] == game["outcome"]]
            if len(same) == 1:
                self.floating.append((MOVED, player, game, same[0]))
            else:
                remaining.append(j)

        for j in remaining:
            before = [jj for jj in matched if jj < j]
            after = [jj for jj in matched if jj > j]
            low_j, high_j = (max(before) if before else -1), (min(after) if after else len(games))
            low_i = matched[low_j] if before else -1
            high_i = matched[high_j] if after else len(self.reference)
            in_gap = [jj for jj in remaining if low_j < jj < high_j]
            taken = self.taken_by(player)
            free = [i for i in range(low_i + 1, high_i) if i not in taken]
            if len(in_gap) == 1 and len(free) == 1:
                self.placed[free[0]].append((player, games[j]))
            else:
                self.floating.append((UNPLACED, player, games[j], None))

    def slot_opponent(self, i):
        return self.reference[i]["opponent"]

    def entries_at(self, i):
        """Everything proposed for slot i, including moved games."""
        return self.placed[i] + [(p, g) for kind, p, g, slot in self.floating if kind == MOVED and slot == i]


class _Proposal:
    def __init__(self, players):
        self.players = players
        self.conflicts = []

    def add(self, kind, season, slot, summary, details, choices, suggested=None, custom=None, player=None):
        conflict = {
            "id": f"c{len(self.conflicts) + 1}",
            "type": kind,
            "season": season,
            "slot": slot,
            "player": player,
            "summary": summary,
            "details": details,
            "choices": choices,
            "suggested": suggested,
            "custom": custom,
        }
        self.conflicts.append(conflict)
        return conflict["id"]

    def opponent_conflict(self, season, i):
        names, players_by_key = {}, {}
        for player, game in season.entries_at(i):
            key = _key(game["opponent"])
            names.setdefault(key, game["opponent"])
            players_by_key.setdefault(key, []).append(player)
        if len(names) < 2:
            return None
        ranked, clear = _ranked(Counter({key: len(ps) for key, ps in players_by_key.items()}))
        choices = [{"value": str(n), "label": names[key], "opponent": names[key]} for n, (key, _c) in enumerate(ranked)]
        details = [f"{_who(self.players, players_by_key[key])}: {names[key]}" for key, _c in ranked]
        suggested = next((c["value"] for c, (key, _n) in zip(choices, ranked) if key == clear), None)
        return self.add(
            OPPONENT, season.index, i, f"{season.name}, week {i + 1}: players list different opponents",
            details, choices, suggested, custom="opponent",
        )

    def score(self, season, i):
        """Return (team score, opponent score, conflict id) for slot i."""
        readings = {}  # (status, team, opponent, raw) -> players
        outcomes = Counter()
        for player, game in season.entries_at(i):
            status, team, opponent = legacy.parse_score(game["score"], game["outcome"])
            raw = game["score"] if status == "malformed" else ""
            readings.setdefault((status, team, opponent, raw), []).append(player)
            if game["outcome"]:
                outcomes[game["outcome"]] += 1
        ok = Counter({(t, o): len(ps) for (s, t, o, _r), ps in readings.items() if s == "ok"})
        malformed = [(raw, ps) for (s, _t, _o, raw), ps in readings.items() if s == "malformed"]
        where = f"{season.name}, {_week(i, season.slot_opponent(i))}"

        if not malformed and len(ok) == 1:
            team, opponent = next(iter(ok))
            return team, opponent, None

        if not malformed and not ok:
            ranked, clear = _ranked(outcomes)
            seen = [outcome for outcome, _n in ranked] or ["W", "L", "T"]
            choices = [{"value": f"result:{r}", "label": f"Record the result only: {r}", "result": r} for r in seen]
            details = ["Nobody recorded a score."] + [
                f"Recorded outcome {r}: {n} player{'s' if n != 1 else ''}" for r, n in ranked
            ]
            suggested = f"result:{clear}" if clear else None
            conflict = self.add(MISSING_SCORE, season.index, i, f"{where}: no score", details, choices,
                                suggested, custom=SCORE)
            return None, None, conflict

        ranked, clear = _ranked(ok)
        choices = [
            {"value": str(n), "label": f"{t}-{o} ({_result({'team_score': t, 'opponent_score': o})})",
             "team_score": t, "opponent_score": o}
            for n, ((t, o), _c) in enumerate(ranked)
        ]
        details = []
        for (status, team, opponent, raw), ps in readings.items():
            if status == "ok":
                details.append(f"{_who(self.players, ps)}: {team}-{opponent}")
            elif status == "malformed":
                details.append(f"{_who(self.players, ps)}: {raw!r} can't be read as a score")
            else:
                details.append(f"{_who(self.players, ps)}: no score")
        suggested = next((c["value"] for c, (score, _n) in zip(choices, ranked) if score == clear), None)
        summary = f"{where}: {'unreadable score' if malformed else 'players disagree on the score'}"
        conflict = self.add(SCORE, season.index, i, summary, details, choices, suggested, custom=SCORE)
        return None, None, conflict

    def floating_conflict(self, season, kind, player, game, slot):
        name = self.players[player]["name"]
        free = sorted(set(range(len(season.reference))) - season.taken_by(player) | ({slot} if slot is not None else set()))
        choices = [
            {"value": f"slot:{i}", "label": f"Attach to {_week(i, season.slot_opponent(i))}", "slot": i}
            for i in free
        ]
        described = f"{game['opponent']} ({game['score'] or 'no score'}, {game['outcome'] or 'no outcome'})"
        if kind == MOVED:
            summary = f"{season.name}: {name} logged the {described} game out of order"
            details = [f"Proposed: {_week(slot, season.slot_opponent(slot))}"]
            return self.add(MOVED, season.index, slot, summary, details, choices, f"slot:{slot}", player=player)
        choices += [
            {"value": "new", "label": "Add it as a new game"},
            {"value": "discard", "label": f"Discard {name}'s line for this game"},
        ]
        summary = f"{season.name}: {name}'s {described} game doesn't match any game"
        return self.add(UNPLACED, season.index, None, summary, [], choices, player=player)


def propose(parsed):
    """Align every season and list the conflicts. Returns a JSON-able proposal."""
    players = [{key: value for key, value in p.items() if key != "seasons"} for p in parsed["players"]]
    proposal = _Proposal(players)

    names = []
    for p in parsed["players"]:
        for season in p["seasons"]:
            if season["name"] not in names:
                names.append(season["name"])

    seasons = []
    for index, name in enumerate(names):
        games_by_player = {}
        for n, p in enumerate(parsed["players"]):
            for season in p["seasons"]:
                if season["name"] == name and season["games"]:
                    games_by_player.setdefault(n, season["games"])
        if not games_by_player:
            seasons.append({"name": name, "team": "", "slots": [], "floating": []})
            continue
        season = _Season(index, name, games_by_player, players)
        season.align()

        slots = []
        for i, ref in enumerate(season.reference):
            opponent_conflict = proposal.opponent_conflict(season, i)
            team_score, opponent_score, score_conflict = proposal.score(season, i)
            playoff_votes = [g["is_playoff"] for _p, g in season.entries_at(i)]
            slots.append({
                "opponent": ref["opponent"],
                "team_score": team_score,
                "opponent_score": opponent_score,
                "is_playoff": sum(playoff_votes) * 2 > len(playoff_votes),
                "lines": [_line(p, g) for p, g in season.placed[i]],
                "opponent_conflict": opponent_conflict,
                "score_conflict": score_conflict,
            })
        floating = []
        for kind, player, game, slot in season.floating:
            conflict = proposal.floating_conflict(season, kind, player, game, slot)
            floating.append({
                "conflict": conflict,
                "type": kind,
                "line": _line(player, game),
                "opponent": game["opponent"],
                "score": game["score"],
                "outcome": game["outcome"],
                "is_playoff": game["is_playoff"],
            })
        teams = Counter(players[p]["team"] for p in games_by_player if players[p]["team"])
        seasons.append({
            "name": name,
            "team": teams.most_common(1)[0][0] if teams else "",
            "slots": slots,
            "floating": floating,
        })

    return {"players": players, "seasons": seasons, "conflicts": proposal.conflicts}


# --- Resolving -------------------------------------------------------------------------


def _number(value):
    text = str(value if value is not None else "").strip()
    if not text.isdigit() or int(text) > MAX_SCORE:
        raise ValueError("Scores must be whole numbers from 0 to 999.")
    return int(text)


def resolve(conflict, data):
    """Check one choice for ``conflict``. ``data`` has "choice" and, for typed values, the fields.

    Returns the resolution to store, or raises ValueError with a readable message.
    """
    choice = str(data.get("choice") or "").strip()
    if choice == "custom" and conflict["custom"] == SCORE:
        return {"choice": "custom", "team_score": _number(data.get("team_score")),
                "opponent_score": _number(data.get("opponent_score"))}
    if choice == "custom" and conflict["custom"] == OPPONENT:
        opponent = str(data.get("opponent") or "").strip()
        if not opponent or len(opponent) > MAX_OPPONENT_LENGTH:
            raise ValueError("Type the opponent's name (up to 100 characters).")
        return {"choice": "custom", "opponent": opponent}
    if choice in {c["value"] for c in conflict["choices"]}:
        return {"choice": choice}
    raise ValueError("Pick one of the options.")


def resolved_count(proposal, resolutions):
    return sum(1 for c in proposal["conflicts"] if c["id"] in resolutions)


def acknowledge_missing(proposal, resolutions, season):
    """Accept the suggested "record the result only" for every unresolved missing score in one season."""
    updated = dict(resolutions)
    for c in proposal["conflicts"]:
        if c["type"] == MISSING_SCORE and c["season"] == season and c["id"] not in updated and c["suggested"]:
            updated[c["id"]] = {"choice": c["suggested"]}
    return updated


def _chosen(conflict, resolution):
    if resolution["choice"] == "custom":
        return resolution
    return next(c for c in conflict["choices"] if c["value"] == resolution["choice"])


def _result(game):
    if game["team_score"] is not None and game["opponent_score"] is not None:
        if game["team_score"] == game["opponent_score"]:
            return "T"
        return "W" if game["team_score"] > game["opponent_score"] else "L"
    return game["recorded_result"]


def build(proposal, resolutions):
    """Apply every resolution. Returns {"players", "seasons": [{name, team, games: [...]}]}.

    Each game has order, opponent, team_score, opponent_score, recorded_result,
    is_playoff, result, and lines ({player, did_not_play, values}).
    Raises NotReady listing every unresolved or impossible choice.
    """
    problems, chosen = [], {}
    for conflict in proposal["conflicts"]:
        resolution = resolutions.get(conflict["id"])
        if resolution is None:
            problems.append(f"Not resolved yet: {conflict['summary']}.")
            continue
        try:
            chosen[conflict["id"]] = _chosen(conflict, resolve(conflict, resolution))
        except ValueError as error:
            problems.append(f"{conflict['summary']}: {error}")
    if problems:
        raise NotReady(problems)

    players = proposal["players"]
    seasons = []
    for season in proposal["seasons"]:
        games = []
        for i, slot in enumerate(season["slots"]):
            game = {
                "order": i + 1,
                "opponent": slot["opponent"],
                "team_score": slot["team_score"],
                "opponent_score": slot["opponent_score"],
                "recorded_result": "",
                "is_playoff": slot["is_playoff"],
                "lines": [dict(line) for line in slot["lines"]],
            }
            if slot["opponent_conflict"]:
                game["opponent"] = chosen[slot["opponent_conflict"]]["opponent"]
            if slot["score_conflict"]:
                pick = chosen[slot["score_conflict"]]
                if "result" in pick:
                    game["recorded_result"] = pick["result"]
                else:
                    game["team_score"], game["opponent_score"] = pick["team_score"], pick["opponent_score"]
            games.append(game)

        for entry in season["floating"]:
            pick = chosen[entry["conflict"]]
            line = dict(entry["line"])
            name = players[line["player"]]["name"]
            if pick["value"] == "discard":
                continue
            if pick["value"] == "new":
                status, team, opponent = legacy.parse_score(entry["score"], entry["outcome"])
                if status != "ok" and not entry["outcome"]:
                    problems.append(
                        f"{name}'s {entry['opponent']} game has no readable score or outcome, so it can't be "
                        "added as a new game. Attach it to a game or discard it."
                    )
                    continue
                games.append({
                    "order": len(games) + 1,
                    "opponent": entry["opponent"],
                    "team_score": team,
                    "opponent_score": opponent,
                    "recorded_result": "" if status == "ok" else entry["outcome"],
                    "is_playoff": entry["is_playoff"],
                    "lines": [line],
                })
                continue
            target = games[pick["slot"]]
            if any(existing["player"] == line["player"] for existing in target["lines"]):
                problems.append(
                    f"{name} already has a line in {season['name']}, {_week(pick['slot'], target['opponent'])}. "
                    "Pick a different game for their other line."
                )
                continue
            target["lines"].append(line)

        for game in games:
            game["result"] = _result(game)
        seasons.append({"name": season["name"], "team": season["team"], "games": games})

    if problems:
        raise NotReady(problems)
    return {"players": players, "seasons": seasons}
