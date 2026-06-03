<script setup lang="ts">
import { ref } from "vue";
import Sidebar from "./components/Sidebar.vue";
import ChatPanel from "./components/ChatPanel.vue";
import CitationDrawer from "./components/CitationDrawer.vue";
import IngestionPreview from "./components/IngestionPreview.vue";
import { state, errorBus } from "./composables/store";
import type { Citation } from "./types";

const drawerChunkId = ref<number | null>(null);
const previewDocId = ref<number | null>(null);

function onCiteClick(c: Citation) {
  drawerChunkId.value = c.chunk_id;
}

function openPreview() {
  if (state.activeDocumentId != null) {
    previewDocId.value = state.activeDocumentId;
  }
}
</script>

<template>
  <div class="h-screen flex">
    <Sidebar />
    <main class="flex-1 flex flex-col bg-bg-deep min-w-0">
      <div class="flex-1 min-h-0 relative">
        <ChatPanel @cite-click="onCiteClick" />
        <button v-if="state.activeDocumentId != null"
                class="absolute top-3 right-6 text-[11px] font-m bg-bg-surface border border-border rounded px-3 py-1 text-text3 hover:text-accent hover:border-accent transition-colors"
                @click="openPreview">
          查看文档解析
        </button>
      </div>
    </main>

    <CitationDrawer :chunk-id="drawerChunkId" @close="drawerChunkId = null" />
    <IngestionPreview :document-id="previewDocId" @close="previewDocId = null" />

    <div v-if="errorBus" class="fixed bottom-4 right-4 bg-red/15 border border-red/40 text-red px-4 py-2 rounded text-xs font-m z-[60]"
         @click="errorBus = null">
      {{ errorBus }}
    </div>
  </div>
</template>
