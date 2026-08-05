<template>
  <div ref="listRef" class="web-search-result-list">
    <div v-if="results.length > 0" class="search-results">
      <div
        v-for="(result, index) in results"
        :key="getItemKey(result, index)"
        class="search-result-item"
        :class="{ 'is-citation-highlighted': highlightedSource === result.citation_source }"
        :data-citation-source="result.citation_source || undefined"
      >
        <div class="result-header">
          <span class="source-status">
            <span v-if="result.citation_index" class="citation-badge">
              {{ result.citation_index }}
            </span>
            <span v-else class="candidate-badge">候选</span>
          </span>
          <h5 class="result-title">
            <a :href="result.url" target="_blank" rel="noopener noreferrer">
              {{ result.title }}
            </a>
          </h5>
          <span v-if="typeof result.score === 'number'" class="result-score">
            相关度: {{ (result.score * 100).toFixed(1) }}%
          </span>
        </div>

        <div v-if="result.content" class="result-content">
          {{ result.content }}
        </div>
      </div>
    </div>

    <div v-else class="no-results">
      <p>{{ emptyText }}</p>
    </div>
  </div>
</template>

<script setup>
import { onBeforeUnmount, ref } from 'vue'

const props = defineProps({
  results: {
    type: Array,
    default: () => []
  },
  emptyText: {
    type: String,
    default: '未找到相关搜索结果'
  }
})

const listRef = ref(null)
const highlightedSource = ref('')
let highlightTimer = null

const getItemKey = (item, index) => {
  if (item?.url) return item.url
  if (item?.title) return `${item.title}-${index}`
  return `${index}`
}

const revealSource = (citationSource) => {
  const source = String(citationSource || '')
  if (!source || !props.results.some((result) => result.citation_source === source)) return false

  highlightedSource.value = source
  const target = [...(listRef.value?.querySelectorAll('[data-citation-source]') || [])].find(
    (element) => element.dataset.citationSource === source
  )
  target?.scrollIntoView({ behavior: 'smooth', block: 'nearest' })

  if (highlightTimer) window.clearTimeout(highlightTimer)
  highlightTimer = window.setTimeout(() => {
    highlightedSource.value = ''
  }, 1800)
  return true
}

onBeforeUnmount(() => {
  if (highlightTimer) window.clearTimeout(highlightTimer)
})

defineExpose({ revealSource })
</script>

<style scoped lang="less">
.web-search-result-list {
  .search-results {
    display: flex;
    flex-direction: column;
    gap: 8px;
  }

  .search-result-item {
    padding: 10px;
    border: 1px solid var(--gray-150);
    border-radius: 8px;
    background: var(--gray-0);

    &.is-citation-highlighted {
      background: var(--main-10);
      box-shadow: inset 3px 0 0 var(--main-color);
    }

    .result-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 8px;
      margin-bottom: 4px;

      .source-status {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        flex: 0 0 32px;
        width: 32px;
      }

      .citation-badge {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        width: 18px;
        height: 18px;
        border-radius: 50%;
        background: var(--main-10);
        color: var(--main-color);
        font-size: 11px;
        font-weight: 600;
        line-height: 1;
      }

      .candidate-badge {
        width: 32px;
        box-sizing: border-box;
        padding: 1px 4px;
        border-radius: 4px;
        background: var(--gray-25);
        color: var(--gray-700);
        font-size: 11px;
        line-height: 16px;
        text-align: center;
      }

      .result-title {
        margin: 0;
        font-size: 14px;
        line-height: 1.4;
        flex: 1;

        a {
          color: var(--main-color);
          text-decoration: none;
          font-weight: 500;

          &:hover {
            text-decoration: underline;
          }
        }
      }

      .result-score {
        font-size: 11px;
        color: var(--gray-600);
        background: var(--gray-50);
        padding: 0 6px;
        border-radius: 10px;
        white-space: nowrap;
      }
    }

    .result-content {
      font-size: 12px;
      line-height: 1.5;
      color: var(--gray-700);
      overflow: hidden;
      display: -webkit-box;
      line-clamp: 2;
      -webkit-line-clamp: 2;
      -webkit-box-orient: vertical;
    }
  }

  .no-results {
    text-align: center;
    color: var(--gray-500);
    padding: 12px;
    font-size: 12px;
  }
}
</style>
