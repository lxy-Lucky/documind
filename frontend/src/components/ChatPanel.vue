<script setup lang="ts">
import { computed, nextTick, reactive, ref, watch } from "vue";
import * as api from "@/api/client";
import { state, errorBus } from "@/composables/store";
import type { ChatMessage, Citation } from "@/types";
import MessageBubble from "./MessageBubble.vue";

const emit = defineEmits<{ (e: "cite-click", c: Citation): void }>();

const question = ref("");
const sending = ref(false);
const scrollEl = ref<HTMLDivElement | null>(null);

const contextLabel = computed(() => {
  const m = state.contextMode;
  if (m.kind === "all") return "全部知识库";
  if (m.kind === "folder") {
    const f = state.folders.find((x) => x.id === m.id);
    return `文件夹 / ${f?.name ?? m.id}`;
  }
  const docs = Object.values(state.documents).flat();
  const d = docs.find((x) => x.id === (m as any).id);
  return `文档 / ${d?.filename ?? m.id}`;
});

async function scrollDown() {
  await nextTick();
  if (scrollEl.value) scrollEl.value.scrollTop = scrollEl.value.scrollHeight;
}

async function send() {
  const q = question.value.trim();
  if (!q || sending.value) return;
  question.value = "";

  state.messages.push({
    id: crypto.randomUUID(),
    role: "user",
    content: q,
    context_label: contextLabel.value,
  });
  await scrollDown();

  // IMPORTANT: wrap in `reactive()` BEFORE pushing so that subsequent
  // mutations to `asst.content` go through the Vue proxy and trigger
  // re-renders. Pushing a plain object and then mutating it directly
  // bypasses reactivity (the array element becomes a fresh proxy whose
  // underlying target is the original object — the original reference
  // no longer routes through the proxy trap).
  const asst = reactive<ChatMessage>({
    id: crypto.randomUUID(),
    role: "assistant",
    content: "",
    streaming: true,
    citations: [],
  });
  state.messages.push(asst);

  sending.value = true;
  const scope = {
    folder_id: state.contextMode.kind === "folder" ? state.contextMode.id : null,
    document_id: state.contextMode.kind === "document" ? state.contextMode.id : null,
  };

  api.streamChat(q, scope, {
    onHits: (cites) => { asst.citations = cites; },
    onToken: (delta) => {
      asst.content += delta;
      scrollDown();
    },
    onConfidence: (c) => { asst.confidence = c; },
    onDone: () => {
      asst.streaming = false;
      sending.value = false;
    },
    onError: (msg) => {
      asst.streaming = false;
      asst.content = (asst.content || "") + `\n\n_错误: ${msg}_`;
      sending.value = false;
      errorBus.value = msg;
    },
  });
}

watch(() => state.messages.length, () => scrollDown());
</script>

<template>
  <div class="flex flex-col h-full">
    <!-- topbar -->
    <div class="px-6 py-3 border-b border-border flex items-center gap-3">
      <div class="text-[11px] font-m text-text3">范围:</div>
      <div class="text-[12px] text-accent">{{ contextLabel }}</div>
    </div>

    <!-- messages -->
    <div ref="scrollEl" class="flex-1 overflow-y-auto px-6 py-6">
      <div v-if="state.messages.length === 0" class="text-center text-text3 mt-24">
        <div class="font-d text-2xl text-text2 mb-2">DocuMind</div>
        <div class="text-sm">选择文件夹或文档，开始提问</div>
      </div>
      <MessageBubble v-for="m in state.messages" :key="m.id" :msg="m"
                     @cite-click="(c) => emit('cite-click', c)" />
    </div>

    <!-- input -->
    <div class="border-t border-border px-6 py-4">
      <div class="flex gap-2 items-end bg-bg-surface border border-border rounded px-3 py-2 focus-within:border-accent transition-colors">
        <textarea v-model="question" rows="1"
                  class="flex-1 bg-transparent resize-none outline-none text-text1 text-[14px] leading-relaxed py-1"
                  placeholder="选择文件夹后在此提问，例如：这个版本的认证方案具体是怎样设计的？"
                  @keydown.enter.exact.prevent="send" />
        <button
          class="px-4 py-1.5 rounded text-[12px] font-d font-semibold transition-all"
          :class="sending || !question.trim()
                  ? 'bg-bg-elevated text-text3 cursor-not-allowed'
                  : 'bg-accent text-bg-deep hover:shadow-glow'"
          :disabled="sending || !question.trim()"
          @click="send"
        >
          {{ sending ? '生成中…' : '发送' }}
        </button>
      </div>
    </div>
  </div>
</template>
