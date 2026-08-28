const fs = require("fs");
const s = fs.readFileSync("frontend/app/desk/[deskId]/page.tsx", "utf8");
const lines = s.split("\n");
lines.forEach((ln, i) => {
  if (ln.includes("replace")) {
    const cps = [...ln].map(c => c.codePointAt(0) > 127 ? "U+" + c.codePointAt(0).toString(16).toUpperCase() + "(" + c + ")" : c).join("");
    console.log(i + 1, JSON.stringify(ln.trim()));
  }
});
// find the char on the line following each ".replace(" ending with newline
const re = /\.replace\(\s*"(.)"\s*,/gs;
let m;
while ((m = re.exec(s)) !== null) {
  console.log("MATCH arg:", JSON.stringify(m[1]), "U+" + m[1].codePointAt(0).toString(16).toUpperCase(), "at", m.index);
}
