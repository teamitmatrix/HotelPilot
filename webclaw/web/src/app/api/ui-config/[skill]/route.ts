import { NextRequest, NextResponse } from "next/server";
import * as fs from "fs";
import * as path from "path";
import { fileURLToPath } from "url";
import * as yaml from "js-yaml";

/**
 * PMS workspace root: prefer PMS_ROOT, then walk from this file / cwd until
 * webclaw/ + erpclaw-engine/ are found. Avoids process.cwd()-only resolution
 * which breaks when Next is started from another directory or standalone output.
 */
function pmsRoot(): string {
  const fromEnv = process.env.PMS_ROOT?.trim();
  if (fromEnv) {
    return path.resolve(fromEnv);
  }

  const markers = ["webclaw", "erpclaw-engine"] as const;
  const isPms = (root: string) =>
    markers.every((name) => fs.existsSync(path.join(root, name)));

  // route.ts → [skill] → ui-config → api → app → src → web → webclaw → pms (7 up)
  const fromFile = path.resolve(
    path.dirname(fileURLToPath(import.meta.url)),
    "..",
    "..",
    "..",
    "..",
    "..",
    "..",
    "..",
  );
  if (isPms(fromFile)) {
    return fromFile;
  }

  let cursor = path.resolve(process.cwd());
  for (let i = 0; i < 8; i++) {
    if (isPms(cursor)) {
      return cursor;
    }
    const parent = path.dirname(cursor);
    if (parent === cursor) {
      break;
    }
    cursor = parent;
  }

  // Last resort: historical layout when cwd is webclaw/web
  return path.resolve(process.cwd(), "..", "..");
}

function resolveEngineDir(root: string): string {
  const engine = process.env.ERPCLAW_ENGINE_PATH || path.join(root, "erpclaw-engine");
  return path.isAbsolute(engine) ? path.normalize(engine) : path.resolve(root, engine);
}

function resolveSkillDir(skill: string): string {
  const root = pmsRoot();
  const engineAbs = resolveEngineDir(root);
  if (skill === "erpclaw") return engineAbs;
  return path.join(root, skill);
}

// Backend API URL (FastAPI on same server)
const API_URL = process.env.API_URL || "http://127.0.0.1:8001";

export async function GET(
  _request: NextRequest,
  { params }: { params: Promise<{ skill: string }> }
) {
  const { skill } = await params;

  // Sanitize skill name
  if (!/^[a-z0-9-]+$/.test(skill)) {
    return NextResponse.json({ error: "Invalid skill name" }, { status: 400 });
  }

  const skillDir = resolveSkillDir(skill);
  let yamlPath = path.join(skillDir, "UI.yaml");

  if (!fs.existsSync(yamlPath)) {
    const engineAbs = resolveEngineDir(pmsRoot());
    const modulesPath = path.join(engineAbs, "modules", skill, "UI.yaml");
    if (fs.existsSync(modulesPath)) {
      yamlPath = modulesPath;
    }
  }

  // Path 1: Hand-written UI.yaml exists — serve it directly
  if (fs.existsSync(yamlPath)) {
    try {
      const content = fs.readFileSync(yamlPath, "utf-8");
      const parsed = yaml.load(content);
      return NextResponse.json(parsed, {
        headers: {
          "Cache-Control": "public, max-age=60, stale-while-revalidate=300",
        },
      });
    } catch {
      return NextResponse.json(
        { error: "Failed to parse UI.yaml" },
        { status: 500 }
      );
    }
  }

  // Path 2: No UI.yaml — ask backend to auto-generate from SKILL.md
  try {
    const res = await fetch(`${API_URL}/api/v1/schema/ui-config/${skill}`, {
      headers: { "Accept": "application/json" },
      // Short timeout — generation should be fast (SKILL.md parsing + inference)
      signal: AbortSignal.timeout(10_000),
    });

    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      return NextResponse.json(
        { error: body.error || `UI generation failed (${res.status})`, generated: false },
        { status: res.status }
      );
    }

    const config = await res.json();
    return NextResponse.json(config, {
      headers: {
        "Cache-Control": "public, max-age=300, stale-while-revalidate=600",
      },
    });
  } catch (e) {
    return NextResponse.json(
      { error: "Could not generate UI config", skill },
      { status: 404 }
    );
  }
}
