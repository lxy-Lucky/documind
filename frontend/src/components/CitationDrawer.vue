<script setup lang="ts">
import { ref, watch } from "vue";
import * as api from "@/api/client";

const props = defineProps<{ chunkId: number | null }>();
const emit = defineEmits<{ (e: "close"): void }>();

const detail = ref<any>(null);
const loading = ref(false);

watch(() => props.chunkId, async (id) => {
  if (id == null) {
    detail.value = null;
    return;
  }
  loading.value = true;
  try {
    detail.value = await api.getChunk(id);
  } catch (e) {
    console.error(e);
  } finally {
    loading.value = false;
  }
});
</script>

<template>
  <transition name="fade">
    <div v-if="chunkId != null" class="fixed inset-0 z-50 flex justify-end">
      <div class="absolute inset-0 bg-bg-deep/70" @click="emit('close')"></div>
      <div class="relative w-[560px] max-w-[90vw] h-full bg-bg-main border-l border-border overflow-y-auto">
        <div class="sticky top-0 bg-bg-main border-b border-border px-5 py-4 flex items-center justify-between z-10">
          <div class="font-d font-semibold">原文片段</div>
          <button class="text-text3 hover:text-text1 text-lg" @click="emit('close')">×</button>
        </div>

        <div v-if="loading" class="p-5 text-text3 text-sm">加载中...</div>

        <div v-else-if="detail" class="p-5 space-y-4">
          <div class="text-[11px] font-m text-text3 space-y-1">
            <div>文件: <span class="text-text1">{{ detail.filename }}</span></div>
            <div>工作表: <span class="text-text1">{{ detail.sheet_name }}</span></div>
            <div v-if="detail.hierarchy_path">层级: <span class="text-text1">{{ detail.hierarchy_path }}</span></div>
            <div v-if="detail.chunk_metadata?.jira_tags?.length">
              JIRA: <span class="text-accent">{{ detail.chunk_metadata.jira_tags.join(', ') }}</span>
            </div>
            <div v-if="detail.chunk_metadata?.colors?.length">
              颜色:
              <span v-for="c in detail.chunk_metadata.colors" :key="c"
                    class="inline-block w-3 h-3 rounded-sm mx-0.5 align-middle"
                    :style="{background: c}"></span>
            </div>
          </div>

          <div>
            <div class="text-[10px] font-m text-text3 uppercase tracking-wider mb-1.5">Markdown</div>
            <pre class="bg-bg-surface border border-border rounded p-3 text-[12px] leading-relaxed whitespace-pre-wrap text-text1 font-m">{{ detail.markdown }}</pre>
          </div>

          <div v-if="detail.summaries?.length">
            <div class="text-[10px] font-m text-text3 uppercase tracking-wider mb-1.5">多视角摘要</div>
            <div v-for="s in detail.summaries" :key="s.perspective" class="bg-bg-surface border border-border rounded p-2.5 mb-2">
              <div class="text-[10px] font-m text-accent mb-0.5">{{ s.perspective }}</div>
              <div class="text-[12px] text-text2">{{ s.text }}</div>
            </div>
          </div>

          <div v-if="detail.screenshot_path">
            <div class="text-[10px] font-m text-text3 uppercase tracking-wider mb-1.5">工作表截图</div>
            <img :src="`/api/documents/sheet/${detail.sheet_id}/screenshot`" class="w-full rounded border border-border" />
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
