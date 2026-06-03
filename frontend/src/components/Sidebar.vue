<script setup lang="ts">
import { onMounted, ref } from "vue";
import * as api from "@/api/client";
import { state, errorBus } from "@/composables/store";
import type { Document, Folder } from "@/types";

const newFolderName = ref("");
const creating = ref(false);
const fileInput = ref<HTMLInputElement | null>(null);
const dragOver = ref(false);

const COLOR_CYCLE = ["amber", "green", "blue", "purple", "red"];

async function refresh() {
  state.folders = await api.listFolders();
  for (const f of state.folders) {
    state.documents[f.id] = await api.listDocuments(f.id);
  }
}
onMounted(refresh);

async function createFolder() {
  if (!newFolderName.value.trim()) return;
  const color = COLOR_CYCLE[state.folders.length % COLOR_CYCLE.length];
  await api.createFolder(newFolderName.value.trim(), color);
  newFolderName.value = "";
  creating.value = false;
  await refresh();
}

async function selectFolder(f: Folder) {
  state.activeFolderId = f.id;
  state.activeDocumentId = null;
  state.contextMode = { kind: "folder", id: f.id };
}

function selectDoc(d: Document) {
  state.activeFolderId = d.folder_id;
  state.activeDocumentId = d.id;
  state.contextMode = { kind: "document", id: d.id };
}

function selectAll() {
  state.contextMode = { kind: "all" };
  state.activeFolderId = null;
  state.activeDocumentId = null;
}

async function delFolder(f: Folder, ev: Event) {
  ev.stopPropagation();
  if (!confirm(`删除文件夹 "${f.name}" 及其全部文档？`)) return;
  await api.deleteFolder(f.id);
  await refresh();
}

async function delDoc(d: Document, ev: Event) {
  ev.stopPropagation();
  if (!confirm(`删除文档 "${d.filename}"？`)) return;
  await api.deleteDocument(d.id);
  await refresh();
}

function openFileDialog() {
  fileInput.value?.click();
}

async function onFileSelected(ev: Event) {
  const inp = ev.target as HTMLInputElement;
  if (!inp.files || inp.files.length === 0) return;
  await handleFiles(Array.from(inp.files));
  inp.value = "";
}

async function handleFiles(files: File[]) {
  if (state.activeFolderId == null) {
    if (state.folders.length === 0) {
      const f = await api.createFolder("默认文件夹");
      state.folders = await api.listFolders();
      state.activeFolderId = f.id;
    } else {
      state.activeFolderId = state.folders[0].id;
    }
  }
  state.uploading = true;
  for (const file of files) {
    await api.uploadDocument(file, state.activeFolderId);
    await new Promise<void>((resolve) => {
      let docId: number | undefined;
      api.subscribeProgress(
        (evt) => {
          state.ingestProgress = {
            stage: evt.stage,
            current: evt.current,
            total: evt.total,
            message: evt.message,
          };
          if (evt.stage === "started" && evt.extra?.document_id) {
            docId = evt.extra.document_id;
          }
        },
        () => {
          state.ingestProgress = null;
          resolve();
        },
        (e) => {
          errorBus.value = "上传失败: " + e;
          state.ingestProgress = null;
          resolve();
        },
      );
    });
    await refresh();
  }
  state.uploading = false;
}

function onDrop(ev: DragEvent) {
  ev.preventDefault();
  dragOver.value = false;
  if (ev.dataTransfer?.files) handleFiles(Array.from(ev.dataTransfer.files));
}

function totalChunks(): number {
  return state.folders.reduce((acc, f) => acc + (f.chunk_count || 0), 0);
}
function totalDocs(): number {
  return state.folders.reduce((acc, f) => acc + (f.doc_count || 0), 0);
}
</script>

<template>
  <aside class="w-[310px] flex flex-col bg-bg-main border-r border-border relative overflow-hidden">
    <!-- header -->
    <div class="px-5 pt-6 pb-4 border-b border-border flex-shrink-0">
      <div class="flex items-center gap-3">
        <div class="w-9 h-9 rounded-lg flex items-center justify-center font-d font-bold text-bg-deep shadow-glow"
             style="background: linear-gradient(135deg, #e4a853, #c48a3a)">D</div>
        <div>
          <div class="font-d text-[17px] font-bold tracking-tight">DocuMind</div>
          <div class="text-[10px] text-text3 font-m tracking-wider mt-px">LOCAL KNOWLEDGE</div>
        </div>
      </div>
    </div>

    <!-- upload -->
    <div
      class="mx-3.5 mt-3.5 border border-dashed rounded p-5 text-center cursor-pointer transition-all relative overflow-hidden flex-shrink-0"
      :class="dragOver ? 'border-accent bg-bg-surface shadow-soft' : 'border-border-light hover:border-accent hover:bg-bg-surface'"
      @click="openFileDialog"
      @dragover.prevent="dragOver = true"
      @dragleave="dragOver = false"
      @drop="onDrop"
    >
      <div class="w-10 h-10 mx-auto mb-2.5 rounded-full bg-bg-elevated flex items-center justify-center">
        <svg class="w-[18px] h-[18px] stroke-accent" fill="none" viewBox="0 0 24 24" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M17 8l-5-5-5 5M12 3v12"/>
        </svg>
      </div>
      <div class="text-xs font-semibold mb-1">点击或拖拽上传 Excel</div>
      <div class="text-[10px] text-text3 font-m">.xlsx</div>
      <input type="file" ref="fileInput" accept=".xlsx" multiple class="hidden" @change="onFileSelected" />
    </div>

    <!-- ingest progress -->
    <div v-if="state.ingestProgress" class="mx-3.5 mt-2 text-[10px] font-m text-accent">
      [{{ state.ingestProgress.stage }}] {{ state.ingestProgress.message }}
      <span v-if="state.ingestProgress.total > 0">
        ({{ state.ingestProgress.current }}/{{ state.ingestProgress.total }})
      </span>
    </div>

    <!-- stats -->
    <div class="flex gap-1.5 px-3.5 pt-3 pb-2 flex-shrink-0">
      <div class="flex items-center gap-1.5 text-[10px] font-m text-text3 bg-bg-surface px-2.5 py-0.5 rounded-full border border-border">
        <span class="w-1.5 h-1.5 rounded-full bg-green shadow-[0_0_5px_rgba(90,212,166,0.4)]"></span>
        {{ totalDocs() }} 文档
      </div>
      <div class="flex items-center gap-1.5 text-[10px] font-m text-text3 bg-bg-surface px-2.5 py-0.5 rounded-full border border-border">
        <span class="w-1.5 h-1.5 rounded-full bg-accent shadow-[0_0_5px_rgba(228,168,83,0.22)]"></span>
        {{ totalChunks() }} 片段
      </div>
    </div>

    <!-- folders -->
    <div class="flex-1 overflow-y-auto px-2 pb-5 flex flex-col">
      <div class="flex items-center justify-between px-3 pt-2.5 pb-1.5">
        <div class="text-[10px] font-m text-text3 tracking-wider uppercase">Folders</div>
        <button class="w-6 h-6 rounded border border-border bg-bg-surface text-text3 hover:border-accent hover:text-accent transition-colors text-sm leading-none"
                @click="creating = true">+</button>
      </div>

      <!-- "all" pseudo-folder -->
      <div
        class="px-3 py-2 rounded-sm cursor-pointer flex items-center gap-2 transition-colors"
        :class="state.contextMode.kind === 'all' ? 'bg-bg-elevated text-accent' : 'hover:bg-bg-surface'"
        @click="selectAll"
      >
        <span class="text-[11px] font-m">∀ 全部</span>
      </div>

      <div v-if="creating" class="mt-2 mx-2 flex gap-1.5">
        <input v-model="newFolderName" class="flex-1 bg-bg-surface text-text1 rounded px-2 py-1 text-xs outline-none border border-border focus:border-accent"
               placeholder="文件夹名" maxlength="20" @keyup.enter="createFolder" @keyup.escape="creating = false" />
        <button class="px-2 text-xs text-accent" @click="createFolder">✓</button>
        <button class="px-2 text-xs text-text3" @click="creating = false">×</button>
      </div>

      <div v-for="f in state.folders" :key="f.id" class="mb-0.5 mt-1">
        <div
          class="px-2.5 py-2 rounded-sm cursor-pointer flex items-center gap-1.5 transition-colors group relative"
          :class="state.activeFolderId === f.id ? 'bg-bg-elevated' : 'hover:bg-bg-surface'"
          @click="selectFolder(f)"
        >
          <div v-if="state.activeFolderId === f.id"
               class="absolute left-0 top-1/2 -translate-y-1/2 w-[3px] h-[55%] rounded-r bg-accent"></div>
          <span class="text-text2 text-sm">▸</span>
          <span class="flex-1 text-[13px]">{{ f.name }}</span>
          <span class="text-[10px] font-m text-text3">{{ f.doc_count }}</span>
          <button class="opacity-0 group-hover:opacity-100 text-text3 hover:text-red text-xs px-1"
                  @click="delFolder(f, $event)">×</button>
        </div>

        <!-- documents -->
        <div v-if="state.activeFolderId === f.id" class="pl-3 mt-1">
          <div v-for="d in state.documents[f.id] || []" :key="d.id"
               class="px-2.5 py-1.5 rounded-sm cursor-pointer flex items-center gap-2 transition-colors group"
               :class="state.activeDocumentId === d.id ? 'bg-bg-elevated' : 'hover:bg-bg-surface'"
               @click="selectDoc(d)">
            <div class="w-7 h-7 rounded-sm flex items-center justify-center text-[10px] font-m font-bold"
                 :class="{
                   'bg-green/10 text-green': d.status === 'ready',
                   'bg-accent/10 text-accent': d.status === 'parsing',
                   'bg-red/10 text-red': d.status === 'failed',
                 }">
              {{ d.filename.split('.').pop()?.toUpperCase() }}
            </div>
            <div class="flex-1 min-w-0">
              <div class="text-[12px] truncate">{{ d.filename }}</div>
              <div class="text-[10px] font-m text-text3">{{ d.status }} · {{ d.chunk_count }} 片段</div>
            </div>
            <button class="opacity-0 group-hover:opacity-100 text-text3 hover:text-red text-xs"
                    @click="delDoc(d, $event)">×</button>
          </div>
        </div>
      </div>
    </div>
  </aside>
</template>
