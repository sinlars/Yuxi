<template>
  <div class="category-manage-view">
    <PageHeader title="分类管理" :loading="loading" />

    <div class="category-layout">
      <div class="category-toolbar">
        <a-select
          v-model:value="filters.categoryType"
          class="type-filter"
          :options="typeOptions"
          @change="selectType"
        />
        <a-button class="lucide-icon-btn" @click="toggleExpandAll">
          <ChevronsDownUp v-if="allExpanded" :size="15" />
          <ChevronsUpDown v-else :size="15" />
          <span>{{ allExpanded ? '收起' : '展开' }}</span>
        </a-button>
        <a-button v-if="isAdmin" type="primary" class="lucide-icon-btn" @click="openCreate(null)">
          <Plus :size="16" />
          <span>新建根分类</span>
        </a-button>
        <span class="category-total">共 {{ total }} 个分类节点</span>
      </div>

      <a-table
        :columns="columns"
        :data-source="items"
        :loading="loading"
        :pagination="false"
        :row-key="(record) => record.id"
        :expanded-row-keys="expandedRowKeys"
        :scroll="{ x: 1240 }"
        size="middle"
        @expand="handleExpand"
      >
        <template #bodyCell="{ column, record }">
          <template v-if="column.key === 'name'">
            <span class="category-name" :class="{ 'is-disabled': !record.enabled }">{{ record.name }}</span>
          </template>
          <template v-else-if="column.key === 'id'">
            <span class="category-id">#{{ record.id }}</span>
          </template>
          <template v-else-if="column.key === 'code'">
            <span>{{ record.code || '—' }}</span>
          </template>
          <template v-else-if="column.key === 'node_type'">
            <a-tag :color="record.is_leaf ? 'blue' : 'purple'">
              {{ record.is_leaf ? '叶子节点' : '分支节点' }}
            </a-tag>
          </template>
          <template v-else-if="column.key === 'level'">
            <span class="category-level">L{{ record.level }}</span>
          </template>
          <template v-else-if="column.key === 'enabled'">
            <a-tag :color="record.enabled ? 'green' : 'default'">
              {{ record.enabled ? '已启用' : '已停用' }}
            </a-tag>
          </template>
          <template v-else-if="column.key === 'remark'">
            <span class="category-remark">{{ record.remark || '—' }}</span>
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
                <a-menu class="category-action-menu" @click="({ key }) => handleAction(key, record)">
                  <a-menu-item key="create-child">
                    <span class="lucide-menu-item">
                      <Plus :size="15" />
                      <span>新增子分类</span>
                    </span>
                  </a-menu-item>
                  <a-menu-item key="edit">
                    <span class="lucide-menu-item">
                      <Pencil :size="15" />
                      <span>编辑</span>
                    </span>
                  </a-menu-item>
                  <a-menu-item key="toggle">
                    <span class="lucide-menu-item">
                      <Power :size="15" />
                      <span>{{ record.enabled ? '停用' : '启用' }}</span>
                    </span>
                  </a-menu-item>
                  <a-menu-divider />
                  <a-menu-item key="delete" danger>
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

    <a-modal
      v-model:open="formOpen"
      :title="editingId ? '编辑分类' : '新建分类'"
      :confirm-loading="formSaving"
      ok-text="保存"
      cancel-text="取消"
      destroy-on-close
      @ok="submitForm"
    >
      <a-form layout="vertical">
        <a-form-item label="分类类型">
          <a-input :value="currentTypeName" disabled />
        </a-form-item>
        <a-form-item label="父分类">
          <a-tree-select
            v-model:value="form.parentId"
            :tree-data="parentTreeData"
            placeholder="不选择则为根节点"
            allow-clear
            tree-default-expand-all
          />
        </a-form-item>
        <a-form-item label="分类名称" required>
          <a-input v-model:value="form.name" placeholder="如：呼吸系统疾病" :maxlength="255" />
        </a-form-item>
        <a-form-item label="分类编码">
          <a-input v-model:value="form.code" placeholder="同类型内唯一，可留空" :maxlength="128" />
        </a-form-item>
        <a-form-item label="备注">
          <a-textarea v-model:value="form.remark" :rows="3" :maxlength="1000" show-count />
        </a-form-item>
        <a-form-item label="是否启用">
          <a-switch v-model:checked="form.enabled" />
        </a-form-item>
      </a-form>
    </a-modal>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { message, Modal } from 'ant-design-vue'
import { ChevronsDownUp, ChevronsUpDown, MoreHorizontal, Pencil, Plus, Power, Trash2 } from '@lucide/vue'

import PageHeader from '@/components/shared/PageHeader.vue'
import { categoryApi } from '@/apis/category_api'
import { useUserStore } from '@/stores/user'

const userStore = useUserStore()
const isAdmin = computed(() => userStore.isAdmin)

const loading = ref(false)
const types = ref([])
const items = ref([])
const total = ref(0)
const expandedRowKeys = ref([])
const filters = reactive({ categoryType: '' })

const formOpen = ref(false)
const formSaving = ref(false)
const editingId = ref(null)
const form = reactive({ parentId: null, name: '', code: '', remark: '', enabled: true })

const typeOptions = computed(() => types.value.map((item) => ({ label: item.name, value: item.code })))
const currentTypeName = computed(() => typeName(filters.categoryType))

const columns = computed(() => {
  const base = [
    { title: '分类名称', key: 'name', width: 280 },
    { title: 'ID', key: 'id', width: 80 },
    { title: '分类编码', key: 'code', width: 140 },
    { title: '节点类型', key: 'node_type', width: 100 },
    { title: '层级', key: 'level', width: 70 },
    { title: '状态', key: 'enabled', width: 90 },
    { title: '备注', key: 'remark', width: 220 },
    { title: '更新时间', key: 'updated_at', width: 170 }
  ]
  if (isAdmin.value) {
    base.push({ title: '操作', key: 'actions', width: 80, fixed: 'right' })
  }
  return base
})

const parentTreeData = computed(() => toTreeSelectData(items.value, editingId.value))

const allExpanded = computed(() => {
  const parentIds = collectParentKeys(items.value)
  return parentIds.length > 0 && parentIds.every((id) => expandedRowKeys.value.includes(id))
})

function typeName(code) {
  return types.value.find((item) => item.code === code)?.name || code
}

function formatTime(value) {
  if (!value) return '—'
  return new Date(value).toLocaleString('zh-CN', { hour12: false })
}

function collectParentKeys(list, keys = []) {
  list.forEach((item) => {
    if (item.children?.length) {
      keys.push(item.id)
      collectParentKeys(item.children, keys)
    }
  })
  return keys
}

function toTreeSelectData(list, excludedId) {
  const result = []
  list.forEach((item) => {
    if (item.id === excludedId) return
    const node = { value: item.id, title: item.name }
    if (item.children?.length) {
      node.children = toTreeSelectData(item.children, excludedId)
    }
    result.push(node)
  })
  return result
}

async function loadTypes() {
  const res = await categoryApi.getTypes()
  types.value = res.items || []
  if (!filters.categoryType && types.value.length) {
    filters.categoryType = types.value[0].code
  }
}

async function loadTree() {
  if (!filters.categoryType) {
    items.value = []
    total.value = 0
    return
  }
  loading.value = true
  try {
    const res = await categoryApi.getTree(filters.categoryType)
    items.value = res.items || []
    total.value = res.total || 0
    expandedRowKeys.value = collectParentKeys(items.value)
  } catch (error) {
    message.error(error.message || '加载分类失败')
  } finally {
    loading.value = false
  }
}

function selectType() {
  loadTree()
}

function toggleExpandAll() {
  expandedRowKeys.value = allExpanded.value ? [] : collectParentKeys(items.value)
}

function handleExpand(expanded, record) {
  if (expanded) {
    expandedRowKeys.value = [...expandedRowKeys.value, record.id]
  } else {
    expandedRowKeys.value = expandedRowKeys.value.filter((key) => key !== record.id)
  }
}

function openCreate(parent) {
  editingId.value = null
  form.parentId = parent ? parent.id : null
  form.name = ''
  form.code = ''
  form.remark = ''
  form.enabled = true
  formOpen.value = true
}

function openEdit(record) {
  editingId.value = record.id
  form.parentId = record.parent_id
  form.name = record.name
  form.code = record.code || ''
  form.remark = record.remark || ''
  form.enabled = record.enabled
  formOpen.value = true
}

function handleAction(key, record) {
  if (key === 'create-child') {
    openCreate(record)
  } else if (key === 'edit') {
    openEdit(record)
  } else if (key === 'toggle') {
    toggleEnabled(record)
  } else if (key === 'delete') {
    confirmRemove(record)
  }
}

async function submitForm() {
  if (!form.name.trim()) {
    message.warning('请填写分类名称')
    return
  }
  formSaving.value = true
  try {
    const payload = {
      parent_id: form.parentId ?? null,
      name: form.name.trim(),
      code: form.code.trim(),
      remark: form.remark.trim(),
      enabled: form.enabled
    }
    if (editingId.value) {
      await categoryApi.updateCategory(editingId.value, payload)
    } else {
      await categoryApi.createCategory({ ...payload, category_type: filters.categoryType })
    }
    formOpen.value = false
    message.success(editingId.value ? '分类已更新' : '分类已创建')
    await loadTree()
  } catch (error) {
    message.error(error.message || '保存分类失败')
  } finally {
    formSaving.value = false
  }
}

async function toggleEnabled(record) {
  try {
    await categoryApi.updateCategory(record.id, {
      parent_id: record.parent_id,
      name: record.name,
      code: record.code || '',
      remark: record.remark || '',
      enabled: !record.enabled
    })
    message.success(record.enabled ? '分类已停用' : '分类已启用')
    await loadTree()
  } catch (error) {
    message.error(error.message || '更新分类状态失败')
  }
}

function confirmRemove(record) {
  Modal.confirm({
    title: '确认删除该分类？',
    content: record.is_leaf ? record.name : `${record.name}（存在子分类时不可删除）`,
    okText: '删除',
    cancelText: '取消',
    okType: 'danger',
    onOk: () => removeCategory(record)
  })
}

async function removeCategory(record) {
  try {
    await categoryApi.deleteCategory(record.id)
    message.success('已删除')
    await loadTree()
  } catch (error) {
    message.error(error.message || '删除分类失败')
  }
}

onMounted(async () => {
  try {
    await loadTypes()
  } catch (error) {
    message.error(error.message || '加载分类类型失败')
  }
  await loadTree()
})
</script>

<style scoped lang="less">
.category-manage-view {
  display: flex;
  flex-direction: column;
  height: 100%;
  overflow: hidden;
}

.category-layout {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 16px var(--page-padding) 24px;
  overflow: auto;
}

.category-toolbar {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}

.type-filter {
  width: 180px;
}

.category-total {
  color: var(--gray-600);
  font-size: 13px;
}

.category-name {
  color: var(--gray-2000);
  font-weight: 500;
}

.category-name.is-disabled {
  color: var(--gray-600);
}

.category-id {
  color: var(--gray-600);
  font-size: 12px;
}

.category-level {
  color: var(--gray-700);
  font-size: 12px;
}

.category-remark {
  display: inline-block;
  max-width: 200px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  vertical-align: bottom;
  color: var(--gray-700);
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
</style>
