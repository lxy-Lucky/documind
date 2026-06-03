<script setup lang="ts">
import { computed } from "vue";
import { renderAnswer } from "@/composables/markdown";
import type { ChatMessage, Citation } from "@/types";

const props = defineProps<{ msg: ChatMessage }>();
const emit = defineEmits<{ (e: "cite-click", c: Citation): void }>();

const html = computed(() =>
  props.msg.role === "assistant" ? renderAnswer(props.msg.content) : "",
);

function onClick(ev: MouseEvent) {
  const t = ev.target as HTMLElement;
  if (t.classList.contains("cite")) {
    const n = parseInt(t.getAttribute("data-cite") || "0", 10);
    const c = props.msg.citations?.find((x) => x.index === n);
    if (c) emit("cite-click", c);
  }
}

const confidenceColor = computed(() => {
  const c = props.msg.confidence ?? -1;
  if (c >= 8) return "text-green border-green/40 bg-green/10";
  if (c >= 5) return "text-accent border-accent/40 bg-accent/10";
  if (c >= 0) return "text-red border-red/40 bg-red/10";
  return "text-text3 border-border bg-bg-surface";
});
</script>

<template>
  <div class="mb-6">
    <div v-if="msg.role === 'user'" class="flex justify-end">
      <div class="max-w-[78%] bg-bg-elevated border border-border-light px-4 py-3 rounded-2xl rounded-tr-md">
        <div class="text-text1 text-[14px] leading-relaxed">{{ msg.content }}</div>
        <div v-if="msg.context_label" class="text-[10px] font-m text-text3 mt-1.5">
          范围: {{ msg.context_label }}
        </div>
      </div>
    </div>

    <div v-else>
      <div class="flex items-center gap-2 mb-2">
        <div class="w-6 h-6 rounded bg-accent/15 flex items-center justify-center text-accent text-xs font-d font-bold">A</div>
        <span class="text-text2 text-xs font-m">DocuMind</span>
        <span v-if="msg.confidence != null"
              :class="confidenceColor"
              class="text-[10px] font-m px-1.5 py-0.5 rounded border">
          自信度 {{ msg.confidence }}/10
        </span>
        <span v-if="msg.streaming" class="text-[10px] font-m text-accent animate-pulse">正在生成…</span>
      </div>

      <div class="md-answer text-[14px] leading-relaxed pl-8" v-html="html" @click="onClick"></div>

      <div v-if="msg.citations && msg.citations.length" class="pl-8 mt-3">
        <div class="text-[10px] font-m text-text3 mb-1.5">引用片段</div>
        <div class="flex flex-wrap gap-1.5">
          <button v-for="c in msg.citations" :key="c.index"
                  class="text-[11px] font-m bg-bg-surface border border-border hover:border-accent hover:text-accent px-2 py-1 rounded transition-colors"
                  @click="emit('cite-click', c)">
            #{{ c.index }} · {{ c.filename }} / {{ c.sheet_name }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>
