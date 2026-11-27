/* Shared markup and cached review requests for Windows and mobile. */
const coachingViews = new Map();
const coachingHistory = new Map();
const coachingStorage = "moosi.coaching.results.v2";
let coachingPatch;
function readCoachingStorage() {
  try { return JSON.parse(localStorage.getItem(coachingStorage) || "{}"); }
  catch { return {}; }
}
function saveCoachingResult(key, result) {
  try {
    const previous = Object.entries(readCoachingStorage()).filter(([entry]) => entry !== key).slice(-99);
    localStorage.setItem(coachingStorage, JSON.stringify(Object.fromEntries([...previous,[key,result]])));
  } catch { /* A disabled or full local store does not prevent a review. */ }
}
function coachingMatch(id, puuid) {
  const key = JSON.stringify([puuid,id]);
  if (!coachingViews.has(key)) {
    const saved = readCoachingStorage()[key];
    if (saved?.version === MoosiCoaching.VERSION && saved.total != null) return Promise.resolve(saved);
    coachingPatch ??= fetch(new URL("patch_config.json",location.href)).then(response => response.json()).catch(() => null);
    const promise = Promise.all([getCoachingInput(id, puuid),coachingPatch])
      .then(([{info,timeline},patch]) => {
        const result = MoosiCoaching.analyze(info,timeline,puuid,patch);
        if (patch && result?.total != null) saveCoachingResult(key,result);
        return result;
      })
      .catch(error => { coachingViews.delete(key); throw error; });
    coachingViews.set(key,promise);
    if (coachingViews.size > 100) coachingViews.delete(coachingViews.keys().next().value);
  }
  return coachingViews.get(key);
}
function coachingPanel(result) {
  if (result === undefined) return '<p class="muted">Analysiere Match-Timeline …</p>';
  if (result === false) return '<p class="muted">Timeline gerade nicht erreichbar. Öffne das Spiel erneut.</p>';
  if (!result) return '<p class="muted">Für dieses Spiel ist keine auswertbare Timeline verfügbar.</p>';
  const at = ms => Math.floor(ms/60000) + ":" + String(Math.floor(ms/1000)%60).padStart(2,"0");
  return '<section class="card coaching-panel"><h3>Coaching-Analyse</h3><div class="coaching-total"><b>' +
    (result.total == null ? 'Teilansicht' : result.total + ' <small>/ 100</small>') + '</b><span>' +
    MoosiCoaching.outcome(result) + '</span></div>' +
    (result.lever ? '<p><strong>' + at(result.lever.time) + ' · Replay-Fokus</strong><br>' + result.lever.text + '</p>' : '') +
    '<details><summary>Vier Teilwerte</summary>' + result.pillars.map(p =>
      '<div class="coaching-factor"><span>' + p.name + ' · ' + p.weight + '%</span><b>' +
      (p.value == null ? '–' : Math.round(p.value)) + '</b>' +
      (p.value == null ? '' : '<meter min="0" max="100" value="' + Math.round(p.value) + '"></meter>') +
      '</div>').join('') +
    '<p class="muted small">Messwerte aus Riot-Daten. Sicht, Absicht und exakte Bewegungen im Replay prüfen.</p></details></section>';
}
function coachingMission(block, result) {
  if (!result?.sample?.length) return "";
  const key = JSON.stringify(["moosi.coaching.mission.v1",block.puuid,result.role,result.queue]);
  const latest = result.sample.at(-1);
  const metric = latest.lever?.metric;
  const rules = {
    objectiveDeath:["Vor Objectives den Rückweg prüfen", r => Array.isArray(r.deaths) && r.limits.objectiveEvents > 0
      ? !r.deaths.some(death => death.plannedSpawn || death.nearbyObjective && !death.trade) : null],
    early:["Early-Wert auf mindestens 72 bringen", r => Number.isFinite(r.early) ? r.early >= 72 : null],
    tempo:["Tempo-Wert auf mindestens 65 bringen", r => Number.isFinite(r.pillars[1].value) ? r.pillars[1].value >= 65 : null],
    impact:["Rollenbeitrag auf mindestens 65 bringen", r => Number.isFinite(r.pillars[3].value) ? r.pillars[3].value >= 65 : null]
  };
  try {
    let task = JSON.parse(localStorage.getItem(key) || "null");
    if (!task?.metric || !rules[task.metric]) {
      if (!rules[metric]) return "";
      task = {metric,after:latest.start};
      localStorage.setItem(key,JSON.stringify(task));
    }
    if (task.last?.end === latest.start) {
      return '<p><strong>' + task.last.title + '</strong><br>' + task.last.successes + '/3 erreicht · ' +
        (task.last.successes >= 2 ? 'Block abgeschlossen.' : 'Gleiche Mission im nächsten Block.') + '</p>';
    }
    const [name,evaluate] = rules[task.metric];
    const evaluations = result.sample.filter(row => row.start > task.after).map(row => ({start:row.start, hit:evaluate(row)}))
      .filter(row => row.hit !== null).slice(0,3);
    const successes = evaluations.filter(row => row.hit).length;
    if (evaluations.length === 3 && task.after < evaluations[2].start) {
      const next = {metric:successes >= 2 && rules[metric] ? metric : task.metric,after:evaluations[2].start};
      localStorage.setItem(key,JSON.stringify({...next,last:{successes,title:name,end:next.after}}));
      if (latest.start <= next.after) {
        return '<p><strong>' + name + '</strong><br>' + successes + '/3 erreicht · ' +
          (successes >= 2 ? 'Block abgeschlossen.' : 'Gleiche Mission im nächsten Block.') + '</p>';
      }
    }
    return '<p><strong>' + name + '</strong><br>' + evaluations.length + '/3 ausgewertet · ' + successes + ' im Ziel · Auf diesem Gerät</p>';
  } catch { return ""; }
}

function coachingBlock(rows,puuid,editable=false) {
  const first = rows.find(row => row.role && [420,440].includes(row.queue));
  if (!puuid || !first) return '';
  const ids = [...new Set(rows.filter(row => row.role === first.role && row.queue === first.queue).map(row => row.id))].slice(0,15);
  const key = JSON.stringify([puuid,ids]);
  if (!coachingHistory.has(key)) {
    if (coachingHistory.size >= 20) coachingHistory.delete(coachingHistory.keys().next().value);
    coachingHistory.set(key,{puuid,ids});
  }
  const block = coachingHistory.get(key);
  const result = block.result;
  const plot = result?.sample ? '<div class="coaching-trend" role="img" aria-label="Prozesswerte für ' +
    result.count + ' Spiele, chronologisch">' + result.sample.map(row =>
      '<i class="' + (row.win ? 'win' : 'loss') + '" title="' + row.total + ' / 100" style="height:' + row.total + '%"></i>'
    ).join('') + '<em style="bottom:' + result.floor + '%"></em><em style="bottom:' + result.ceiling + '%"></em></div>' : '';
  const score = result?.index != null ? '<p>Floor <b>' + Math.round(result.floor) + '</b> · Ceiling <b>' +
    Math.round(result.ceiling) + '</b> · Early-Stabilität <b>' + Math.round(result.routine) +
    '</b> · Index <b>' + Math.round(result.index) + '</b></p>' + plot +
    (result.fatigue ? '<p class="muted">Der Prozesswert fiel im letzten Spielblock. Eine kurze Pause oder ein Replay-Check bietet sich an.</p>' : '') : '';
  return '<section class="card coaching-panel"><h3>Dein Leistungsfloor</h3><p class="muted small">' + first.role +
    ' · ' + (first.queue === 420 ? 'Solo/Duo' : 'Flex') + ' · ' + (result?.count ?? ids.length) +
    '/15 ' + (result ? 'auswertbar' : 'geladen') + '</p>' + score +
    '<button class="btn sec" data-coaching-key="' + encodeURIComponent(key) + '" onclick="loadCoachingBlock(this)" ' +
    (block.busy || ids.length < 5 ? 'disabled' : '') + '>' + (block.busy ? 'Wird ausgewertet …' : 'Ranked-Verlauf auswerten') +
    '</button>' + (editable ? coachingMission(block,result) : '') + '<p aria-live="polite" class="muted small">' + (block.message || (ids.length < 5 ? 'Mindestens fünf passende Matches erforderlich.' : '')) + '</p></section>';
}
async function loadCoachingBlock(button) {
  const block = coachingHistory.get(decodeURIComponent(button.dataset.coachingKey));
  if (!block || block.busy) return;
  block.busy = true;
  const results = [];
  try {
    for (const id of block.ids) {
      block.message = 'Lade Match ' + (results.length+1) + ' von ' + block.ids.length;
      render();
      results.push(await coachingMatch(id,block.puuid));
    }
    block.result = MoosiCoaching.consistency(results);
    block.message = (block.result?.count || 0) < 5 ? 'Für den Verlauf fehlen vollständige Werte.' : '';
  } catch { block.message = 'Abruf unterbrochen. Bitte erneut versuchen.'; }
  finally { block.busy = false; render(); }
}
