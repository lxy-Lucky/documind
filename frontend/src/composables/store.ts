import { reactive, ref } from "vue";
import type { Folder, Document, ChatMessage, ContextMode } from "@/types";

export const state = reactive({
  folders: [] as Folder[],
  documents: {} as Record<number, Document[]>, // by folder_id
  activeFolderId: null as number | null,
  activeDocumentId: null as number | null,
  contextMode: { kind: "all" } as ContextMode,
  messages: [] as ChatMessage[],
  uploading: false,
  ingestProgress: null as null | { stage: string; current: number; total: number; message: string },
  drawerChunkId: null as number | null,
});

export const errorBus = ref<string | null>(null);
