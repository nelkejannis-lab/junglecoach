import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";
const html = fs.readFileSync(new URL("../../jc/static/index.html", import.meta.url), "utf8");
const mobile = fs.readFileSync(new URL("../public/m/index.html", import.meta.url), "utf8");
function between(source, a, b) {
  const start = source.indexOf(a), end = source.indexOf(b, start);
  assert.ok(start >= 0 && end > start);
  return source.slice(start, end);
}
test("desktop leaderboard includes own account even after another profile was searched", () => {
  const context = {friendsData: {me: {me: true, riot_id: "Me#EUW", loading: true}}, state: {}, cfgData: {},
    profQuery: "Other#EUW", profData: {riot_id: "Other#EUW"}, frSort: "score",
    esc: String, rankVal: () => 0, paCol: () => "white", rankTxt: () => "rank"};
  vm.createContext(context);
  vm.runInContext(between(html, "function friendsRanking(", "function renderFriendDetail("), context);
  const rendered = context.friendsRanking([]);
  assert.match(rendered, /Me/);
  assert.match(rendered, /\(du\)/);
  assert.doesNotMatch(rendered, /Other/);
});
test("coach request failure becomes a visible error instead of endless loading", async () => {
  const context = {coachData: null, view: "coach", AbortController, setTimeout, clearTimeout,
    render() {}, fetch: async () => ({ok: false, json: async () => ({error: "sample failure"})})};
  vm.createContext(context);
  vm.runInContext(between(html, "async function loadCoach(", "function renderCoach("), context);
  await context.loadCoach();
  assert.equal(context.coachData.error, "sample failure");
  assert.equal(context.loadCoach.busy, false);
});
test("desktop and mobile averages use identical decimal rounding", () => {
  for (const source of [html, mobile]) {
    const fn = between(source, "const moosiScore = ", "\n");
    const context = {MS_N: 10};
    vm.createContext(context);
    vm.runInContext(fn + "\nglobalThis.average = moosiScore;", context);
    assert.equal(context.average([6.2, 6.3]), 6.3);
    assert.equal(context.average([null, 5, 7]), 6);
  }
});
test("mobile leaderboard uses the same twenty-match candidate window for self and friends", () => {
  const fn = between(mobile, "function leaderboard(", "function vFriends(");
  const seen = [];
  const context = {S: {me: {riot_id: "Me#EUW"}, games: Array(40).fill({}), friends: ["Other#EUW"],
      fdata: {"Other#EUW": {games: Array(40).fill({})}}, lbm: "score"},
    solo: () => null, agg: games => {seen.push(games.length); return {score: 5};},
    rankScore: () => 0, LBM: {score: ["Score", e => e.a.score, e => e.a.score]}};
  vm.createContext(context);
  vm.runInContext(fn, context);
  context.leaderboard();
  assert.deepEqual(seen, [20,20]);
});

