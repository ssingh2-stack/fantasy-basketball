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
