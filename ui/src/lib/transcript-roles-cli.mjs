// stdin {rows:[{text, first, spawnParent, session}]} -> stdout [{r, a2a, why}]: lets Python use the same classifier.
import { classifyUserRow, isHumanFacing } from "./transcript-roles.mjs";
let buf = "";
process.stdin.on("data", c => (buf += c));
process.stdin.on("end", () => {
  const { rows = [] } = JSON.parse(buf || "{}");
  process.stdout.write(JSON.stringify(rows.map(x => classifyUserRow(x.text, { first: !!x.first, spawnParent: x.spawnParent || null, humanFacing: isHumanFacing(x.session), busSender: x.busSender || null }))));
});
