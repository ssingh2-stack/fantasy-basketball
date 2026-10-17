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

## How projections work (`app/project.py`)

1. **Per-minute rates** for every stat. The last 3 seasons are weighted 1.0 / 0.6 / 0.4.
   Once games are played, the current season is blended in, and its weight grows with minutes played.
2. **Regression**: 300 minutes of league-average play are mixed into each player's rates,
   so small samples don't produce extreme projections.
3. **Aging curve**: players 22 and under improve about 5% a year, 31 and older decline.
   Minutes get an age adjustment too.
4. **Minutes per game** come from the weighted history. **Games played** come from each
   player's availability over the last 3 seasons, blended with the league norm (~85%).
5. **Manual overrides** go in `data/overrides.csv`. Use them for trades, new roles, injuries
   and rookies. Edit the file, then click **Rebuild projections**.

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
- **Owner filter / Hide taken**: see who's available. The ✓ column marks players taken in your draft
  before the league sync catches up.
- **Live draft**: re-syncs the league every 30 seconds.
- **Team category strength** shows every fantasy team's category totals and league rank, for spotting punts and trade targets.
- **+/-** is ADP minus our rank. Positive values mean the player goes later than we rank them (a value pick).

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
