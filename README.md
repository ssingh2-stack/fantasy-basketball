# Fantasy Hoops

NBA fantasy projections and category rankings (in the style of Hashtag Basketball and
Basketball Monster), connected to your ESPN or Yahoo league so you can see who's taken.

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp config.example.json config.json        # then fill in your league (see below)

python -m app.cli fetch      # download the last 3 seasons from Basketball-Reference
python -m app.cli project    # build data/projections.json
python -m app.cli serve      # open http://localhost:8000
```

Then click **Sync league**. Seasons are named by the year they end: `2027` = 2026-27.

## Deploying (Railway)

`railway.json` sets the start command (`python -m app.cli serve --host 0.0.0.0`, which listens on
`$PORT`). Railway starts with an empty disk, so on boot the server downloads stats and builds
projections in the background. The page says "Building projections…" for about a minute. Stats
then refresh every 24 hours (`REFRESH_HOURS` env var, `0` to disable).

League sync is optional. Its buttons stay hidden until a league is configured. On Railway, put
settings in a `FANTASY_CONFIG` env var as JSON (same shape as `config.json`), e.g.
`{"league": {"platform": "espn", "league_id": "12345"}}`.

If Basketball-Reference blocks Railway's servers (check the deploy logs for `failed to fetch`),
run `python -m app.cli fetch` on your own computer and commit the files in `data/raw/`.
The server only re-downloads the in-progress season, so it will use the committed history.

## How projections work (`app/project.py`)

1. **Per-minute rates** for volume stats (shot attempts, rebounds, assists, steals, ...). The last
   3 seasons are weighted 1.0 / 0.6 / 0.4. Once games are played, the current season is blended in,
   and its weight grows with minutes played.
2. **Per-stat regression**: each rate is pulled toward league average by its own amount, so
   noisy stats like steals are pulled harder than stable ones like rebounds. 2P%, 3P% and FT%
   are regressed on attempts, so a hot 3-point season on low volume is tempered.
3. **Aging curve**: players 22 and under improve about 5% a year, 31 and older decline. Minutes
   get an age adjustment too, applied for every year since the player last played.
4. **Minutes per game** come from the weighted history. **Games played** come from each player's
   availability over the last 3 seasons, blended with the league norm (~85%). A fully missed
   season (e.g. an Achilles tear) counts as zero games.
5. **Current rosters**: every team's roster is pulled from Basketball-Reference, so offseason
   trades and signings move players automatically (NEW badge) and unsigned free agents drop out.
6. **Manual overrides** in `data/overrides.csv` cover new roles (usage changes), injuries and
   rookies. They're seeded with researched 2026 offseason moves, opening-week injuries and the top
   rookies. Edit the file and push (or click **Rebuild projections** locally). Rows for player names
   that don't match are skipped with a warning in the logs.
7. **Team minutes budget**: each team's projected minutes are fit into the 240 available per game.
   Excess minutes come mostly from the bench, so adding a star squeezes the reserves more than the
   other starters.

When your league is synced, its data on current NBA teams, eligible positions, ADP and injury
status is merged in. Players who changed teams get a **NEW** badge. Rostered rookies with no
projection are listed at the top of the page so you can add them to the overrides.

## Rankings (`static/rank.js`)

Each player gets a z-score per category, measured against the draftable pool (league size ×
roster spots). FG%, FT% and 3P% use *impact*: (player% − pool%) × attempts. TO counts
negatively. The total is the sum of z × multiplier. Uncheck a category to punt it. Rankings
update instantly in the browser.

- **Averages vs Totals**: totals multiply by projected games played.
- **GP penalty** (averages mode): reduces a player's value in proportion to games they're projected to miss.
- **Punt presets**: one click for common builds (punt FT%, punt FG%, bigs, guards, ...).
- **Tiers**: players are grouped where value drops off sharply, so you can see when a run is ending.
- **Draft marks**: ✓ marks a player taken by someone else, and ★ marks your own pick. Marks are
  saved in your browser, so this works without connecting a league.
- **Fit / Best for my team**: once you have ★ picks, the Fit column re-scores every player by how much
  they help your weakest (non-punted) categories. **Best for my team** sorts available players by Fit.
- **Owner filter / Hide taken**: see who's available.
- **Live draft**: re-syncs the league every 30 seconds.
- **Team category strength** shows every fantasy team's category totals and league rank, for spotting punts and trade targets.
- **+/-** is ADP minus our rank. Positive values mean the player goes later than we rank them (a value pick).

## Built for H2H categories

- **Use Averages + GP penalty** (the default). Each week is its own matchup, so per-game impact
  matters most. Missed games still cost you, but you can stream a replacement, which is what the
  20% penalty reflects. Totals mode is there for roto leagues.
- **Fantasy playoffs**: the NBA schedule is downloaded on startup. Pick your league's playoff weeks
  (default: the final 3 Mon-Sun weeks, Mar 22 - Apr 11, 2027). The **PO G** column shows each player's
  team games in that window. **Playoff weight** (Light by default) shifts value toward players
  who'll actually play then.
- **Late-season risk** (`PO RISK` / `PO BOOST` badges, hover for reasons): veterans on likely lottery
  teams (`tank_teams` in config) often get shut down by March, players 35+ rest more, and
  injury-prone players miss more. Young players on lottery teams tend to get more late minutes.
  These feed into the playoff weight.

## Connecting your league

**ESPN** (`"platform": "espn"`): set `league_id`, the number in your league URL. For private
leagues, also copy the `espn_s2` and `SWID` cookies from fantasy.espn.com (browser dev tools →
Application → Cookies). The SWID also lets the app highlight your own team.

**Yahoo** (`"platform": "yahoo"`): create an app at https://developer.yahoo.com/apps/create/
with *Fantasy Sports → Read* permission and redirect URI `oob`. Put the client ID and secret,
plus your `league_id`, in `yahoo`. Click **Connect Yahoo** in the app, approve access, and paste
the code. The token refreshes automatically.

## Tests

```bash
pytest -q
node --test tests/*.mjs
```
