<template>
  <div class="resource-manage-view">
    <PageHeader title="资源管理" :loading="loading" />

    <div class="resource-layout">
      <div class="resource-toolbar">
        <a-select
          v-model:value="filters.resourceType"
          class="type-filter"
          :options="typeOptions"
          @change="selectType"
        />
        <a-input-search
          v-model:value="filters.keyword"
          class="keyword-search"
          placeholder="搜索标题、摘要、作者或机构"
          allow-clear
          @search="reloadResources"
        />
        <a-button v-if="isAdmin" type="primary" class="lucide-icon-btn" @click="goCreate">
          <Plus :size="16" />
          <span>新建资源</span>
        </a-button>
        <a-button v-if="isAdmin" class="lucide-icon-btn" @click="typeDrawerOpen = true">
          <Settings2 :size="15" />
          <span>类型管理</span>
        </a-button>
        <a-button
          v-if="isAdmin"
          class="lucide-icon-btn"
          :disabled="!selectedRowKeys.length"
          :loading="publishing"
          @click="publishSelected"
        >
          <Send :size="15" />
          <span>发布</span>
        </a-button>
        <a-button
          v-if="isAdmin"
          class="lucide-icon-btn"
          :disabled="!selectedRowKeys.length"
          @click="openCategoryAssign"
        >
          <Tags :size="15" />
          <span>分类标引</span>
        </a-button>
        <span class="resource-total">共 {{ total }} 条资源</span>
      </div>

      <a-table
        :columns="columns"
        :data-source="items"
        :loading="loading"
        :pagination="pagination"
        :row-key="(record) => record.id"
        :row-selection="rowSelection"
        :scroll="{ x: 1320 }"
        size="middle"
        @change="handleTableChange"
      >
        <template #bodyCell="{ column, record }">
          <template v-if="column.key === 'title'">
            <div class="resource-title-cell">
              <a-avatar shape="square" :size="44" :src="record.cover_url || undefined">
                <template #icon>
                  <FileText v-if="!record.cover_url" :size="20" />
                </template>
              </a-avatar>
              <div class="resource-title-text">
                <span class="resource-title">{{ record.title }}</span>
                <span v-if="record.subtitle" class="resource-subtitle">{{ record.subtitle }}</span>
              </div>
            </div>
          </template>
          <template v-else-if="column.key === 'type'">
            <a-tag color="blue">{{ typeName(record.resource_type) }}</a-tag>
          </template>
          <template v-else-if="column.key === 'status'">
            <a-tag :color="record.status === 'published' ? 'green' : 'orange'">
              {{ record.status === 'published' ? '已发布' : '编辑中' }}
            </a-tag>
          </template>
          <template v-else-if="column.key === 'author'">
            <span>{{ record.author || '—' }}</span>
            <div v-if="record.institution" class="resource-institution">{{ record.institution }}</div>
          </template>
          <template v-else-if="column.key === 'terms'">
            <div class="resource-terms">
              <div v-if="record.keywords?.length" class="resource-term-row">
                <span class="resource-term-label">关键词</span>
                <a-tag v-for="word in record.keywords" :key="word">{{ word }}</a-tag>
              </div>
              <div v-if="record.subject_terms?.length" class="resource-term-row">
                <span class="resource-term-label">主题词</span>
                <a-tag v-for="word in record.subject_terms" :key="word">{{ word }}</a-tag>
              </div>
              <span v-if="!record.keywords?.length && !record.subject_terms?.length">—</span>
            </div>
          </template>
          <template v-else-if="column.key === 'creators'">
            <div class="resource-creator">
              <span>创建：{{ record.created_by || '—' }}</span>
              <span>更新：{{ record.updated_by || '—' }}</span>
            </div>
          </template>
          <template v-else-if="column.key === 'updated_at'">
            <span>{{ formatTime(record.updated_at) }}</span>
          </template>
          <template v-else-if="column.key === 'actions'">
            <a-dropdown :trigger="['click']" placement="bottomRight">
              <button type="button" class="row-more-trigger" aria-label="更多操作">
                <MoreHorizontal :size="18" />
              </button>
              <template #overlay>
                <a-menu class="resource-action-menu" @click="({ key }) => handleResourceAction(key, record)">
                  <a-menu-item key="view">
                    <span class="lucide-menu-item">
                      <Eye :size="15" />
                      <span>查看</span>
                    </span>
                  </a-menu-item>
                  <a-menu-item v-if="isAdmin" key="edit">
                    <span class="lucide-menu-item">
                      <Pencil :size="15" />
                      <span>编辑</span>
                    </span>
                  </a-menu-item>
                  <a-menu-item v-if="isAdmin && record.status !== 'published'" key="publish">
                    <span class="lucide-menu-item">
                      <Send :size="15" />
                      <span>发布</span>
                    </span>
                  </a-menu-item>
                  <a-menu-divider v-if="isAdmin" />
                  <a-menu-item v-if="isAdmin" key="delete" danger>
                    <span class="lucide-menu-item">
                      <Trash2 :size="15" />
                      <span>删除</span>
                    </span>
                  </a-menu-item>
                </a-menu>
              </template>
            </a-dropdown>
          </template>
        </template>
      </a-table>
    </div>

    <a-drawer v-model:open="typeDrawerOpen" title="资源类型管理" width="520" destroy-on-close>
      <div class="type-create">
        <a-input v-model:value="newType.code" placeholder="编码，如 case" />
        <a-input v-model:value="newType.name" placeholder="名称，如 案例" />
        <a-input-number v-model:value="newType.sort_order" :min="0" placeholder="排序" />
        <a-button type="primary" :loading="typeSaving" @click="createType">新增</a-button>
      </div>
      <a-list :data-source="types" size="small" bordered>
        <template #renderItem="{ item }">
          <a-list-item>
            <div class="type-row">
              <div class="type-row-main">
                <a-input v-model:value="typeEdits[item.code].name" size="small" />
                <a-input-number v-model:value="typeEdits[item.code].sort_order" size="small" :min="0" />
                <a-tag v-if="item.is_builtin" color="default">内置</a-tag>
              </div>
              <a-space :size="4">
                <a-button type="link" size="small" @click="saveType(item)">保存</a-button>
                <a-popconfirm v-if="!item.is_builtin" title="确认删除该类型？" @confirm="removeType(item)">
                  <a-button type="link" size="small" danger>删除</a-button>
                </a-popconfirm>
              </a-space>
            </div>
          </a-list-item>
        </template>
      </a-list>
      <div class="type-hint">类型编码创建后不可修改；内置类型与仍被资源使用的类型不可删除。</div>
    </a-drawer>

    <a-modal
      v-model:open="categoryModalOpen"
      title="批量分类标引"
      :confirm-loading="assigning"
      ok-text="确认标引"
      cancel-text="取消"
      destroy-on-close
      @ok="confirmCategoryAssign"
    >
      <div class="category-assign-body">
        <p class="category-assign-hint">
          已选 {{ selectedRowKeys.length }} 条资源，可跨分类类型选择一个或多个分类。
        </p>
        <a-tree-select
          v-model:value="assignCategoryIds"
          class="category-assign-select"
          :tree-data="categoryTreeData"
          :loading="categoryOptionsLoading"
          multiple
          allow-clear
          show-search
          tree-node-filter-prop="title"
          placeholder="请选择分类"
        />
      </div>
    </a-modal>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { Modal, message } from 'ant-design-vue'
import { Eye, FileText, MoreHorizontal, Pencil, Plus, Send, Settings2, Tags, Trash2 } from '@lucide/vue'

import PageHeader from '@/components/shared/PageHeader.vue'
import { categoryApi } from '@/apis/category_api'
import { resourceApi } from '@/apis/resource_api'
import { useUserStore } from '@/stores/user'
import { buildCategoryTreeSelectData } from '@/utils/categoryTree'

const router = useRouter()
const userStore = useUserStore()
const isAdmin = computed(() => userStore.isAdmin)

const loading = ref(false)
const types = ref([])
const items = ref([])
const total = ref(0)
const filters = reactive({ resourceType: 'all', keyword: '', page: 1, pageSize: 20 })

const typeDrawerOpen = ref(false)
const typeSaving = ref(false)
const typeEdits = reactive({})
const newType = reactive({ code: '', name: '', sort_order: 0 })

const selectedRowKeys = ref([])
const publishing = ref(false)

const categoryModalOpen = ref(false)
const assigning = ref(false)
const assignCategoryIds = ref([])
const categoryOptionsLoading = ref(false)
const categoryTreeData = ref([])

const columns = [
  { title: '标题', key: 'title', width: 300 },
  { title: '类型', key: 'type', width: 100 },
  { title: '状态', key: 'status', width: 90 },
  { title: '作者 / 机构', key: 'author', width: 150 },
  { title: '关键词 / 主题词', key: 'terms', width: 220 },
  { title: '创建人 / 更新人', key: 'creators', width: 140 },
  { title: '更新时间', key: 'updated_at', width: 170 },
  { title: '操作', key: 'actions', width: 80, fixed: 'right' }
]

const rowSelection = computed(() => ({
  selectedRowKeys: selectedRowKeys.value,
  onChange: (keys) => {
    selectedRowKeys.value = keys
  }
}))

const pagination = computed(() => ({
  current: filters.page,
  pageSize: filters.pageSize,
  total: total.value,
  showSizeChanger: true,
  showTotal: (count) => `共 ${count} 条`
}))

const typeOptions = computed(() => [
  { label: '全部类型', value: 'all' },
  ...types.value.map((item) => ({ label: item.name, value: item.code }))
])

function typeName(code) {
  return types.value.find((item) => item.code === code)?.name || code
}

function formatTime(value) {
  if (!value) return '—'
  return new Date(value).toLocaleString('zh-CN', { hour12: false })
}

async function loadTypes() {
  const res = await resourceApi.getTypes()
  types.value = res.items || []
  types.value.forEach((item) => {
    typeEdits[item.code] = { name: item.name, sort_order: item.sort_order }
  })
}

async function loadResources() {
  loading.value = true
  try {
    const res = await resourceApi.getResources({
      resource_type: filters.resourceType === 'all' ? '' : filters.resourceType,
      keyword: filters.keyword,
      page: filters.page,
      page_size: filters.pageSize
    })
    items.value = res.items || []
    total.value = res.total || 0
  } catch (error) {
    message.error(error.message || '加载资源失败')
  } finally {
    loading.value = false
  }
}

function selectType() {
  filters.page = 1
  selectedRowKeys.value = []
  loadResources()
}

function reloadResources() {
  filters.page = 1
  selectedRowKeys.value = []
  loadResources()
}

function handleTableChange(pager) {
  filters.page = pager.current
  filters.pageSize = pager.pageSize
  selectedRowKeys.value = []
  loadResources()
}

async function publishResources(ids) {
  publishing.value = true
  try {
    const res = await resourceApi.publishResources(ids)
    message.success(`已发布 ${res.published} 条资源`)
    selectedRowKeys.value = []
    await loadResources()
  } catch (error) {
    message.error(error.message || '发布失败')
  } finally {
    publishing.value = false
  }
}

function publishSelected() {
  publishResources([...selectedRowKeys.value])
}

function publishOne(record) {
  publishResources([record.id])
}

async function loadCategoryOptions() {
  categoryOptionsLoading.value = true
  try {
    const res = await categoryApi.getTypes()
    const categoryTypes = res.items || []
    const trees = await Promise.all(categoryTypes.map((type) => categoryApi.getTree(type.code)))
    const treesByType = {}
    categoryTypes.forEach((type, index) => {
      treesByType[type.code] = trees[index]?.items || []
    })
    categoryTreeData.value = buildCategoryTreeSelectData(categoryTypes, treesByType)
  } catch (error) {
    message.error(error.message || '加载分类失败')
  } finally {
    categoryOptionsLoading.value = false
  }
}

async function openCategoryAssign() {
  if (!selectedRowKeys.value.length) return
  assignCategoryIds.value = []
  categoryModalOpen.value = true
  await loadCategoryOptions()
}

async function confirmCategoryAssign() {
  if (!assignCategoryIds.value.length) {
    message.warning('请至少选择一个分类')
    return
  }
  assigning.value = true
  try {
    const res = await resourceApi.assignCategories([...selectedRowKeys.value], [...assignCategoryIds.value])
    if (res.linked) {
      message.success(`已为 ${res.resources} 条资源新增 ${res.linked} 条分类标引`)
    } else {
      message.info('所选分类均已标引，无需重复添加')
    }
    categoryModalOpen.value = false
    selectedRowKeys.value = []
    await loadResources()
  } catch (error) {
    message.error(error.message || '分类标引失败')
  } finally {
    assigning.value = false
  }
}

function goCreate() {
  router.push({ name: 'ResourceCreate' })
}

function goDetail(record) {
  router.push({ name: 'ResourceDetail', params: { id: record.id } })
}

function goEdit(record) {
  router.push({ name: 'ResourceEdit', params: { id: record.id } })
}

function handleResourceAction(key, record) {
  if (key === 'view') {
    goDetail(record)
  } else if (key === 'edit') {
    goEdit(record)
  } else if (key === 'publish') {
    publishOne(record)
  } else if (key === 'delete') {
    confirmRemoveResource(record)
  }
}

function confirmRemoveResource(record) {
  Modal.confirm({
    title: '确认删除该资源？',
    content: record.title,
    okText: '删除',
    cancelText: '取消',
    okType: 'danger',
    onOk: () => removeResource(record)
  })
}

async function removeResource(record) {
  try {
    await resourceApi.deleteResource(record.id)
    selectedRowKeys.value = selectedRowKeys.value.filter((key) => key !== record.id)
    message.success('已删除')
    await loadResources()
  } catch (error) {
    message.error(error.message || '删除失败')
  }
}

async function createType() {
  if (!newType.code.trim() || !newType.name.trim()) {
    message.warning('请填写类型编码与名称')
    return
  }
  typeSaving.value = true
  try {
    await resourceApi.createType({
      code: newType.code.trim(),
      name: newType.name.trim(),
      sort_order: Number(newType.sort_order) || 0
    })
    newType.code = ''
    newType.name = ''
    newType.sort_order = 0
    await loadTypes()
    message.success('类型已新增')
  } catch (error) {
    message.error(error.message || '新增类型失败')
  } finally {
    typeSaving.value = false
  }
}

async function saveType(item) {
  try {
    await resourceApi.updateType(item.code, {
      name: typeEdits[item.code].name,
      sort_order: Number(typeEdits[item.code].sort_order) || 0
    })
    await loadTypes()
    message.success('类型已更新')
  } catch (error) {
    message.error(error.message || '更新类型失败')
  }
}

async function removeType(item) {
  try {
    await resourceApi.deleteType(item.code)
    await loadTypes()
    await loadResources()
    message.success('类型已删除')
  } catch (error) {
    message.error(error.message || '删除类型失败')
  }
}

onMounted(async () => {
  try {
    await loadTypes()
  } catch (error) {
    message.error(error.message || '加载资源类型失败')
  }
  await loadResources()
})
</script>

<style scoped lang="less">
.resource-manage-view {
  display: flex;
  flex-direction: column;
  height: 100%;
  overflow: hidden;
}

.resource-layout {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 16px var(--page-padding) 24px;
  overflow: auto;
}

.resource-toolbar {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}

.type-filter {
  width: 160px;
}

.keyword-search {
  width: 320px;
  max-width: 100%;
}

.resource-total {
  color: var(--gray-600);
  font-size: 13px;
}

.resource-title-cell {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
}

.resource-title-text {
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.resource-title {
  font-weight: 500;
  color: var(--gray-2000);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.resource-subtitle {
  color: var(--gray-600);
  font-size: 12px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.resource-institution {
  color: var(--gray-600);
  font-size: 12px;
}

.resource-terms {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.resource-term-row {
  display: flex;
  align-items: center;
  gap: 4px;
  flex-wrap: wrap;
}

.resource-term-label {
  color: var(--gray-600);
  font-size: 12px;
}

.resource-creator {
  display: flex;
  flex-direction: column;
  color: var(--gray-700);
  font-size: 12px;
}

.row-more-trigger {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: var(--gray-800);
  cursor: pointer;
}

.row-more-trigger:hover {
  background: var(--gray-100);
}

.lucide-menu-item {
  display: inline-flex;
  align-items: center;
  gap: 8px;
}

.type-create {
  display: grid;
  grid-template-columns: 1fr 1fr 90px auto;
  gap: 8px;
  margin-bottom: 12px;
}

.type-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  width: 100%;
}

.type-row-main {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.type-hint {
  margin-top: 12px;
  color: var(--gray-600);
  font-size: 12px;
}

.category-assign-body {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.category-assign-hint {
  margin: 0;
  color: var(--gray-600);
  font-size: 13px;
}

.category-assign-select {
  width: 100%;
}

@media (max-width: 900px) {
  .type-create {
    grid-template-columns: 1fr;
  }
}
</style>
