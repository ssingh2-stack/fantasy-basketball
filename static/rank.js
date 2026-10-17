// Category z-score rankings, in the style of Basketball Monster / Hashtag Basketball.
//
// For each enabled category we compute every player's value, then a z-score
// against a "draftable pool" (league size x roster spots). Percentage categories
// use impact = (player% - pool%) x attempts, so a 60% shooter on 3 attempts
// matters less than a 55% shooter on 18. The pool is re-chosen from the
// rankings a few times so the averages reflect players who'd actually be rostered.

export const CATS = {
  fgm:     { label: 'FGM', type: 'count', stat: 'fgm' },
  fg_pct:  { label: 'FG%', type: 'pct', made: 'fgm', att: 'fga' },
  ftm:     { label: 'FTM', type: 'count', stat: 'ftm' },
  ft_pct:  { label: 'FT%', type: 'pct', made: 'ftm', att: 'fta' },
  fg3m:    { label: '3PM', type: 'count', stat: 'fg3m' },
  fg3_pct: { label: '3P%', type: 'pct', made: 'fg3m', att: 'fg3a' },
  pts:     { label: 'PTS', type: 'count', stat: 'pts' },
  ato:     { label: 'A/TO', type: 'ratio', num: 'ast', den: 'tov' },
  dreb:    { label: 'DREB', type: 'count', stat: 'dreb' },
  oreb:    { label: 'OREB', type: 'count', stat: 'oreb' },
  treb:    { label: 'TREB', type: 'count', stat: 'treb' },
  ast:     { label: 'AST', type: 'count', stat: 'ast' },
  stl:     { label: 'STL', type: 'count', stat: 'stl' },
  blk:     { label: 'BLK', type: 'count', stat: 'blk' },
  tov:     { label: 'TO', type: 'count', stat: 'tov', negative: true },
};

// Standard 9-category league.
export const DEFAULT_CATS = { fg_pct: 1, ft_pct: 1, fg3m: 1, pts: 1, treb: 1, ast: 1, stl: 1, blk: 1, tov: 1 };

function poolRates(pool) {
  const sum = (k) => pool.reduce((s, p) => s + (p[k] || 0), 0);
  const rates = {};
  for (const [key, c] of Object.entries(CATS)) {
    if (c.type === 'pct') rates[key] = sum(c.made) / (sum(c.att) || 1);
    if (c.type === 'ratio') rates[key] = sum(c.num) / (sum(c.den) || 1);
  }
  return rates;
}

// Raw (pre-z) value of a category for one player, per game.
export function rawValue(p, key, rates) {
  const c = CATS[key];
  if (c.type === 'count') return p[c.stat] || 0;
  if (c.type === 'pct') return (p[c.made] || 0) - rates[key] * (p[c.att] || 0);
  return (p[c.num] || 0) - rates[key] * (p[c.den] || 0);
}

function meanSd(values) {
  const n = values.length || 1;
  const mean = values.reduce((a, b) => a + b, 0) / n;
  const sd = Math.sqrt(values.reduce((a, b) => a + (b - mean) ** 2, 0) / n) || 1;
  return { mean, sd };
}

/**
 * @param players  projections: per-game stats plus gp
 * @param opts.cats        { catKey: multiplier } for enabled categories
 * @param opts.mode        'avg' (per game) or 'tot' (per game x games played)
 * @param opts.poolSize    number of rostered players in the league (teams x roster spots)
 * @param opts.gpPenalty   0..1, in avg mode shrinks value for players projected to miss games
 * @param opts.seasonGames games left in the season (for the GP penalty)
 * @returns new player objects with z (per-category z-scores), value, and rank
 */
export function rankPlayers(players, opts) {
  const { cats, mode = 'avg', poolSize = 156, gpPenalty = 0, seasonGames = 82 } = opts;
  const keys = Object.keys(cats).filter((k) => cats[k] && CATS[k]);
  const scale = (p) => (mode === 'tot' ? p.gp || 0 : 1);

  let pool = [...players].sort((a, b) => b.mpg * b.gp - a.mpg * a.gp).slice(0, poolSize);
  let ranked;
  for (let iter = 0; iter < 4; iter++) {
    const rates = poolRates(pool);
    const stats = {};
    for (const k of keys) stats[k] = meanSd(pool.map((p) => rawValue(p, k, rates) * scale(p)));

    ranked = players.map((p) => {
      const z = {};
      let value = 0;
      for (const k of keys) {
        const zk = (rawValue(p, k, rates) * scale(p) - stats[k].mean) / stats[k].sd;
        z[k] = CATS[k].negative ? -zk : zk;
        value += z[k] * cats[k];
      }
      if (mode === 'avg' && gpPenalty > 0) {
        const missed = Math.max(0, 1 - (p.gp || 0) / seasonGames);
        value -= gpPenalty * missed * Math.abs(value);
      }
      return { ...p, z, value };
    });
    ranked.sort((a, b) => b.value - a.value);
    pool = ranked.slice(0, poolSize);
  }
  ranked.forEach((p, i) => { p.rank = i + 1; });
  return ranked;
}

// Color bucket for a category z-score, matching the Elite..Not Good legend.
export function tier(z) {
  if (z >= 2) return 'elite';
  if (z >= 1.25) return 'vgood';
  if (z >= 0.5) return 'good';
  if (z > -0.5) return 'avg';
  if (z > -1.25) return 'below';
  return 'bad';
}

// One-click category setups. Each lists the categories turned off relative to a 9-cat league.
export const PRESETS = {
  'Standard 9-cat': [],
  'Punt FT%': ['ft_pct'],
  'Punt FG%': ['fg_pct'],
  'Punt TO': ['tov'],
  'Punt AST': ['ast'],
  'Punt 3PM': ['fg3m'],
  'Bigs (punt FT% + 3PM)': ['ft_pct', 'fg3m'],
  'Guards (punt FG% + REB + BLK)': ['fg_pct', 'treb', 'blk'],
};

/**
 * Per-category weights for "best fit for my team": categories where my roster is weak
 * (relative to my other categories) weigh up to 1.5x, strong ones down to 0.5x.
 * Returns null until I have at least one player.
 */
export function needWeights(myPlayers, cats) {
  const keys = Object.keys(cats);
  if (!myPlayers.length || !keys.length) return null;
  const sums = keys.map((k) => myPlayers.reduce((s, p) => s + (p.z[k] || 0), 0));
  const { mean, sd } = meanSd(sums);
  return Object.fromEntries(keys.map((k, i) => [k, 1 - 0.5 * Math.tanh((sums[i] - mean) / sd)]));
}

export function fitScore(p, cats, weights) {
  return Object.keys(cats).reduce((s, k) => s + cats[k] * weights[k] * (p.z[k] || 0), 0);
}

// Tier breaks wherever value drops sharply between consecutive players (or a tier gets long).
export function assignTiers(ranked, { gap = 0.35, maxSize = 12 } = {}) {
  let t = 1;
  let size = 0;
  ranked.forEach((p, i) => {
    if (i > 0 && (ranked[i - 1].value - p.value >= gap || size >= maxSize)) {
      t += 1;
      size = 0;
    }
    p.tier = t;
    size += 1;
  });
  return ranked;
}

// ---------- H2H fantasy playoffs ----------

const DAY = 86400000;
const toDate = (s) => new Date(`${s}T00:00:00Z`);
const fmt = (d) => d.toISOString().slice(0, 10);

/**
 * Candidate 3-week (Mon-Sun) playoff windows, ending on the last Sunday of the regular season
 * and one or two weeks earlier (leagues differ). Returns [{start, end}] as YYYY-MM-DD.
 */
export function playoffWindows(schedule, weeks = 3) {
  const last = Object.values(schedule).flat().sort().at(-1);
  if (!last) return [];
  let end = toDate(last);
  while (end.getUTCDay() !== 0) end = new Date(end.getTime() - DAY); // back up to a Sunday
  return [0, 1, 2].map((shift) => {
    const e = new Date(end.getTime() - shift * 7 * DAY);
    return { start: fmt(new Date(e.getTime() - (weeks * 7 - 1) * DAY)), end: fmt(e) };
  });
}

export function gamesInWindow(dates = [], start, end) {
  return dates.filter((d) => d >= start && d <= end).length;
}

/**
 * Shift value toward players who'll actually play during the fantasy playoffs: their team's
 * games in the window, times their late-season availability (shutdowns, rest, injury risk).
 * weight 0 = ignore, 1 = playoff games scale value fully. Re-sorts and re-ranks.
 */
export function applyPlayoffWeight(ranked, { schedule = {}, start, end, weight = 0 }) {
  const teams = Object.keys(schedule);
  const hasWindow = Boolean(start && end && teams.length);
  const avg = hasWindow ? teams.reduce((s, t) => s + gamesInWindow(schedule[t], start, end), 0) / teams.length : 0;
  for (const p of ranked) {
    const factor = p.late_risk?.factor ?? 1;
    p.poGames = hasWindow ? gamesInWindow(schedule[p.team], start, end) : null;
    p.poRatio = (hasWindow && avg ? p.poGames / avg : 1) * factor;
    if (weight > 0) p.value += weight * Math.abs(p.value) * (p.poRatio - 1);
  }
  ranked.sort((a, b) => b.value - a.value);
  ranked.forEach((p, i) => { p.rank = i + 1; });
  return { ranked, avgGames: avg };
}
