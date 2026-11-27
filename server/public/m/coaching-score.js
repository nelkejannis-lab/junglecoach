/* Deterministic post-match measures. Used by the Windows dashboard and mobile web view. */
(function (root) {
  "use strict";
  const VERSION = 1;
  const WEIGHTS = {
    scaler: [30,30,20,20], skirmisher: [30,20,25,25], tank: [20,20,25,35], assassin: [25,25,30,20],
    JUNGLE: [30,25,25,20], TOP: [30,25,25,20], MIDDLE: [30,25,25,20], BOTTOM: [30,30,25,15], UTILITY: [25,20,25,30]
  };
  const JUNGLE = {
    scaler: "Karthus Lillia Shyvana Graves Brand Belveth Gwen MasterYi Fiddlesticks Diana Udyr Hecarim",
    skirmisher: "LeeSin Elise JarvanIV RekSai XinZhao Nidalee Vi Warwick Volibear Trundle Poppy Jax Briar",
    tank: "Sejuani Maokai Amumu Zac Ivern Skarner Rammus Nunu",
    assassin: "Khazix Evelynn Rengar Viego Ekko Talon Kayn Shaco Nocturne Qiyana"
  };
  const TARGET = {scaler: [28,72], skirmisher: [24,58], tank: [24,56], assassin: [24,64], JUNGLE: [24,60]};
  const NAMES = ["Frühes Fundament", "Tempo und Aktivität", "Todeskontext", "Rollenbeitrag"];
  const clamp = value => Math.max(0, Math.min(100, value));
  const mean = values => values.reduce((sum, value) => sum + value, 0) / values.length;
  const weighted = pairs => pairs.every(([value]) => Number.isFinite(value))
    ? clamp(pairs.reduce((sum, [value, weight]) => sum + value * weight, 0) / 100) : null;
  const proportion = (own, other) => Number.isFinite(own) && Number.isFinite(other) && own + other > 0
    ? 200 * own / (own + other) : null;
  const curve = (value, points) => {
    if (value <= points[0][0]) return points[0][1];
    for (let i = 1; i < points.length; i++) {
      const [x,y] = points[i], [a,b] = points[i-1];
      if (value <= x) return b + (value-a)/(x-a)*(y-b);
    }
    return points.at(-1)[1];
  };
  const totalCs = frame => frame?.minionsKilled + frame?.jungleMinionsKilled;
  const participated = (event, player) => event.killerId === player || event.assistingParticipantIds?.includes(player);
  const major = event => event.type === "ELITE_MONSTER_KILL" &&
    ["DRAGON","BARON_NASHOR","RIFTHERALD","HORDE","ATAKHAN"].includes(event.monsterType);
  const scoreDate = info => info.gameStartTimestamp || info.gameCreation || 0;
  function analyze(info, timeline, puuid, patch) {
    const participants = info?.participants || [];
    const player = participants.find(row => row.puuid === puuid);
    const role = player?.teamPosition;
    const duration = info?.gameDuration;
    const frames = timeline?.info?.frames || [];
    if (!player || !WEIGHTS[role] || ![420,440,400].includes(info.queueId) ||
        (info.mapId != null && info.mapId !== 11) || duration < 600 || !Number.isFinite(duration) ||
        !frames.length || Math.abs(frames.at(-1).timestamp / 1000 - duration) > 65) return null;
    const pid = player.participantId;
    const rival = participants.find(row => row.teamPosition === role && row.teamId !== player.teamId);
    const archetype = role === "JUNGLE"
      ? Object.keys(JUNGLE).find(key => JUNGLE[key].split(" ").includes(player.championName)) || role : role;
    const events = frames.flatMap(frame => frame.events || []);
    const kills = events.filter(event => event.type === "CHAMPION_KILL");
    const objectives = events.filter(major);
    const teamByPlayer = new Map(participants.map(row => [row.participantId, row.teamId]));
    const owner = event => event.killerTeamId || event.teamId || teamByPlayer.get(event.killerId);
    const ours = event => owner(event) === player.teamId;
    const at = minute => frames.find(frame => Math.abs(frame.timestamp - minute * 60000) < 5000);
    const near = (event, start, end) => event.timestamp >= start && event.timestamp <= end;
    const positiveTrade = time => {
      const skirmish = kills.filter(event => near(event, time-15000, time+15000));
      return skirmish.filter(ours).length > skirmish.filter(event => owner(event) && !ours(event)).length ||
        objectives.some(event => ours(event) && near(event, time-15000, time+15000));
    };
    const scheduled = String(info.gameVersion || "").split(".").slice(0,2).join(".") === patch?.data_dragon_patch
      ? Object.entries(patch.initial_spawn_seconds || {}).map(([goal,seconds]) => ({goal,time:seconds*1000})) : [];
    const beforeSpawn = time => scheduled.find(goal => goal.time <= duration*1000 && goal.time > time && goal.time-time <= 50000);
    const deaths = kills.filter(event => event.victimId === pid).map(event => {
      const time = event.timestamp;
      const preObjective = objectives.find(goal => !ours(goal) && owner(goal) && near(goal, time, time+50000));
      const previousObjective = objectives.find(goal => ours(goal) && near(goal, time-35000, time));
      const trade = positiveTrade(time);
      const spawn = beforeSpawn(time);
      const nearbyObjective = preObjective?.monsterType || spawn?.goal || null;
      const snapshot = [...frames].reverse().find(frame => frame.timestamp < time)?.participantFrames;
      const gold = participants.map(p => ({team:p.teamId,value:snapshot?.[p.participantId]?.totalGold}));
      const teamLead = gold.length === 10 && gold.every(row => Number.isFinite(row.value))
        ? gold.reduce((sum,row) => sum+(row.team === player.teamId ? row.value : -row.value),0) : null;
      const lostCritical = objectives.some(goal => owner(goal) && !ours(goal) && near(goal,time,time+45000) &&
        (goal.monsterType === "BARON_NASHOR" || goal.monsterSubType === "ELDER_DRAGON")) ||
        events.some(goal => goal.type === "BUILDING_KILL" && goal.buildingType === "INHIBITOR_BUILDING" &&
          goal.teamId === player.teamId && near(goal,time,time+45000));
      const turningPoint = Boolean(spawn && time >= 900000 && teamLead >= 3000 && lostCritical);
      // Kill timing does not imply an objective was started or lost because of this player.
      return {time, trade, nearbyObjective, plannedSpawn:spawn?.time ?? null, teamLead, turningPoint,
        shutdown:event.shutdownBounty ?? null, weight:spawn ? 3 : trade ? (archetype === "tank" ? .1 : .3) : previousObjective ? 1.4 : 1,
        kind:turningPoint ? "Tod rund um den Spawn, danach ein wichtiges Ziel verloren" :
          spawn ? "Tod kurz vor geplantem Objective-Spawn" : trade ? "Trade im Zeitfenster" :
          previousObjective ? "Tod nach eigenem Objective" : preObjective ? "Tod vor gegnerischem Objective-Kill" : "Tod im Replay prüfen"};
    });
    const deathComplete = deaths.length === player.deaths;
    const ten = at(10)?.participantFrames;
    const earlyDeaths = deathComplete ? clamp(100-deaths.filter(death => death.time < 600000)
      .reduce((sum, death) => sum + (death.trade ? 5 : death.time < 360000 ? 35 : 22),0)) : null;
    const purchases = events.filter(event => event.type === "ITEM_PURCHASED" && event.participantId === pid);
    const goldFrames = [4,5,6,7,8,9,10].map(minute => at(minute));
    const goldDiscipline = goldFrames.every(frame => Number.isFinite(frame?.participantFrames?.[pid]?.currentGold))
      ? clamp(100-18*Math.max(0,goldFrames.filter(frame => frame.participantFrames[pid].currentGold >= 1300 &&
        !purchases.some(event => near(event,frame.timestamp-60000,frame.timestamp+60000))).length-1)) : null;
    const levelSix = id => events.find(event => event.type === "LEVEL_UP" && event.level === 6 && event.participantId === id)?.timestamp;
    const gap = levelSix(rival?.participantId) - levelSix(pid);
    const level = Number.isFinite(gap) ? clamp(Math.abs(gap) <= 10000 ? 75 : 70 +
      (gap > 0 ? 5 : -6) * Math.floor(Math.abs(gap)/15000)) : null;
    const csCurve = TARGET[archetype] ? [4,6,8,10].map(minute => {
      const [low, high] = TARGET[archetype];
      const target = (low + (high-low)*(minute-4)/6) *
        (kills.filter(event => event.timestamp < 360000 && participated(event,pid)).length >= 2 ? .92 : 1);
      return clamp(totalCs(at(minute)?.participantFrames?.[pid]) / target * 100);
    }) : [];
    const xp = proportion(ten?.[pid]?.xp,ten?.[rival?.participantId]?.xp);
    const resources = TARGET[archetype] ? (csCurve.every(Number.isFinite) ? mean(csCurve) : null)
      : proportion(ten?.[pid]?.totalGold,ten?.[rival?.participantId]?.totalGold);
    const early = weighted([[resources,35],[role === "JUNGLE" ? level : xp,25],[earlyDeaths,25],[goldDiscipline,15]]);
    let active = 0, observed = 0, quiet = null;
    for (let i = 2; i < frames.length; i++) {
      const before = frames[i-1], after = frames[i];
      const a = before.participantFrames?.[pid], b = after.participantFrames?.[pid];
      const elapsed = after.timestamp-before.timestamp;
      if (!a || !b || elapsed < 45000 || elapsed > 75000 ||
          deaths.some(death => near({timestamp:death.time}, before.timestamp-60000, after.timestamp))) continue;
      const frameEvents = (after.events || []).filter(event => event.type === "WARD_PLACED" && event.creatorId === pid ||
        event.type === "WARD_KILL" && event.killerId === pid || event.type === "ITEM_PURCHASED" && event.participantId === pid ||
        (event.type === "CHAMPION_KILL" || major(event)) && participated(event,pid));
      const delta = b.damageStats?.totalDamageDoneToChampions - a.damageStats?.totalDamageDoneToChampions;
      if (!Number.isFinite(delta) || !Number.isFinite(totalCs(b)-totalCs(a))) continue;
      const output = b.jungleMinionsKilled-a.jungleMinionsKilled >= 4 ||
        b.minionsKilled-a.minionsKilled >= 5 || delta >= 100 || frameEvents.length > 0;
      if (output) active++;
      else if (quiet === null) quiet = after.timestamp;
      observed++;
    }
    const activity = observed >= 5 ? curve(active/observed,[[.60,20],[.75,65],[.90,100]]) : null;
    const fourteen = at(14);
    const farmEnd = at(25) || [...frames].reverse().find(frame => frame.timestamp < 1500000);
    const farmStart = totalCs(at(2)?.participantFrames?.[pid]);
    const farmMid = totalCs(fourteen?.participantFrames?.[pid]);
    const farmLate = totalCs(farmEnd?.participantFrames?.[pid]);
    const farmMinutes = (farmEnd?.timestamp - fourteen?.timestamp) / 60000;
    const midKills = kills.filter(event => ours(event) && near(event, 840000, farmEnd?.timestamp));
    const midKp = midKills.filter(event => participated(event,pid)).length / midKills.length;
    const farmRatio = (farmLate-farmMid)/farmMinutes / ((farmMid-farmStart)/12);
    const farm = role !== "UTILITY" && farmMinutes >= 3 && Number.isFinite(farmRatio) && farmRatio >= 0
      ? midKp > .75 ? 100 : curve(farmRatio,[[0,20],[.60,25],[.85,95],[1,100]]) : null;
    // The API has no continuous movement or death interval; keep activity and farm as observable measures.
    const tempo = role === "UTILITY" ? activity : weighted([[activity,60],[farm,40]]);
    const deathScore = deathComplete ? clamp(100 - 11 * 1800/duration * deaths.reduce((sum,death) => sum+death.weight,0) - (deaths.some(death => death.turningPoint) ? 12 : 0)) : null;
    const mates = participants.filter(row => row.teamId === player.teamId);
    const teamStat = key => mates.every(row => Number.isFinite(row[key])) ? mates.reduce((sum,row) => sum+row[key],0) : NaN;
    const goldShare = player.goldEarned/teamStat("goldEarned");
    const damageRatio = (player.totalDamageDealtToChampions/teamStat("totalDamageDealtToChampions"))/goldShare;
    const killShare = (player.kills+player.assists)/teamStat("kills");
    const utility = archetype === "tank" || role === "UTILITY";
    const efficiency = utility ? (Number.isFinite(killShare) && Number.isFinite(player.timeCCingOthers)
      ? mean([clamp(killShare/.65*100),clamp(player.timeCCingOthers/(duration/60)*30)]) : null)
      : Number.isFinite(damageRatio) ? curve(damageRatio,[[0,0],[.65,35],[1,75],[1.25,100]]) : null;
    const alliedGoals = objectives.filter(ours);
    const objectiveContribution = alliedGoals.length ? 100*alliedGoals.filter(event => participated(event,pid)).length/alliedGoals.length
      : null;
    const setup = objectives.length ? 100*objectives.filter(goal => purchases.some(event =>
      near(event,goal.timestamp-135000,goal.timestamp-60000)) || events.some(event =>
      near(event,goal.timestamp-60000,goal.timestamp-10000) && (event.type === "WARD_PLACED" && event.creatorId === pid ||
        event.type === "WARD_KILL" && event.killerId === pid))).length/objectives.length : null;
    const impact = weighted([[setup,40],[efficiency,35],[objectiveContribution,25]]);
    const totals = [early,tempo,deathScore,impact];
    const total = weighted(totals.map((value,i) => [value,WEIGHTS[archetype][i]]));
    const objectiveDeath = deaths.find(death => death.turningPoint) || deaths.find(death => death.plannedSpawn) ||
      deaths.find(death => death.nearbyObjective && !death.trade);
    const earlyDeath = deaths.find(death => death.time < 360000 && !death.trade);
    const lever = objectiveDeath ? {time:objectiveDeath.time, text:objectiveDeath.kind, metric:"objectiveDeath"} :
      early !== null && early < 60 ? {time:earlyDeath?.time ?? 600000, text:earlyDeath?.kind || "Erste Clear- und Reset-Entscheidungen vergleichen", metric:"early"} :
      tempo !== null && tempo < 60 && quiet !== null ? {time:quiet, text:"Erste Minute ohne messbare Aktivität prüfen", metric:"tempo"} :
      impact !== null && impact < 60 && objectives.length ? {time:objectives[0].timestamp, text:"Vorbereitung vor dem Objective im Replay prüfen", metric:"impact"} :
      deaths.length ? {time:deaths[0].time, text:deaths[0].kind, metric:"deaths"} : null;
    return {version:VERSION, role, archetype, queue:info.queueId, start:scoreDate(info), duration, win:player.win,
      total:total === null ? null : Math.round(total), early, lever, deaths:deathComplete ? deaths : null,
      pillars:totals.map((value,i) => ({name:NAMES[i], value, weight:WEIGHTS[archetype][i]})),
      limits:{activityMinutes:observed, objectiveEvents:objectives.length, exactObjectiveStart:false}};
  }
  function consistency(results) {
    const latest = results.find(row => row && [420,440].includes(row.queue));
    if (!latest) return null;
    const sample = results.filter(row => row?.role === latest.role && row.queue === latest.queue &&
      row.version === VERSION && Number.isFinite(row.total) && Number.isFinite(row.early))
      .sort((a,b) => b.start-a.start).slice(0,15);
    if (sample.length < 5) return {count:sample.length, role:latest.role, queue:latest.queue};
    const points = sample.map(row => row.total).sort((a,b) => a-b);
    const floor = mean(points.slice(0,Math.ceil(points.length*.30)));
    const ceiling = mean(points.slice(-Math.ceil(points.length*.25)));
    const gap = ceiling-floor;
    const avgEarly = mean(sample.map(row => row.early));
    const sd = Math.sqrt(mean(sample.map(row => (row.early-avgEarly)**2)));
    const routine = clamp(100-sd*3.5);
    const sessions = [];
    for (const row of [...sample].reverse()) {
      const current = sessions.at(-1);
      const last = current?.at(-1);
      if (!last || row.start-last.start-last.duration*1000 >= 45*60000) sessions.push([row]);
      else current.push(row);
    }
    const session = sessions.at(-1);
    const last = session.at(-1), previous = session.at(-2);
    const fatigue = session.length >= 3 && mean(session.slice(0,2).map(r => r.total)) -
      mean(session.slice(2).map(r => r.total)) > 10 || Boolean(previous && previous.win === false && previous.total-last.total > 12);
    return {count:sample.length, role:latest.role, queue:latest.queue, floor, ceiling, gap, sd, routine,
      index:.5*floor+.3*routine+.2*clamp(100-2*gap), fatigue, sample:[...sample].reverse()};
  }
  function outcome(result) {
    if (result?.total == null || typeof result.win !== "boolean") return "";
    return result.total >= 68 ? result.win ? "Sieg mit hohem Prozesswert" : "Hoher Prozesswert trotz Niederlage"
      : result.win ? "Sieg mit Lernpotenzial" : "Niederlage mit Lernpotenzial";
  }
  root.MoosiCoaching = {VERSION, analyze, consistency, outcome};
})(typeof module === "object" ? module.exports : globalThis);
