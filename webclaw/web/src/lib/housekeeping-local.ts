const NOTES_KEY = "hk-room-notes";
const IN_PROGRESS_KEY = "hk-in-progress";

function readJson<T>(key: string, fallback: T): T {
  if (typeof window === "undefined") return fallback;
  try {
    const raw = sessionStorage.getItem(key);
    if (!raw) return fallback;
    return JSON.parse(raw) as T;
  } catch {
    return fallback;
  }
}

function writeJson(key: string, value: unknown) {
  if (typeof window === "undefined") return;
  sessionStorage.setItem(key, JSON.stringify(value));
}

export function readHousekeepingNotes(): Record<string, string> {
  return readJson<Record<string, string>>(NOTES_KEY, {});
}

export function saveHousekeepingNote(roomId: string, note: string) {
  const trimmed = note.trim();
  const all = readHousekeepingNotes();
  if (!trimmed) {
    delete all[roomId];
  } else {
    all[roomId] = trimmed;
  }
  writeJson(NOTES_KEY, all);
}

export function readInProgressRoomIds(): Set<string> {
  const ids = readJson<string[]>(IN_PROGRESS_KEY, []);
  return new Set(ids);
}

export function persistInProgressRoomIds(ids: Set<string>) {
  writeJson(IN_PROGRESS_KEY, [...ids]);
}

export function setRoomInProgress(roomId: string, inProgress: boolean): Set<string> {
  const next = readInProgressRoomIds();
  if (inProgress) next.add(roomId);
  else next.delete(roomId);
  persistInProgressRoomIds(next);
  return next;
}
