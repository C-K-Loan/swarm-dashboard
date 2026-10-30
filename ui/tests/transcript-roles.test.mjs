import test from "node:test";
import assert from "node:assert/strict";
import { classifyUserRow, reclassifyTranscript, isHumanFacing, busSenderFor } from "../src/lib/transcript-roles.mjs";

const role = (text, opts) => classifyUserRow(text, opts);

test("CKL's own messages and actions stay human", () => {
  for (const t of ["ok impress now get in IN AAZEROTH", "yo PC froze are we still good?", "[Request interrupted by user]", "<command-name>/compact</command-name>", "<bash-input>ls</bash-input>", '<pasted_content id="1"> I want a World Of Warcraft classic X Call Of Duty game!'])
    assert.equal(role(t).r, "u", t);
});

test("harness events are not human", () => {
  for (const t of ["<task-notification> <task-id>b1</task-id>", "[heartbeat] fleet check: cct who gamejam", "<turn_aborted> The user interrupted", "[SYSTEM NOTIFICATION - NOT USER INPUT] background", "[Image: original 2560x2160, displayed at 2000x1688.]", "[from cct-harness-system] YO you haven't updated state"])
    assert.equal(role(t).r, "sys", t);
});

test("agent messages carry their sender", () => {
  assert.deepEqual([role('<pasted_content id="f9"> [from gamejam-nuke] DONE + STOPPING').r, role('<pasted_content id="f9"> [from gamejam-nuke] DONE').a2a], ["x", "gamejam-nuke"]);
  assert.equal(role("[tester] GPU HAZARD NOW: kernel logged").a2a, "tester");
  assert.equal(role("[harness-dev -> cct-manager] ACK #45").a2a, "harness-dev");
  assert.equal(role("[master, no reply needed] Disk check").a2a, "master");
  assert.equal(role("[tester/gpu-watchdog] TERMed agent").a2a, "tester");
  assert.equal(role("[Image #17][from infra-agent@workstation] FINAL SPEC").a2a, "infra-agent@workstation");
  assert.equal(role("[Image #3] can u tweak this").r, "u");
});

test("spawn briefs are labeled as briefs, with the spawner when known", () => {
  assert.equal(role("You are a research agent. Research ONLY; do not write to any repo.").r, "spawn");
  assert.equal(role("You are gamejam-wowfmt-dev, a DEV agent for ONE crate").r, "spawn");
  assert.deepEqual(role('<pasted_content id="65"> [from gamejam-master] You are gamejam-grind-dev, a DEV agent'), { r: "spawn", a2a: "gamejam-master", why: "[from X] brief" });
  assert.equal(role("[cct-manager BRIEF] You are harness-dev").a2a, "cct-manager");
  assert.deepEqual(role('<pasted_content id="de"> Project: WoWCoD, a native-Rust FPS', { first: true, spawnParent: "gamejam-master" }).a2a, "gamejam-master");
  assert.equal(role('<pasted_content id="de"> Project: WoWCoD', { first: false, spawnParent: "gamejam-master" }).r, "u");
  assert.equal(role("You are wrong about that, fix it").r, "u");
});

test("reclassify is idempotent and keeps the first user row stable", () => {
  const rows = { s: [{ r: "u", x: '<pasted_content id="1"> Project: X' }, { r: "a", x: "ok" }, { r: "u", x: '<pasted_content id="2"> more' }] };
  assert.equal(reclassifyTranscript(rows), 0);
  assert.equal(reclassifyTranscript(rows, () => "gamejam-master"), 1);
  assert.deepEqual(rows.s.map(r => r.r), ["spawn", "a", "u"]);
  assert.equal(reclassifyTranscript(rows, () => "gamejam-master"), 0);
});

test("outside a human-facing session, unmarked input is never human", () => {
  assert.deepEqual(role("please rebuild the terrain", { humanFacing: false }), { r: "x", a2a: "unknown sender", why: "not a human-facing session" });
  assert.equal(role("please rebuild the terrain", { humanFacing: true }).r, "u");
  assert.equal(role("please rebuild the terrain").r, "u");
  assert.deepEqual(role("go", { humanFacing: false, first: true, spawnParent: "gamejam-master" }).a2a, "gamejam-master");
  assert.equal(role("[Request interrupted by user]", { humanFacing: null }).r, "u");
  assert.deepEqual(role("ship it", { humanFacing: true, busSender: "gamejam-tester" }), { r: "x", a2a: "gamejam-tester", why: "bus delivery" });
  assert.equal(isHumanFacing("gamejam-master"), true);
  assert.equal(isHumanFacing("gamejam-dev"), false);
  assert.equal(isHumanFacing("claude-gamejam-736c6a66"), false);
  assert.equal(isHumanFacing("cct-manager"), null);
});

test("bus rows attribute bare cct sends; human/ckl bus rows do not", () => {
  const bus = [{ ts: 100, from: "ckl", prefix: "hello there" }, { ts: 150, from: "gamejam-master", prefix: "GPU RULE effective now: at most one renderer" }];
  assert.equal(busSenderFor(bus, 200, "<pasted_content id=\"1\">\nGPU RULE effective now: at most one"), "gamejam-master");
  assert.equal(busSenderFor(bus, 120, "hello there"), null);
  assert.equal(busSenderFor(bus, 900, "GPU RULE effective now"), null);
});

test("reclassify upgrades unknown senders when the bus arrives and stays idempotent", () => {
  const rows = { s1: [{ r: "u", x: "first" }, { r: "u", x: "GPU RULE effective now" }] };
  const opts = { humanFacing: () => false, busSender: () => null };
  reclassifyTranscript(rows, () => null, opts);
  assert.deepEqual(rows.s1.map(r => r.a2a), ["unknown sender", "unknown sender"]);
  assert.equal(reclassifyTranscript(rows, () => null, { ...opts, busSender: (sid, r) => r.x.startsWith("GPU") ? "gamejam-master" : null }), 1);
  assert.equal(rows.s1[1].a2a, "gamejam-master");
  assert.equal(reclassifyTranscript(rows, () => null, { ...opts, busSender: (sid, r) => r.x.startsWith("GPU") ? "gamejam-master" : null }), 0);
});

test("pane actions and stubbed cct prefixes in agent sessions are not human", () => {
  assert.equal(role("[Request interrupted by user for tool use]", { humanFacing: false }).r, "sys");
  assert.equal(role("[Request interrupted by user]", { humanFacing: true }).r, "u");
  assert.equal(role("/model gpt-6.1-sol", { humanFacing: false }).r, "sys");
  assert.equal(role("/model gpt-6-luna[from gamejam-master@workstation] Read and execute brief.txt", { humanFacing: false }).a2a, "gamejam-master@workstation");
  assert.equal(role("continue[from gamejam-master@workstation] Codex quota is back", { humanFacing: false }).a2a, "gamejam-master@workstation");
  assert.equal(role("y[from gamejam-codex-source] 1915 READY", { humanFacing: true }).a2a, "gamejam-codex-source");
  assert.equal(role("I said earlier in the chat that [from X] style is weird", { humanFacing: true }).r, "u");
  assert.equal(isHumanFacing("codex-gamejam-49eed4dc"), true);
});
