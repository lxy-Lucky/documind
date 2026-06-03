import type { Folder, Document, ChatMessage, Citation, ProgressEvent } from "@/types";
import { fetchEventSource } from "@microsoft/fetch-event-source";

const BASE = ""; // proxied via vite

async function http<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(BASE + path, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!r.ok) throw new Error(`${r.status}: ${await r.text()}`);
  return r.json();
}

// ---- folders ----
export const listFolders = () => http<Folder[]>("/api/folders");
export const createFolder = (name: string, color = "amber") =>
  http<Folder>("/api/folders", { method: "POST", body: JSON.stringify({ name, color }) });
export const updateFolder = (id: number, name: string, color: string) =>
  http<Folder>(`/api/folders/${id}`, { method: "PATCH", body: JSON.stringify({ name, color }) });
export const deleteFolder = (id: number) =>
  http(`/api/folders/${id}`, { method: "DELETE" });

// ---- documents ----
export const listDocuments = (folder_id?: number) =>
  http<Document[]>(`/api/documents${folder_id != null ? `?folder_id=${folder_id}` : ""}`);
export const getDocument = (id: number) => http<any>(`/api/documents/${id}`);
export const listChunks = (doc_id: number, sheet_id?: number) =>
  http<any[]>(`/api/documents/${doc_id}/chunks${sheet_id != null ? `?sheet_id=${sheet_id}` : ""}`);
export const getChunk = (id: number) => http<any>(`/api/documents/chunk/${id}`);
export const deleteDocument = (id: number) =>
  http(`/api/documents/${id}`, { method: "DELETE" });

export const screenshotUrl = (sheet_id: number) =>
  `/api/documents/sheet/${sheet_id}/screenshot`;

// ---- upload ----
export async function uploadDocument(file: File, folder_id: number) {
  const fd = new FormData();
  fd.append("file", file);
  fd.append("folder_id", String(folder_id));
  const r = await fetch("/api/documents/upload", { method: "POST", body: fd });
  if (!r.ok) throw new Error(`${r.status}: ${await r.text()}`);
  return r.json() as Promise<{ status: string; filename: string }>;
}

export function subscribeProgress(
  onEvent: (evt: ProgressEvent) => void,
  onDone: (evt: ProgressEvent) => void,
  onError: (msg: string) => void,
  document_id?: number,
) {
  const ctrl = new AbortController();
  const url = `/api/documents/progress/stream${document_id != null ? `?document_id=${document_id}` : ""}`;
  fetchEventSource(url, {
    signal: ctrl.signal,
    openWhenHidden: true,
    async onopen(resp) {
      if (!resp.ok) {
        throw new Error(`progress stream ${resp.status}: ${await resp.text()}`);
      }
    },
    onmessage(ev) {
      let data: any = {};
      try { data = JSON.parse(ev.data); } catch {}
      if (ev.event === "done") {
        onDone(data);
        ctrl.abort();
      } else if (ev.event === "error") {
        onError(ev.data || "ingestion error");
        ctrl.abort();
      } else {
        onEvent({
          stage: ev.event || data.stage || "progress",
          message: data.message || "",
          current: data.current || 0,
          total: data.total || 0,
          extra: data.extra,
        });
      }
    },
    onerror(err) {
      onError(String(err));
      throw err;
    },
  });
  return () => ctrl.abort();
}

// ---- chat ----
export function streamChat(
  question: string,
  scope: { folder_id?: number | null; document_id?: number | null },
  callbacks: {
    onHits: (cites: Citation[]) => void;
    onToken: (delta: string) => void;
    onConfidence: (c: number | null) => void;
    onDone: () => void;
    onError: (msg: string) => void;
  },
) {
  const ctrl = new AbortController();
  fetchEventSource("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      question,
      folder_id: scope.folder_id ?? null,
      document_id: scope.document_id ?? null,
    }),
    signal: ctrl.signal,
    openWhenHidden: true,        // don't pause if tab loses focus
    async onopen(resp) {
      if (!resp.ok) {
        throw new Error(`chat opened with ${resp.status}: ${await resp.text()}`);
      }
    },
    onmessage(ev) {
      if (ev.event === "hits") {
        try { callbacks.onHits(JSON.parse(ev.data)); } catch {}
      } else if (ev.event === "token") {
        callbacks.onToken(ev.data);
      } else if (ev.event === "confidence") {
        const v = ev.data ? parseInt(ev.data, 10) : null;
        callbacks.onConfidence(Number.isFinite(v as number) ? (v as number) : null);
      } else if (ev.event === "done") {
        callbacks.onDone();
        ctrl.abort();
      } else if (ev.event === "error") {
        callbacks.onError(ev.data);
        ctrl.abort();
      }
    },
    onerror(err) {
      // Returning a value from onerror suppresses the default retry loop.
      callbacks.onError(String(err));
      ctrl.abort();
      throw err;   // ← throwing prevents the library from retrying
    },
  });
  return () => ctrl.abort();
}
