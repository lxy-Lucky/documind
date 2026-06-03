<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import * as api from "@/api/client";

const props = defineProps<{ documentId: number | null }>();
const emit = defineEmits<{ (e: "close"): void }>();

const detail = ref<any>(null);
const chunks = ref<any[]>([]);
const activeSheetId = ref<number | null>(null);
const loading = ref(false);

async function load() {
  if (props.documentId == null) return;
  loading.value = true;
  try {
    detail.value = await api.getDocument(props.documentId);
    chunks.value = await api.listChunks(props.documentId);
    if (detail.value?.sheets?.length) {
      activeSheetId.value = detail.value.sheets[0].id;
    }
  } finally {
    loading.value = false;
  }
}
onMounted(load);
watch(() => props.documentId, load);

const filteredChunks = computed(() =>
  chunks.value.filter((c) => activeSheetId.value == null || c.sheet_id === activeSheetId.value),
);
</script>

<template>
  <transition name="fade">
    <div v-if="documentId != null" class="fixed inset-0 z-40 bg-bg-deep/90 flex">
      <div class="m-auto w-[90vw] h-[90vh] bg-bg-main border border-border rounded-xl overflow-hidden flex flex-col">
        <div class="px-6 py-4 border-b border-border flex items-center justify-between flex-shrink-0">
          <div>
            <div class="font-d font-bold">解析预览</div>
            <div class="text-[11px] font-m text-text3 mt-0.5">{{ detail?.filename }} · {{ detail?.status }}</div>
          </div>
          <button class="text-text3 hover:text-text1 text-xl" @click="emit('close')">×</button>
        </div>

        <div v-if="loading" class="flex-1 flex items-center justify-center text-text3">加载中...</div>

        <div v-else class="flex-1 flex overflow-hidden">
          <!-- sheet list -->
          <div class="w-64 border-r border-border overflow-y-auto p-2">
            <div v-for="s in detail?.sheets || []" :key="s.id"
                 class="px-3 py-2 rounded cursor-pointer text-[12px] mb-0.5 transition-colors"
                 :class="activeSheetId === s.id ? 'bg-bg-elevated text-accent' : 'hover:bg-bg-surface'"
                 @click="activeSheetId = s.id">
              <div class="font-medium truncate">{{ s.name }}</div>
              <div class="text-[10px] font-m text-text3 mt-0.5">
                {{ s.sheet_type }} · {{ (s.classifier_confidence * 100).toFixed(0) }}%
              </div>
            </div>
          </div>

          <!-- chunks -->
          <div class="flex-1 overflow-y-auto p-4">
            <div v-if="!filteredChunks.length" class="text-text3 text-sm text-center mt-12">该工作表没有产出 chunk</div>
            <div v-for="c in filteredChunks" :key="c.id" class="mb-4 bg-bg-surface border border-border rounded p-3">
              <div class="text-[10px] font-m text-text3 mb-1.5">
                #{{ c.id }} · {{ c.hierarchy_path || '-' }}
                <span v-if="c.chunk_metadata?.jira_tags?.length" class="text-accent ml-2">
                  [{{ c.chunk_metadata.jira_tags.join(',') }}]
                </span>
              </div>
              <pre class="text-[12px] text-text1 whitespace-pre-wrap font-m">{{ c.markdown || c.text }}</pre>
            </div>
          </div>

          <!-- sheet screenshot -->
          <div v-if="activeSheetId" class="w-[420px] border-l border-border overflow-y-auto p-4">
            <div class="text-[10px] font-m text-text3 uppercase tracking-wider mb-2">原 sheet 截图</div>
            <img :src="`/api/documents/sheet/${activeSheetId}/screenshot`"
                 class="w-full rounded border border-border" />
          </div>
        </div>
      </div>
    </div>
  </transition>
</template>

<style scoped>
.fade-enter-active, .fade-leave-active { transition: opacity 0.2s; }
.fade-enter-from, .fade-leave-to { opacity: 0; }
</style>
