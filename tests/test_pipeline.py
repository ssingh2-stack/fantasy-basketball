"""Tests run on synthetic fixtures (no network). Player stat lines below are made up."""
import csv
import json

import pytest

from app import project, scrape
from app.leagues import espn, yahoo
from app.names import fix_mojibake, norm_name, norm_team
from app.server import merge

BBREF_HTML = """
<table id="totals_stats"><tbody>
<tr><th data-stat="ranker">1</th>
  <td data-stat="name_display" data-append-csv="tradeda01"><a>Traded Guy*</a></td>
  <td data-stat="age">27</td><td data-stat="team_name_abbr">2TM</td><td data-stat="pos">SF</td>
  <td data-stat="games">70</td><td data-stat="games_started">70</td><td data-stat="mp">2400</td>
  <td data-stat="fg">500</td><td data-stat="fga">1000</td><td data-stat="fg3">150</td><td data-stat="fg3a">400</td>
  <td data-stat="ft">200</td><td data-stat="fta">250</td><td data-stat="orb">60</td><td data-stat="drb">300</td>
  <td data-stat="trb">360</td><td data-stat="ast">250</td><td data-stat="stl">70</td><td data-stat="blk">30</td>
  <td data-stat="tov">120</td><td data-stat="pts">1350</td></tr>
<tr><td data-stat="name_display" data-append-csv="tradeda01">Traded Guy</td><td data-stat="team_name_abbr">CHO</td><td data-stat="mp">1000</td></tr>
<tr><td data-stat="name_display" data-append-csv="tradeda01">Traded Guy</td><td data-stat="team_name_abbr">BRK</td><td data-stat="mp">1400</td></tr>
<tr class="thead"><th data-stat="ranker">Rk</th></tr>
<tr><td data-stat="name_display">League Average</td></tr>
</tbody></table>
"""


def test_parse_totals_combines_traded_players():
    rows = scrape.parse_totals(BBREF_HTML)
    assert len(rows) == 1
    r = rows[0]
    assert r["name"] == "Traded Guy"
    assert r["team"] == "BKN"  # last team, normalized from BRK
    assert r["min"] == "2400" and r["pts"] == "1350"


def test_fix_mojibake():
    assert fix_mojibake("Nikola Joki\u00c4\u0087") == "Nikola Jokić"
    assert fix_mojibake("Luka Don\u00c4\u008di\u00c4\u0087") == "Luka Dončić"
    assert fix_mojibake("Nikola Jokić") == "Nikola Jokić"  # already correct: unchanged
    assert fix_mojibake("LeBron James") == "LeBron James"


def test_name_and_team_normalization():
    assert norm_name("Nikola Jokić") == "nikola jokic"
    assert norm_name("Jaren Jackson Jr.") == "jaren jackson"
    assert norm_name("De'Aaron Fox") == "deaaron fox"
    assert norm_name("Nicolas Claxton") == norm_name("Nic Claxton")
    assert norm_team("PHO") == norm_team("PHX") == "PHX"
    assert norm_team("GS") == "GSW"


def line(pid, name, team, age, g, mp, scale=1.0, pos="PG"):
    """A season line where every per-minute rate is `scale` x a baseline."""
    per_min = {"fgm": 0.2, "fga": 0.42, "fg3m": 0.06, "fg3a": 0.17, "ftm": 0.1, "fta": 0.12,
               "oreb": 0.03, "dreb": 0.14, "treb": 0.17, "ast": 0.12, "stl": 0.03, "blk": 0.02, "tov": 0.05}
    row = {"bbref_id": pid, "name": name, "team": team, "pos": pos, "age": age, "g": g, "gs": g, "min": mp}
    for k, v in per_min.items():
        row[k] = round(v * mp * scale, 1)
    row["pts"] = round(2 * row["fgm"] + row["fg3m"] + row["ftm"], 1)
    return row


def write_season(raw, season, rows):
    with (raw / f"totals_{season}.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=scrape.COLUMNS)
        w.writeheader()
        w.writerows(rows)


@pytest.fixture
def raw(tmp_path, monkeypatch):
    raw = tmp_path / "raw"
    raw.mkdir()
    monkeypatch.setattr(project, "RAW_DIR", raw)
    monkeypatch.setattr(scrape, "RAW_DIR", raw)  # rosters are read through scrape
    monkeypatch.setattr(project, "OVERRIDES_PATH", tmp_path / "overrides.csv")
    filler = [line(f"fill{i}", f"Filler {i}", "ATL", 26, 70, 2000) for i in range(20)]
    write_season(raw, 2026, filler + [
        line("star01", "Star Player", "DEN", 29, 72, 2500, scale=1.6),
        line("young01", "Young Riser", "OKC", 21, 75, 1800, scale=1.1),
        line("cameo01", "Cameo Guy", "MIA", 26, 5, 40, scale=3.0),
        line("healthy01", "Healthy Twin", "MIL", 35, 70, 2500, scale=1.6),
        line("hurt01", "Often Hurt", "LAL", 30, 30, 1000, scale=1.4),
    ])
    write_season(raw, 2025, filler + [
        line("star01", "Star Player", "DEN", 28, 74, 2600, scale=1.5),
        line("hurt01", "Often Hurt", "LAL", 29, 40, 1300, scale=1.4),
        line("achilles01", "Achilles Vet", "MIL", 34, 70, 2500, scale=1.6),
        line("healthy01", "Healthy Twin", "MIL", 34, 70, 2500, scale=1.6),
    ])
    write_season(raw, 2024, filler + [
        line("gone01", "Retired Vet", "BOS", 38, 60, 1200),
        line("achilles01", "Achilles Vet", "MIL", 33, 73, 2600, scale=1.6),
    ])
    return tmp_path


def cfg():
    return {"season": 2027, "history": [2026, 2025, 2024], "history_weights": [1.0, 0.6, 0.4],
            "include_current_season": True, "season_games": 82, "games_remaining": 82}


def by_name(players):
    return {p["name"]: p for p in players}


def test_projection_basics(raw):
    ps = by_name(project.build_projections(cfg()))
    star, filler = ps["Star Player"], ps["Filler 0"]
    assert star["pts"] > filler["pts"] * 1.3
    assert 30 < star["mpg"] < 37
    assert star["age"] == 30
    assert star["pts"] == pytest.approx(2 * star["fgm"] + star["fg3m"] + star["ftm"], rel=1e-3)
    assert star["treb"] == pytest.approx(star["oreb"] + star["dreb"], rel=1e-3)
    assert "Retired Vet" not in ps  # hasn't played in the last two seasons


def test_small_samples_regress_to_league_average(raw):
    ps = by_name(project.build_projections(cfg()))
    cameo, filler = ps["Cameo Guy"], ps["Filler 0"]
    per_min = lambda p: p["pts"] / p["mpg"]
    # 40 minutes at 3x production should be pulled most of the way back to average.
    assert per_min(cameo) < per_min(filler) * 1.4


def test_injury_history_lowers_games(raw):
    ps = by_name(project.build_projections(cfg()))
    assert ps["Often Hurt"]["gp"] < ps["Star Player"]["gp"] - 15
    assert ps["Star Player"]["gp"] <= 82


def test_missed_season_counts_against_player(raw):
    """A star who sat out all of 2025-26 (like Lillard after his Achilles tear) should project
    fewer games and more aging than an identical player who stayed healthy."""
    ps = by_name(project.build_projections(cfg()))
    hurt, healthy = ps["Achilles Vet"], ps["Healthy Twin"]
    assert hurt["missed_seasons"] == [2026] and healthy["missed_seasons"] == []
    assert hurt["age"] == 36 and healthy["age"] == 36
    assert hurt["gp"] < 55 < healthy["gp"]
    assert hurt["pts"] < healthy["pts"]  # two years of decline vs one, applied to the older sample
    assert hurt["mpg"] < healthy["mpg"]


def test_young_players_improve(raw):
    ps = by_name(project.build_projections(cfg()))
    young = ps["Young Riser"]
    assert young["mpg"] > 1800 / 75  # gets a minutes bump at 22


def test_overrides(raw):
    (raw / "overrides.csv").write_text(
        "# comment line\n"
        "player,team,pos,mpg,usage_mult,gp,games_out,fgm,fga,fg3m,fg3a,ftm,fta,oreb,dreb,ast,stl,blk,tov,pts,note\n"
        "Star Player,LAL,PG/SG,,1.1,,20,,,,,,,,,,,,,,Traded; out until December\n"
        "Brand New Rookie,WAS,SF,28,,,,7,15,1.5,4.5,3,4,1.5,4,2.5,1,0.6,2.5,18.5,Rookie\n"
    )
    ps = by_name(project.build_projections(cfg()))
    star = ps["Star Player"]
    assert star["team"] == "LAL" and star["pos"] == "PG,SG"
    assert star["note"].startswith("Traded")
    rookie = ps["Brand New Rookie"]
    assert rookie["pts"] == 18.5 and rookie["mpg"] == 28 and rookie["rookie"]
    assert rookie["fg_pct"] == pytest.approx(7 / 15, abs=1e-3)


def test_override_scaling(raw):
    plain = by_name(project.build_projections(cfg()))["Star Player"]
    (raw / "overrides.csv").write_text(
        "player,team,pos,mpg,usage_mult,gp,games_out,fgm,fga,fg3m,fg3a,ftm,fta,oreb,dreb,ast,stl,blk,tov,pts,note\n"
        "Star Player,,,,1.1,,20,,,,,,,,,,,,,,\n"
        "Young Riser,,,30,,,,,,,,,,,,,,,,,\n"
    )
    ps = by_name(project.build_projections(cfg()))
    assert ps["Star Player"]["pts"] == pytest.approx(plain["pts"] * 1.1, rel=1e-3)
    assert ps["Star Player"]["gp"] == plain["gp"] - 20
    assert ps["Young Riser"]["mpg"] == 30


def test_current_season_blends_in(raw):
    before = by_name(project.build_projections(cfg()))["Star Player"]["pts"]
    write_season(raw / "raw", 2027, [line("star01", "Star Player", "DEN", 30, 20, 700, scale=2.2)])
    after = by_name(project.build_projections(cfg()))["Star Player"]["pts"]
    assert after > before


# ---------- league connectors ----------

class FakeResp:
    def __init__(self, payload, status=200):
        self.payload, self.status_code = payload, status

    def json(self):
        return self.payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


def test_espn_sync(monkeypatch):
    league = {
        "settings": {"name": "Hoops League"},
        "teams": [{"id": 1, "name": "Team A", "owners": ["{ABC}"]}, {"id": 2, "location": "Team", "nickname": "B", "owners": ["{XYZ}"]}],
        "draftDetail": {"inProgress": True, "picks": [{"overallPickNumber": 1, "teamId": 1, "playerId": 10}, {"overallPickNumber": 2, "teamId": 2, "playerId": -1}]},
    }
    pool = {"players": [
        {"id": 10, "onTeamId": 1, "player": {"fullName": "Nikola Jokić", "proTeamId": 7, "eligibleSlots": [4, 9, 10, 11, 12],
                                             "ownership": {"averageDraftPosition": 1.5}, "injuryStatus": "ACTIVE"}},
        {"id": 11, "onTeamId": 0, "player": {"fullName": "Free Agent", "proTeamId": 21, "eligibleSlots": [0, 1, 5],
                                             "ownership": {"averageDraftPosition": 120.2}, "injuryStatus": "OUT"}},
    ]}

    def fake_get(self, url, params=None, headers=None, timeout=None):
        return FakeResp(pool if params == {"view": "kona_player_info"} else league)

    monkeypatch.setattr("requests.Session.get", fake_get)
    out = espn.sync({"season": 2027, "league": {"league_id": "123", "espn_s2": "x", "swid": "{abc}"}})
    assert out["league_name"] == "Hoops League"
    assert out["teams"] == [{"id": "1", "name": "Team A", "mine": True}, {"id": "2", "name": "Team B", "mine": False}]
    jokic, fa = out["players"]
    assert (jokic["key"], jokic["team"], jokic["pos"], jokic["owner"], jokic["adp"], jokic["injury"]) == \
        ("nikola jokic", "DEN", "C", "1", 1.5, None)
    assert (fa["owner"], fa["pos"], fa["injury"], fa["team"]) == (None, "PG,SG", "OUT", "PHX")
    assert out["draft"] == {"in_progress": True, "picks": [{"pick": 1, "team": "1", "key": "nikola jokic"}]}


def yahoo_player(key, name, abbr, positions, extra=None):
    meta = [{"player_key": key}, {"player_id": key.split(".")[-1]}, {"name": {"full": name}}, [],
            {"editorial_team_abbr": abbr},
            {"eligible_positions": [{"position": p} for p in positions]}]
    return {"player": [meta] + ([extra] if extra else [])}


def test_yahoo_sync(monkeypatch):
    roster = {"fantasy_content": {"league": [
        [{"league_key": "466.l.99"}, {"name": "Yahoo Hoops"}, {"draft_status": "postdraft"}],
        {"teams": {"0": {"team": [
            [{"team_key": "466.l.99.t.1"}, {"team_id": "1"}, {"name": "My Squad"}, [], {"is_owned_by_current_login": 1}],
            {"roster": {"coverage_type": "date", "0": {"players": {
                "0": yahoo_player("466.p.1", "Victor Wembanyama", "SA", ["C", "PF", "F", "Util"]),
                "count": 1}}}}]},
            "1": {"team": [[{"team_key": "466.l.99.t.2"}, {"team_id": "2"}, {"name": "Rival"}], {"roster": {"0": {"players": {"count": 0}}}}]},
            "count": 2}},
    ]}}
    adp_page = {"fantasy_content": {"league": [{}, {"players": {
        "0": yahoo_player("466.p.1", "Victor Wembanyama", "SA", ["C"], {"draft_analysis": [{"average_pick": "1.9"}]}),
        "1": yahoo_player("466.p.2", "Some Guard", "GS", ["PG", "G"], {"draft_analysis": [{"average_pick": "-"}]}),
        "count": 2}}]}}
    draft = {"fantasy_content": {"league": [{}, {"draft_results": {
        "0": {"draft_result": {"pick": 1, "team_key": "466.l.99.t.1", "player_key": "466.p.1"}}, "count": 1}}]}}

    def fake_get(cfg, path):
        if path == "game/nba":
            return {"game": [{"game_key": "466"}]}
        if path.endswith("/teams/roster"):
            return roster["fantasy_content"]
        if "players;sort=AR;start=0;" in path:
            return adp_page["fantasy_content"]
        if "players;sort=AR" in path:
            return {"league": [{}, {"players": []}]}
        if path.endswith("/draftresults"):
            return draft["fantasy_content"]
        raise AssertionError(path)

    monkeypatch.setattr(yahoo, "_get", fake_get)
    out = yahoo.sync({"yahoo": {"league_id": "99"}})
    assert out["league_name"] == "Yahoo Hoops"
    assert out["teams"] == [{"id": "1", "name": "My Squad", "mine": True}, {"id": "2", "name": "Rival", "mine": False}]
    players = {p["key"]: p for p in out["players"]}
    wemby = players["victor wembanyama"]
    assert (wemby["team"], wemby["pos"], wemby["owner"], wemby["adp"]) == ("SAS", "PF,C", "1", 1.9)
    guard = players["some guard"]
    assert (guard["team"], guard["owner"], guard["adp"]) == ("GSW", None, None)
    assert out["draft"]["picks"] == [{"pick": 1, "team": "1", "key": "victor wembanyama"}]


def test_merge_attaches_league_data():
    projections = [
        {"key": "a", "name": "A", "team": "BOS", "pos": "SF"},
        {"key": "b", "name": "B", "team": "MIA", "pos": "C", "override": True},
    ]
    league = {"platform": "espn", "league_name": "L", "synced_at": "now", "teams": [], "draft": {},
              "players": [
                  {"key": "a", "name": "A", "team": "LAL", "pos": "SF,PF", "owner": "3", "adp": 12.0, "injury": None},
                  {"key": "b", "name": "B", "team": "NYK", "pos": "C", "owner": None, "adp": None, "injury": "OUT"},
                  {"key": "rook", "name": "Rookie", "team": "WAS", "pos": "SF", "owner": None, "adp": 40.0},
              ]}
    out = merge(json.loads(json.dumps(projections)), league)
    a, b = out["players"]
    assert (a["team"], a["prev_team"], a["pos"], a["owner"], a["adp"]) == ("LAL", "BOS", "SF,PF", "3", 12.0)
    assert b["team"] == "MIA" and "prev_team" not in b  # manual override wins
    assert out["unmatched"] == [{"name": "Rookie", "team": "WAS", "adp": 40.0}]


# ---------- per-stat regression, rosters, minutes budget ----------

def test_hot_three_point_season_on_low_volume_is_tempered(raw):
    """A 50% 3-point season on 40 attempts should project well below 50%; FT% regresses less."""
    hot = line("hot01", "Hot Shooter", "ORL", 25, 60, 1200)
    hot.update(fg3m=20, fg3a=40, ftm=90, fta=100)
    hot["pts"] = round(2 * hot["fgm"] + hot["fg3m"] + hot["ftm"], 1)
    rows = list(csv.DictReader((raw / "raw" / "totals_2026.csv").open()))
    write_season(raw / "raw", 2026, rows + [hot])
    p = by_name(project.build_projections(cfg()))["Hot Shooter"]
    assert 0.36 < p["fg3_pct"] < 0.42
    assert p["ft_pct"] > 0.82  # 90% on 100 FTs stays clearly above average
    assert p["pts"] == pytest.approx(2 * p["fgm"] + p["fg3m"] + p["ftm"], rel=1e-3)


ROSTER_HTML = """<table id="roster"><tbody>
<tr><td data-stat="number">1</td><td data-stat="player" data-append-csv="star01"><a>Star Player</a></td></tr>
<tr><td data-stat="player" data-append-csv="rook01"><a>Rookie Guy</a> (TW)</td></tr>
</tbody></table>"""


def test_parse_roster():
    assert scrape.parse_roster(ROSTER_HTML) == [
        {"bbref_id": "star01", "name": "Star Player"}, {"bbref_id": "rook01", "name": "Rookie Guy"}]
    assert scrape.parse_roster("<html><body>no roster</body></html>") == []


def write_rosters(raw, assignments):
    teams = ["ATL", "BOS", "BKN", "CHA", "CHI", "CLE", "DAL", "DEN", "DET", "GSW", "HOU", "IND", "LAC", "LAL",
             "MEM", "MIA", "MIL", "MIN", "NOP", "NYK", "OKC", "ORL", "PHI", "PHX", "POR", "SAC", "SAS", "TOR",
             "UTA", "WAS"]
    rows = [{"bbref_id": f"filler-{t}", "name": f"Filler {t}", "team": t} for t in teams]
    rows += [{"bbref_id": pid, "name": pid, "team": team} for pid, team in assignments.items()]
    with (raw / "raw" / "rosters_2027.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["bbref_id", "name", "team"])
        w.writeheader()
        w.writerows(rows)


def test_rosters_move_players_and_drop_free_agents(raw):
    on_team = {f"fill{i}": "ATL" for i in range(20)}
    write_rosters(raw, {**on_team, "star01": "MIA", "young01": "OKC", "hurt01": "LAL"})
    ps = by_name(project.build_projections(cfg()))
    assert ps["Star Player"]["team"] == "MIA" and ps["Star Player"]["prev_team"] == "DEN"
    assert "prev_team" not in ps["Young Riser"]
    assert "Cameo Guy" not in ps  # not on any roster -> unsigned


def test_partial_rosters_are_ignored(raw):
    with (raw / "raw" / "rosters_2027.csv").open("w", newline="") as f:
        f.write("bbref_id,name,team\nstar01,Star Player,MIA\n")
    ps = by_name(project.build_projections(cfg()))
    assert ps["Star Player"]["team"] == "DEN" and "Cameo Guy" in ps


def test_team_minutes_budget_squeezes_bench_most():
    def player(name, mpg, locked=False):
        p = {c: mpg / 2 for c in project.COUNTING}
        p.update(name=name, team="BOS", mpg=mpg, gp=82, mpg_locked=locked)
        return p
    roster = [player("Star", 36), player("Starter", 32), player("Starter2", 30), player("Starter3", 30),
              player("Starter4", 28), player("Bench", 22), player("Bench2", 20), player("Bench3", 18),
              player("Deep", 14), player("Signed Star", 34, locked=True)]  # 264 minutes
    before = {p["name"]: p["mpg"] for p in roster}
    project.apply_team_minutes(roster, 82)
    after = {p["name"]: p["mpg"] for p in roster}
    assert sum(after.values()) == pytest.approx(240, abs=0.5)
    assert after["Signed Star"] == 34  # hand-set minutes are untouched
    assert before["Star"] - after["Star"] < before["Deep"] - after["Deep"]
    star = next(p for p in roster if p["name"] == "Star")
    assert star["pts"] == pytest.approx(after["Star"] / 2)  # stats scale with minutes


def test_team_under_budget_is_untouched():
    roster = [{**{c: 1.0 for c in project.COUNTING}, "name": "A", "team": "BOS", "mpg": 30, "gp": 82}]
    project.apply_team_minutes(roster, 82)
    assert roster[0]["mpg"] == 30


def test_override_for_unknown_player_without_stats_is_ignored(raw, capsys):
    (raw / "overrides.csv").write_text(
        "player,team,pos,mpg,usage_mult,gp,games_out,fgm,fga,fg3m,fg3a,ftm,fta,oreb,dreb,ast,stl,blk,tov,pts,note\n"
        "Misspelled Playr,LAL,,,1.1,,,,,,,,,,,,,,,,typo\n"
    )
    ps = by_name(project.build_projections(cfg()))
    assert "Misspelled Playr" not in ps
    assert "unknown player 'Misspelled Playr'" in capsys.readouterr().out


def test_parse_schedule_keeps_regular_season_games_only():
    payload = {"leagueSchedule": {"seasonYear": "2026-27", "gameDates": [
        {"gameDate": "10/10/2026 00:00:00", "games": [
            {"gameId": "0012600001", "gameDateEst": "2026-10-10T00:00:00Z",
             "homeTeam": {"teamTricode": "BOS"}, "awayTeam": {"teamTricode": "NYK"}}]},
        {"gameDate": "10/20/2026 00:00:00", "games": [
            {"gameId": "0022600001", "gameDateEst": "2026-10-20T00:00:00Z",
             "homeTeam": {"teamTricode": "DET"}, "awayTeam": {"teamTricode": "BOS"}},
            {"gameId": "0022600002", "gameDateEst": "2026-10-20T00:00:00Z",
             "homeTeam": {"teamTricode": "NYK"}, "awayTeam": {"teamTricode": "PHI"}}]},
        {"gameDate": "12/09/2026 00:00:00", "games": [  # NBA Cup placeholder, teams not set yet
            {"gameId": "0022600999", "gameDateEst": "2026-12-09T00:00:00Z",
             "homeTeam": {"teamTricode": ""}, "awayTeam": {"teamTricode": ""}}]},
    ]}}
    assert scrape.parse_schedule(payload) == {
        "DET": ["2026-10-20"], "BOS": ["2026-10-20"], "NYK": ["2026-10-20"], "PHI": ["2026-10-20"]}


def test_late_season_risk():
    tank = {"SAC"}
    vet = project.late_season_risk({"team": "SAC", "age": 31, "gp": 70}, tank, 82)
    kid = project.late_season_risk({"team": "SAC", "age": 21, "gp": 70}, tank, 82)
    old = project.late_season_risk({"team": "BOS", "age": 36, "gp": 50}, tank, 82)
    fine = project.late_season_risk({"team": "BOS", "age": 27, "gp": 75}, tank, 82)
    assert vet["factor"] == 0.8 and kid["factor"] == 1.05
    assert old["factor"] == round(0.85 * 0.9, 2) and len(old["reasons"]) == 2
    assert fine == {"factor": 1.0, "reasons": []}
