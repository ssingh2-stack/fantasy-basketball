// Run with: node --test tests/
import test from 'node:test';
import assert from 'node:assert/strict';
import { rankPlayers, DEFAULT_CATS, tier } from '../static/rank.js';

// Synthetic players: an average baseline with one standout trait each.
const base = { gp: 70, mpg: 30, fgm: 6, fga: 13, ftm: 3, fta: 4, fg3m: 1.5, fg3a: 4, pts: 16.5,
  oreb: 1, dreb: 4, treb: 5, ast: 3, stl: 1, blk: 0.5, tov: 2 };
const filler = Array.from({ length: 40 }, (_, i) => ({ ...base, key: `f${i}`, name: `F${i}`, pts: 14 + (i % 5) }));
const shooter = { ...base, key: 'shooter', name: 'Shooter', fg3m: 4, fg3a: 9, ftm: 5, fta: 5.3, pts: 24 };
const big = { ...base, key: 'big', name: 'Big', treb: 12, dreb: 9, oreb: 3, blk: 2.5, fgm: 8, fga: 12, ftm: 2, fta: 4.5, fg3m: 0, fg3a: 0 };
const hurt = { ...shooter, key: 'hurt', name: 'Hurt', gp: 35 };
const players = [...filler, shooter, big, hurt];

test('standouts rank above filler', () => {
  const r = rankPlayers(players, { cats: DEFAULT_CATS, poolSize: 30 });
  const top3 = r.slice(0, 3).map((p) => p.key);
  assert.ok(top3.includes('shooter') && top3.includes('big'), top3.join());
  assert.equal(r.find((p) => p.key === 'big').z.ft_pct < 0, true); // poor FT% at volume hurts
});

test('punting a category re-ranks', () => {
  const nine = rankPlayers(players, { cats: DEFAULT_CATS, poolSize: 30 });
  const puntBigs = rankPlayers(players, { cats: { ...DEFAULT_CATS, treb: 0, blk: 0, fg_pct: 0 }, poolSize: 30 });
  const rank = (r, k) => r.find((p) => p.key === k).rank;
  assert.ok(rank(puntBigs, 'big') > rank(nine, 'big'));
  assert.ok(rank(puntBigs, 'shooter') <= rank(nine, 'shooter'));
});

test('multipliers and turnovers', () => {
  const r = rankPlayers(players, { cats: { pts: 1, tov: 1 }, poolSize: 30 });
  const sloppy = rankPlayers([...players, { ...base, key: 'sloppy', tov: 6 }], { cats: { tov: 1 }, poolSize: 30 });
  assert.ok(sloppy.find((p) => p.key === 'sloppy').z.tov < -2); // more turnovers = negative z
  const heavy = rankPlayers(players, { cats: { pts: 3, tov: 1 }, poolSize: 30 });
  const s = (rr) => rr.find((p) => p.key === 'shooter').value;
  assert.ok(s(heavy) > s(r));
});

test('games played: GP penalty in averages, natural penalty in totals', () => {
  const avg = rankPlayers(players, { cats: DEFAULT_CATS, poolSize: 30, gpPenalty: 0 });
  const pen = rankPlayers(players, { cats: DEFAULT_CATS, poolSize: 30, gpPenalty: 0.5, seasonGames: 82 });
  const tot = rankPlayers(players, { cats: DEFAULT_CATS, poolSize: 30, mode: 'tot' });
  const v = (r, k) => r.find((p) => p.key === k);
  assert.equal(v(avg, 'hurt').value.toFixed(6), v(avg, 'shooter').value.toFixed(6));
  assert.ok(v(pen, 'hurt').value < v(pen, 'shooter').value);
  assert.ok(v(tot, 'hurt').rank > v(tot, 'shooter').rank);
});

test('tiers', () => {
  assert.equal(tier(2.1), 'elite');
  assert.equal(tier(0), 'avg');
  assert.equal(tier(-2), 'bad');
});

test('need weights favor my weak categories', async () => {
  const { needWeights, fitScore } = await import('../static/rank.js');
  const cats = { pts: 1, ast: 1, blk: 1 };
  const mine = [{ z: { pts: 2, ast: 1.5, blk: -1 } }, { z: { pts: 1, ast: 1, blk: -0.5 } }];
  const w = needWeights(mine, cats);
  assert.ok(w.blk > 1 && w.pts < 1);
  const shotBlocker = { z: { pts: 0, ast: 0, blk: 2 } };
  const scorer = { z: { pts: 2, ast: 0, blk: 0 } };
  assert.ok(fitScore(shotBlocker, cats, w) > fitScore(scorer, cats, w));
  assert.equal(needWeights([], cats), null);
});

test('tiers break on value gaps and long runs', async () => {
  const { assignTiers } = await import('../static/rank.js');
  const players = [5, 4.9, 4.8, 4.0, 3.95, ...Array.from({ length: 15 }, (_, i) => 3.9 - i * 0.01)].map((value) => ({ value }));
  assignTiers(players);
  assert.deepEqual(players.slice(0, 5).map((p) => p.tier), [1, 1, 1, 2, 2]);
  assert.equal(players.at(-1).tier, 3); // the long flat run is split after 12
});
