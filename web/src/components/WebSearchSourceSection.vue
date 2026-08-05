<template>
  <div class="source-section">
    <div class="section-title">
      <span>网络检索结果</span>
      <span class="section-stats">引用 {{ citedCount }} · 检索 {{ sources.length }}</span>
    </div>
    <WebSearchResultList ref="resultListRef" :results="sources" empty-text="未找到网络搜索来源" />
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import WebSearchResultList from '@/components/sources/WebSearchResultList.vue'

const props = defineProps({
  sources: {
    type: Array,
    default: () => []
  }
})

const citedCount = computed(() => props.sources.filter((source) => source.citation_index).length)

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
