import test from "node:test";
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import vm from "node:vm";

const local = new URL("../../jc/static/",import.meta.url);
const web = new URL("../public/m/",import.meta.url);
const context = vm.createContext({});
vm.runInContext(readFileSync(new URL("coaching-score.js",local),"utf8"),context);
const coach = context.MoosiCoaching;
function game({name="Graves", win=false, dangerous=false}={}) {
  const me = {participantId:1,puuid:"me",teamPosition:"JUNGLE",teamId:100,
    championName:name, win, kills:2,assists:4,deaths:dangerous?1:0,goldEarned:12000,
    totalDamageDealtToChampions:18000,timeCCingOthers:70};
  const rival = {...me,participantId:2,puuid:"them",teamId:200};
  const frames = Array.from({length:26},(_,i)=>({timestamp:i*60000, events:[],
    participantFrames:{1:{minionsKilled:0,jungleMinionsKilled:i*8,totalGold:500+i*390,xp:i*500,currentGold:500,
      damageStats:{totalDamageDoneToChampions:i*500}}}}));
  frames[6].events.push({type:"LEVEL_UP",participantId:1,level:6,timestamp:330000},
    {type:"LEVEL_UP",participantId:2,level:6,timestamp:350000});
  frames[15].events.push({type:"WARD_PLACED",creatorId:1,timestamp:870000});
  frames[16].events.push({type:"ELITE_MONSTER_KILL",killerId:1,killerTeamId:100,monsterType:"DRAGON",timestamp:920000});
  if (dangerous) {
    frames[20].events.push({type:"CHAMPION_KILL",killerId:2,victimId:1,shutdownBounty:450,timestamp:1180000});
    frames[21].events.push({type:"ELITE_MONSTER_KILL",killerId:2,killerTeamId:200,monsterType:"BARON_NASHOR",timestamp:1220000});
  }
  const info = {queueId:420,mapId:11,gameDuration:1500,gameCreation:1,participants:[me,rival]};
  return {info,timeline:{info:{frames}}};
}
const result = args => { const {info,timeline} = game(args); return coach.analyze(info,timeline,"me"); };
test("same process score on a win and a loss",()=>{
  const loss = result({win:false}), win = result({win:true});
  assert.ok(loss.total >= 68);
  assert.equal(win.total,loss.total);
  assert.match(coach.outcome(loss),/trotz Niederlage/);
});
test("an objective-related death supplies a replay timestamp without asserting a causal throw",()=>{
  const scored = result({win:true,dangerous:true});
  assert.equal(scored.lever.time,1180000);
  assert.equal(scored.deaths[0].nearbyObjective,"BARON_NASHOR");
  assert.equal(scored.deaths[0].shutdown,450);
  assert.ok(scored.total < result({win:true}).total);
});
test("a reported spawn requires a version match and supports a contextual turning point",()=>{
  const {info,timeline} = game({win:true,dangerous:true});
  info.gameVersion = "16.23.123.456";
  for (let i=3; i<=10; i++) info.participants.push({participantId:i,teamId:i<=6 ? 100 : 200});
  const before = timeline.info.frames[19].participantFrames;
  for (const p of info.participants) before[p.participantId] = {...before[p.participantId],totalGold:p.teamId===100?9000:8000};
  const config = JSON.parse(readFileSync(new URL("patch_config.json",local),"utf8"));
  const report = coach.analyze(info,timeline,"me",config);
  assert.equal(report.deaths[0].plannedSpawn,1200000);
  assert.equal(report.deaths[0].turningPoint,true);
  assert.equal(report.deaths[0].weight,3);
  assert.equal(coach.analyze({...info,gameVersion:"16.22"},timeline,"me",config).deaths[0].plannedSpawn,null);
});
test("missing frames preserve a partial result and champion archetypes carry different weights",()=>{
  const {info,timeline} = game({name:"Amumu"});
  timeline.info.frames[4].participantFrames = {};
  const scored = coach.analyze(info,timeline,"me");
  assert.equal(scored.archetype,"tank");
  assert.equal(scored.pillars[0].weight,20);
  assert.equal(scored.total,null);
});
test("15-game report averages the weakest five and strongest four",()=>{
  const rows = Array.from({length:15},(_,i)=>({version:1,start:(i+1)*8000000,duration:1500,queue:420,role:"JUNGLE",early:70,total:50+i,win:true}));
  const report = coach.consistency(rows.reverse());
  assert.equal(report.floor,52);
  assert.equal(report.ceiling,62.5);
  assert.equal(report.routine,100);
  assert.equal(coach.consistency(rows.slice(0,4)).count,4);
});
test("Windows and mobile ship identical coaching scripts",()=>{
  for(const file of ["coaching-score.js","coaching-view.js","patch_config.json"]) assert.deepEqual(readFileSync(new URL(file,local)),readFileSync(new URL(file,web)));
});
