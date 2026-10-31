/**
 * Next.js bundles http-proxy, which still calls Node's deprecated util._extend
 * (DEP0060) when proxying rewrites. Swap that export for Object.assign.
 * Idempotent: safe to run from postinstall, predev, and prebuild.
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const target = path.join(
  root,
  "node_modules",
  "next",
  "dist",
  "compiled",
  "http-proxy",
  "index.js",
);

const OLD =
  '837:e=>{"use strict";e.exports=require("util")}';
const NEXT =
  '837:e=>{"use strict";const u=require("util");const o=Object.create(u);Object.defineProperty(o,"_extend",{value:Object.assign});e.exports=o}';

if (!fs.existsSync(target)) {
  process.exit(0);
}

const source = fs.readFileSync(target, "utf8");
if (source.includes(NEXT)) {
  process.exit(0);
}
if (!source.includes(OLD)) {
  console.warn(
    "patch-next-http-proxy: bundled http-proxy layout changed; DEP0060 patch skipped",
  );
  process.exit(0);
}

fs.writeFileSync(target, source.replace(OLD, NEXT));
