const fs = require("fs");
const files = [
  "frontend/lib/format.ts",
  "frontend/app/desk/[deskId]/page.tsx",
  "frontend/app/close/[deskId]/page.tsx",
];
for (const f of files) {
  const s = fs.readFileSync(f, "utf8");
  console.log(f);
  let i = 0;
  while ((i = s.indexOf(".replace(", i)) !== -1) {
    const seg = s.slice(i + 9, i + 12);
    const chars = [...seg.slice(1, 2)];
    console.log("  arg char:", JSON.stringify(chars[0]), "U+" + chars[0].codePointAt(0).toString(16).toUpperCase());
    i += 9;
  }
  // also: how does money() emit negatives?
}
const fmt = fs.readFileSync("frontend/lib/format.ts", "utf8");
const m = fmt.match(/n < 0 \? `(.)\$/);
console.log("format.ts negative prefix:", JSON.stringify(m ? m[1] : null), m ? "U+" + m[1].codePointAt(0).toString(16).toUpperCase() : "");
