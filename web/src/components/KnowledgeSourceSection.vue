<template>
  <div class="source-section">
    <div class="section-title">
      <span>知识库来源</span>
      <span class="section-stats">引用 {{ citedCount }} · 检索 {{ chunks.length }}</span>
    </div>
    <KbResultGroupedList
      ref="resultListRef"
      :chunks="chunks"
      :show-summary="false"
      :default-expanded="true"
    />
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import KbResultGroupedList from '@/components/sources/KbResultGroupedList.vue'

const props = defineProps({
  chunks: {
    type: Array,
    default: () => []
  }
})

const citedCount = computed(() => props.chunks.filter((chunk) => chunk.citation_index).length)

const resultListRef = ref(null)
const revealSource = (citationSource) =>
  resultListRef.value?.revealSource?.(citationSource) || false

defineExpose({ revealSource })
</script>

<style scoped lang="less">
.source-section {
  .section-title {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    font-size: 12px;
    color: var(--gray-700);
    margin-bottom: 8px;
    font-weight: 600;
  }

  .section-stats {
    color: var(--gray-500);
    font-weight: 400;
    white-space: nowrap;
  }
}
</style>
