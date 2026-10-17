import { CATS, DEFAULT_CATS, PRESETS, assignTiers, fitScore, needWeights, rankPlayers, tier } from './rank.js';

const MULTS = [0, 0.25, 0.5, 0.75, 1, 1.25, 1.5, 2];
const STORE_KEY = 'fantasy-hoops-settings';
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

const defaults = {
  cats: Object.fromEntries(Object.keys(CATS).map((k) => [k, { on: k in DEFAULT_CATS, mult: 1 }])),
  show: '200', mingp: '0', pos: '', team: '', owner: '', mode: 'avg', teams: '12', roster: '13',
  gppen: '0.2', hideTaken: false, sort: 'value', asc: false, localTaken: [], localMine: [],
};
let settings = loadSettings();
let data = null;
let autoSyncTimer = null;

function loadSettings() {
  try {
    const saved = JSON.parse(localStorage.getItem(STORE_KEY) || '{}');
    return { ...defaults, ...saved, cats: { ...defaults.cats, ...(saved.cats || {}) } };
  } catch {
    return structuredClone(defaults);
  }
}
function saveSettings() {
  try { localStorage.setItem(STORE_KEY, JSON.stringify(settings)); } catch { /* private mode */ }
}

function showMessage(html) {
  $('message').innerHTML = html;
  $('message').hidden = !html;
}

async function api(path, method = 'GET', body) {
  const res = await fetch(path, {
    method, headers: body ? { 'Content-Type': 'application/json' } : {}, body: body && JSON.stringify(body),
  });
  const json = await res.json().catch(() => ({}));
  if (!res.ok) throw Object.assign(new Error(json.detail || res.statusText), { status: res.status });
  return json;
}

// ---------- controls ----------

function buildPresets() {
  $('presets').innerHTML = Object.keys(PRESETS).map((name) => `<button type="button" data-preset="${esc(name)}">${esc(name)}</button>`).join('');
  $('presets').addEventListener('click', (e) => {
    const name = e.target.dataset.preset;
    if (!name) return;
    const off = new Set(PRESETS[name]);
    for (const k of Object.keys(CATS)) settings.cats[k] = { on: k in DEFAULT_CATS && !off.has(k), mult: 1 };
    saveSettings();
    buildCategoryControls();
    render();
  });
}

function buildCategoryControls() {
  $('cats').innerHTML = Object.entries(CATS).map(([k, c]) => `
    <div class="cat ${settings.cats[k].on ? 'on' : ''}" data-cat="${k}">
      <label><input type="checkbox" ${settings.cats[k].on ? 'checked' : ''}> ${c.label}</label>
      <select>${MULTS.map((m) => `<option value="${m}" ${m === settings.cats[k].mult ? 'selected' : ''}>x${m}</option>`).join('')}</select>
    </div>`).join('');
}

function bindCategoryControls() {
  $('cats').addEventListener('change', (e) => {
    const box = e.target.closest('.cat');
    const k = box.dataset.cat;
    if (e.target.type === 'checkbox') settings.cats[k].on = e.target.checked;
    else settings.cats[k].mult = Number(e.target.value);
    box.classList.toggle('on', settings.cats[k].on);
    saveSettings();
    render();
  });
}

const FILTERS = { 'f-show': 'show', 'f-mingp': 'mingp', 'f-pos': 'pos', 'f-team': 'team', 'f-owner': 'owner',
  'f-mode': 'mode', 'f-teams': 'teams', 'f-roster': 'roster', 'f-gppen': 'gppen' };

function bindFilters() {
  for (const [id, key] of Object.entries(FILTERS)) {
    $(id).value = settings[key];
    $(id).addEventListener('change', () => { settings[key] = $(id).value; saveSettings(); render(); });
  }
  $('f-hide-taken').checked = settings.hideTaken;
  $('f-hide-taken').addEventListener('change', () => { settings.hideTaken = $('f-hide-taken').checked; saveSettings(); render(); });
  $('f-search').addEventListener('input', render);
}

function fillSelect(id, options, keepFirst) {
  const sel = $(id);
  [...sel.options].slice(keepFirst).forEach((o) => o.remove());
  for (const [value, label] of options) sel.add(new Option(label, value));
  sel.value = settings[FILTERS[id]];
  if (sel.value !== settings[FILTERS[id]]) sel.value = '';
}

// ---------- data ----------

async function load() {
  try {
    data = await api('/api/data');
  } catch (e) {
    if (e.status === 503) {
      // The server is still downloading stats and building projections.
      showMessage(`<strong>Building projections…</strong> ${esc(e.message)}`);
      setTimeout(load, 5000);
    } else {
      showMessage(`<strong>No projections yet.</strong> ${esc(e.message)}`);
    }
    return;
  }
  showMessage('');
  $('season').textContent = `${data.config.season - 1}-${String(data.config.season).slice(2)}`;
  const leagueOn = data.config.league_configured;
  $('btn-sync').hidden = !leagueOn;
  $('auto-sync').parentElement.hidden = !leagueOn;
  $('btn-yahoo').hidden = !(leagueOn && data.config.platform === 'yahoo' && !data.config.yahoo_connected);
  if (!data.league && data.config.data_updated_at) {
    $('league-status').textContent = `Stats updated ${new Date(data.config.data_updated_at).toLocaleString()}`;
  }

  const teams = [...new Set(data.players.map((p) => p.team).filter(Boolean))].sort();
  fillSelect('f-team', teams.map((t) => [t, t]), 1);

  const lg = data.league;
  if (lg) {
    const owners = [['__mine', 'My team'], ...lg.teams.map((t) => [t.id, t.name])];
    fillSelect('f-owner', owners, 2);
    const when = new Date(lg.synced_at).toLocaleTimeString();
    const draft = lg.draft.in_progress ? ` · draft in progress (${lg.draft.picks.length} picks)` : '';
    $('league-status').textContent = `${lg.league_name} (${lg.platform.toUpperCase()}) · synced ${when}${draft}`;
    if (!localStorage.getItem(STORE_KEY + '-teams-set') && lg.teams.length) {
      settings.teams = String(lg.teams.length);
      $('f-teams').value = settings.teams;
      try { localStorage.setItem(STORE_KEY + '-teams-set', '1'); } catch { /* ignore */ }
    }
  }
  renderUnmatched();
  render();
}

function renderUnmatched() {
  const el = $('unmatched');
  const list = data.unmatched || [];
  el.hidden = !list.length;
  if (!list.length) return;
  el.querySelector('summary').textContent = `${list.length} league players have no projection`;
  el.querySelector('ul').innerHTML = list.map((u) =>
    `<li>${esc(u.name)} ${u.team ? `(${esc(u.team)})` : ''}${u.adp ? ` · ADP ${u.adp.toFixed(1)}` : ''}</li>`).join('');
}

// ---------- table ----------

function enabledCats() {
  return Object.fromEntries(Object.entries(settings.cats).filter(([, c]) => c.on && c.mult > 0).map(([k, c]) => [k, c.mult]));
}

function myTeamId() {
  return data.league?.teams.find((t) => t.mine)?.id;
}

function isMine(p) {
  const mine = myTeamId();
  return Boolean(mine && p.owner === mine) || settings.localMine.includes(p.key);
}

function isTaken(p) {
  return Boolean(p.owner) || settings.localTaken.includes(p.key) || settings.localMine.includes(p.key);
}

function matchesPos(p, pos) {
  const list = (p.pos || '').split(',');
  if (pos === 'G') return list.some((x) => x === 'PG' || x === 'SG');
  if (pos === 'F') return list.some((x) => x === 'SF' || x === 'PF');
  return list.includes(pos);
}

function cellFor(p, k, mode) {
  const c = CATS[k];
  const scale = mode === 'tot' ? p.gp : 1;
  const digits = mode === 'tot' ? 0 : 1;
  if (c.type === 'pct') {
    const pct = p[c.att] ? p[c.made] / p[c.att] : 0;
    return `${pct.toFixed(3).replace(/^0/, '')}<span class="sub">(${(p[c.made] * scale).toFixed(digits)}/${(p[c.att] * scale).toFixed(digits)})</span>`;
  }
  if (c.type === 'ratio') return p[c.den] ? (p[c.num] / p[c.den]).toFixed(2) : '-';
  return (p[c.stat] * scale).toFixed(digits);
}

const seasonLabel = (s) => `${String(s - 1).slice(2)}-${String(s).slice(2)}`;

function sortValue(p, key) {
  if (key in CATS) return p.z[key] ?? -99;
  if (key === 'adp') return p.adp ?? 999;
  if (key === 'adpdiff') return p.adp ? p.adp - p.rank : -999;
  if (key === 'name' || key === 'team' || key === 'pos') return p[key] || '';
  return p[key] ?? 0;
}

function render() {
  if (!data) return;
  const cats = enabledCats();
  const catKeys = Object.keys(cats);
  const mode = settings.mode;
  const ranked = rankPlayers(data.players, {
    cats, mode, poolSize: Number(settings.teams) * Number(settings.roster),
    gpPenalty: Number(settings.gppen), seasonGames: data.config.games_remaining,
  });
  assignTiers(ranked);
  const weights = needWeights(ranked.filter(isMine), cats);
  if (weights) for (const p of ranked) p.fit = fitScore(p, cats, weights);
  if (settings.sort === 'fit' && !weights) settings.sort = 'value';

  const mine = myTeamId();
  const q = $('f-search').value.trim().toLowerCase();
  let rows = ranked.filter((p) => {
    if (p.gp < Number(settings.mingp)) return false;
    if (settings.pos && !matchesPos(p, settings.pos)) return false;
    if (settings.team && p.team !== settings.team) return false;
    if (settings.owner === '__avail' && isTaken(p)) return false;
    if (settings.owner === '__mine' && !isMine(p)) return false;
    if (settings.owner && !settings.owner.startsWith('__') && p.owner !== settings.owner) return false;
    if (settings.hideTaken && isTaken(p) && settings.owner !== '__mine') return false;
    if (q && !p.name.toLowerCase().includes(q)) return false;
    return true;
  });
  if (settings.sort !== 'value') {
    const dir = settings.asc ? 1 : -1;
    rows.sort((a, b) => {
      const va = sortValue(a, settings.sort);
      const vb = sortValue(b, settings.sort);
      return (va > vb ? 1 : va < vb ? -1 : 0) * dir;
    });
  } else if (settings.asc) {
    rows.reverse();
  }
  rows = rows.slice(0, Number(settings.show));

  const teamNames = Object.fromEntries((data.league?.teams || []).map((t) => [t.id, t.name]));
  const hasAdp = data.players.some((p) => p.adp);
  const hasLeague = Boolean(data.league);

  const cols = [
    ['check', '✓', false], ['star', '★', false], ['rank', 'R#', true], ['tier', 'Tier', false], ['name', 'Player', true],
    ...(hasAdp ? [['adp', 'ADP', true], ['adpdiff', '+/-', true]] : []),
    ['pos', 'Pos', true], ['team', 'Team', true], ...(hasLeague ? [['owner', 'Owner', true]] : []),
    ['gp', 'GP', true], ['mpg', 'MPG', true],
    ...catKeys.map((k) => [k, CATS[k].label, true]), ['value', 'Total', true],
    ...(weights ? [['fit', 'Fit', true]] : []),
  ];
  const titles = {
    adpdiff: 'ADP minus our rank: positive = value pick',
    check: 'Mark as taken by another team (for drafts the league sync has not caught up on)',
    star: 'Mark as my pick: powers the Fit column and My team row',
    fit: 'Value weighted toward the categories your team is weakest in (punted categories excluded)',
    tier: 'Value tier: players in the same tier are close in value',
  };
  $('table').tHead.innerHTML = `<tr>${cols.map(([k, label, sortable]) => {
    const cls = settings.sort === k ? `sorted ${settings.asc ? 'asc' : ''}` : '';
    return `<th data-sort="${sortable ? k : ''}" class="${cls}" title="${titles[k] || ''}">${label}</th>`;
  }).join('')}</tr>`;

  // Tier divider rows only make sense in plain value order.
  const showTierBreaks = settings.sort === 'value' && !settings.asc;
  let lastTier = null;
  $('table').tBodies[0].innerHTML = rows.map((p) => {
    const divider = showTierBreaks && p.tier !== lastTier
      ? `<tr class="tier-break"><td colspan="${cols.length}">Tier ${p.tier}</td></tr>` : '';
    lastTier = p.tier;
    const badges = [
      p.injury ? `<span class="badge inj" title="Injury status">${esc(p.injury)}</span>` : '',
      p.prev_team ? `<span class="badge new" title="Was on ${esc(p.prev_team)} last season">NEW</span>` : '',
      p.rookie ? '<span class="badge rookie">R</span>' : '',
      (p.missed_seasons || []).length
        ? `<span class="badge inj" title="Did not play in ${p.missed_seasons.map(seasonLabel).join(', ')}: projection uses older seasons, with fewer games and extra aging">MISSED ${p.missed_seasons.map(seasonLabel).join(', ')}</span>`
        : '',
      p.note ? `<span class="badge ovr" title="${esc(p.note)}">i</span>` : '',
    ].join('');
    const diff = p.adp ? p.adp - p.rank : null;
    const cls = [isMine(p) ? 'mine' : '', isTaken(p) && !isMine(p) ? 'taken' : ''].join(' ');
    return `${divider}<tr class="${cls}" data-key="${esc(p.key)}">
      <td class="check"><input type="checkbox" data-mark="taken" ${settings.localTaken.includes(p.key) ? 'checked' : ''} ${p.owner ? 'disabled' : ''}></td>
      <td class="check"><button type="button" class="star ${settings.localMine.includes(p.key) ? 'on' : ''}" data-mark="mine" ${p.owner ? 'disabled' : ''} aria-label="Mark as my pick">★</button></td>
      <td class="num">${p.rank}</td>
      <td class="num">${p.tier}</td>
      <td class="name">${esc(p.name)}${badges}</td>
      ${hasAdp ? `<td class="num">${p.adp ? p.adp.toFixed(1) : '-'}</td>
        <td class="num adp-diff ${diff > 0 ? 'pos' : diff < 0 ? 'neg' : ''}">${diff === null ? '' : (diff > 0 ? '+' : '') + diff.toFixed(0)}</td>` : ''}
      <td>${esc(p.pos)}</td>
      <td>${esc(p.team)}</td>
      ${hasLeague ? `<td class="owner">${esc(teamNames[p.owner] || '')}</td>` : ''}
      <td class="num">${p.gp}</td>
      <td class="num">${p.mpg.toFixed(1)}</td>
      ${catKeys.map((k) => `<td class="num z ${tier(p.z[k])}" title="z = ${p.z[k].toFixed(2)}">${cellFor(p, k, mode)}</td>`).join('')}
      <td class="num value">${p.value.toFixed(2)}</td>
      ${weights ? `<td class="num value">${p.fit.toFixed(2)}</td>` : ''}
    </tr>`;
  }).join('') || `<tr><td colspan="${cols.length}">No players match these filters.</td></tr>`;

  renderTeams(ranked, catKeys);
}

// Category strength of every fantasy team: sum of its players' z-scores.
function renderTeams(ranked, catKeys) {
  const el = $('my-team');
  // Without a synced league, show just my own picks (marked with ★) as one team.
  const lg = data.league || { teams: [{ id: '__local', name: 'My team', mine: true }] };
  const ownerOf = (p) => (settings.localMine.includes(p.key) ? (myTeamId() || '__local') : p.owner);
  const rostered = ranked.filter((p) => ownerOf(p));
  if (!rostered.length) { el.hidden = true; return; }
  const sums = Object.fromEntries(lg.teams.map((t) => [t.id, { n: 0, value: 0, ...Object.fromEntries(catKeys.map((k) => [k, 0])) }]));
  for (const p of rostered) {
    const s = sums[ownerOf(p)];
    if (!s) continue;
    s.n += 1;
    s.value += p.value;
    for (const k of catKeys) s[k] += p.z[k];
  }
  // Rank of each team within each category (1 = best), colored relative to the other teams.
  const rankIn = (k, id) => 1 + lg.teams.filter((t) => sums[t.id][k] > sums[id][k]).length;
  const spread = Object.fromEntries(catKeys.map((k) => {
    const vals = lg.teams.map((t) => sums[t.id][k]);
    const mean = vals.reduce((a, v) => a + v, 0) / vals.length;
    const sd = Math.sqrt(vals.reduce((a, v) => a + (v - mean) ** 2, 0) / vals.length) || 1;
    return [k, { mean, sd }];
  }));
  // With one team there's nothing to compare against, so color by average player z instead.
  const teamTier = (k, v) => (lg.teams.length > 1
    ? tier((v - spread[k].mean) / spread[k].sd)
    : tier(v / Math.max(1, sums[lg.teams[0].id].n) * 1.5));
  const teams = [...lg.teams].sort((a, b) => sums[b.id].value - sums[a.id].value);
  el.hidden = false;
  el.querySelector('summary').textContent = data.league
    ? 'Team category strength (sum of player z-scores, rank in league)'
    : `My team: ${sums.__local.n} picks (sum of z-scores per category)`;
  if (!data.league) el.open = true;
  el.querySelector('.body').innerHTML = `
    <div class="table-wrap"><table><thead><tr><th>Team</th><th>Players</th>
      ${catKeys.map((k) => `<th>${CATS[k].label}</th>`).join('')}<th>Total</th></tr></thead><tbody>
      ${teams.map((t) => {
        const s = sums[t.id];
        return `<tr class="${t.mine ? 'mine' : ''}"><td class="name">${esc(t.name)}</td><td>${s.n}</td>
          ${catKeys.map((k) => `<td class="z ${teamTier(k, s[k])}">${s[k].toFixed(1)}${lg.teams.length > 1 ? ` <span class="sub">#${rankIn(k, t.id)}</span>` : ''}</td>`).join('')}
          <td class="value">${s.value.toFixed(1)}</td></tr>`;
      }).join('')}
    </tbody></table></div>`;
}

function bindTable() {
  $('table').tHead.addEventListener('click', (e) => {
    let key = e.target.closest('th')?.dataset.sort;
    if (!key) return;
    if (key === 'rank') key = 'value';
    if (settings.sort === key) settings.asc = !settings.asc;
    else { settings.sort = key; settings.asc = ['name', 'team', 'pos', 'adp'].includes(key); }
    saveSettings();
    render();
  });
  const toggleMark = (e) => {
    const mark = e.target.dataset?.mark;
    const key = e.target.closest('tr')?.dataset.key;
    if (!mark || !key) return;
    const list = mark === 'mine' ? 'localMine' : 'localTaken';
    const other = mark === 'mine' ? 'localTaken' : 'localMine';
    const set = new Set(settings[list]);
    if (set.has(key)) set.delete(key); else set.add(key);
    settings[list] = [...set];
    settings[other] = settings[other].filter((k) => k !== key); // a player is mine or taken, not both
    saveSettings();
    render();
  };
  $('table').tBodies[0].addEventListener('change', toggleMark);
  $('table').tBodies[0].addEventListener('click', (e) => { if (e.target.tagName === 'BUTTON') toggleMark(e); });
}

// ---------- actions ----------

async function withButton(btn, fn) {
  btn.disabled = true;
  try { await fn(); } catch (e) { showMessage(esc(e.message)); } finally { btn.disabled = false; }
}

async function syncLeague() {
  await api('/api/league/sync', 'POST');
  await load();
}

function bindActions() {
  $('btn-sync').addEventListener('click', () => withButton($('btn-sync'), syncLeague));
  $('btn-best-fit').addEventListener('click', () => {
    settings.sort = 'fit';
    settings.asc = false;
    settings.owner = '__avail';
    $('f-owner').value = '__avail';
    saveSettings();
    render();
  });
  $('btn-clear-marks').addEventListener('click', () => {
    if (!confirm('Clear all your ✓ and ★ marks?')) return;
    settings.localTaken = [];
    settings.localMine = [];
    saveSettings();
    render();
  });
  $('btn-project').addEventListener('click', () => withButton($('btn-project'), async () => {
    await api('/api/project', 'POST');
    await load();
  }));
  $('auto-sync').addEventListener('change', (e) => {
    clearInterval(autoSyncTimer);
    if (e.target.checked) {
      syncLeague().catch((err) => showMessage(esc(err.message)));
      autoSyncTimer = setInterval(() => syncLeague().catch((err) => showMessage(esc(err.message))), 30000);
    }
  });
  $('btn-yahoo').addEventListener('click', async () => {
    try {
      const { url } = await api('/api/yahoo/auth-url');
      $('yahoo-link').href = url;
      $('yahoo-dialog').showModal();
    } catch (e) { showMessage(esc(e.message)); }
  });
  $('yahoo-dialog').addEventListener('close', async () => {
    if ($('yahoo-dialog').returnValue !== 'ok') return;
    try {
      await api('/api/yahoo/code', 'POST', { code: $('yahoo-code').value });
      await syncLeague();
    } catch (e) { showMessage(esc(e.message)); }
  });
}

buildPresets();
buildCategoryControls();
bindCategoryControls();
bindFilters();
bindTable();
bindActions();
load();
