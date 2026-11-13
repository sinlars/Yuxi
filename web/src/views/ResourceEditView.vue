<template>
  <div class="resource-edit-view">
    <header class="resource-edit-header">
      <nav class="resource-breadcrumb" aria-label="资源编辑导航">
        <button type="button" class="resource-breadcrumb-back" @click="goBack">资源管理</button>
        <ChevronRight :size="15" aria-hidden="true" />
        <span class="resource-breadcrumb-current">{{ breadcrumbTitle }}</span>
      </nav>
      <a-space v-if="!readonly" :size="8">
        <a-button @click="goBack">取消</a-button>
        <a-button type="primary" :loading="saving" @click="saveResource">保存</a-button>
      </a-space>
    </header>

    <div class="resource-edit-body">
      <a-spin :spinning="loading">
        <a-form layout="vertical" :disabled="readonly">
          <section class="resource-section">
            <h3 class="resource-section-title">基本信息</h3>
            <a-row :gutter="16">
              <a-col :xs="24" :md="12">
                <a-form-item label="标题" required>
                  <a-input v-model:value="form.title" :maxlength="255" placeholder="请输入资源标题" />
                </a-form-item>
              </a-col>
              <a-col :xs="24" :md="12">
                <a-form-item label="副标题">
                  <a-input v-model:value="form.subtitle" :maxlength="255" placeholder="可选" />
                </a-form-item>
              </a-col>
              <a-col :xs="24" :md="12">
                <a-form-item label="资源类型" required>
                  <a-select v-model:value="form.resource_type" :options="typeOptions" placeholder="选择资源类型" />
                </a-form-item>
              </a-col>
              <a-col :xs="24" :md="12">
                <a-form-item label="作者">
                  <a-input v-model:value="form.author" :maxlength="255" placeholder="可选" />
                </a-form-item>
              </a-col>
              <a-col :xs="24" :md="12">
                <a-form-item label="所属机构">
                  <a-input v-model:value="form.institution" :maxlength="255" placeholder="可选" />
                </a-form-item>
              </a-col>
              <a-col :span="24">
                <a-form-item label="摘要 / 简介">
                  <a-textarea v-model:value="form.summary" :rows="3" placeholder="可选" />
                </a-form-item>
              </a-col>
            </a-row>
          </section>

          <section class="resource-section">
            <h3 class="resource-section-title">分类与封面</h3>
            <a-row :gutter="16">
              <a-col :xs="24" :md="8">
                <a-form-item label="关键词">
                  <a-select v-model:value="form.keywords" mode="tags" :token-separators="[',']" placeholder="回车添加" />
                </a-form-item>
              </a-col>
              <a-col :xs="24" :md="8">
                <a-form-item label="主题词">
                  <a-select
                    v-model:value="form.subject_terms"
                    mode="tags"
                    :token-separators="[',']"
                    placeholder="回车添加"
                  />
                </a-form-item>
              </a-col>
              <a-col :xs="24" :md="8">
                <a-form-item label="标签">
                  <a-select v-model:value="form.tags" mode="tags" :token-separators="[',']" placeholder="回车添加" />
                </a-form-item>
              </a-col>
              <a-col :span="24">
                <a-form-item label="分类标引">
                  <div v-if="readonly" class="category-tags">
                    <a-tag v-for="categoryId in form.category_ids" :key="categoryId">
                      {{ categoryPathMap[categoryId] || `分类 #${categoryId}` }}
                    </a-tag>
                    <span v-if="!form.category_ids.length" class="category-empty">暂无分类</span>
                  </div>
                  <a-tree-select
                    v-else
                    v-model:value="form.category_ids"
                    class="category-select"
                    :tree-data="categoryTreeData"
                    multiple
                    allow-clear
                    show-search
                    tree-node-filter-prop="title"
                    placeholder="请选择一个或多个分类"
                  />
                </a-form-item>
              </a-col>
              <a-col :span="24">
                <a-form-item label="封面 / 缩略图">
                  <div class="cover-field">
                    <a-avatar shape="square" :size="88" :src="form.cover_url || undefined">
                      <template #icon>
                        <PictureOutlined v-if="!form.cover_url" />
                      </template>
                    </a-avatar>
                    <div v-if="!readonly" class="cover-actions">
                      <a-upload :show-upload-list="false" accept="image/*" :before-upload="handleCoverUpload">
                        <a-button :loading="coverUploading">上传封面</a-button>
                      </a-upload>
                      <a-button v-if="form.cover_url" type="link" danger @click="form.cover_url = ''">移除</a-button>
                    </div>
                  </div>
                </a-form-item>
              </a-col>
            </a-row>
          </section>

          <section class="resource-section">
            <h3 class="resource-section-title">正文</h3>
            <div class="markdown-editor">
              <a-textarea
                v-model:value="form.content_markdown"
                class="markdown-input"
                :rows="18"
                placeholder="支持 Markdown 语法，右侧实时预览"
              />
              <div class="markdown-preview">
                <MarkdownPreview :content="form.content_markdown" />
              </div>
            </div>
          </section>

          <section class="resource-section">
            <h3 class="resource-section-title">附件</h3>
            <div class="attachment-list">
              <div v-for="(item, index) in form.attachments" :key="`${item.url}-${index}`" class="attachment-item">
                <PaperClipOutlined />
                <a :href="item.url" target="_blank" rel="noopener noreferrer">{{ item.name }}</a>
                <span class="attachment-size">{{ formatSize(item.size) }}</span>
                <a-button v-if="!readonly" type="link" danger size="small" @click="removeAttachment(index)">
                  移除
                </a-button>
              </div>
              <span v-if="!form.attachments.length" class="attachment-empty">暂无附件</span>
              <a-upload v-if="!readonly" :show-upload-list="false" multiple :before-upload="handleAttachmentUpload">
                <a-button :loading="attachmentUploading">上传附件</a-button>
              </a-upload>
              <div class="attachment-hint">
                支持 PDF、Word、PPT/PPTX、音视频、图片与 3D 模型等格式，单个文件不超过 100 MB。
              </div>
            </div>
          </section>
        </a-form>
      </a-spin>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { message } from 'ant-design-vue'
import { ChevronRight } from '@lucide/vue'
import { PaperClipOutlined, PictureOutlined } from '@ant-design/icons-vue'

import MarkdownPreview from '@/components/common/MarkdownPreview.vue'
import { categoryApi } from '@/apis/category_api'
import { resourceApi } from '@/apis/resource_api'
import { useUserStore } from '@/stores/user'
import { buildCategoryPathMap, buildCategoryTreeSelectData } from '@/utils/categoryTree'

const route = useRoute()
const router = useRouter()
const userStore = useUserStore()

const isAdmin = computed(() => userStore.isAdmin)
const routeName = computed(() => route.name)
const isCreate = computed(() => routeName.value === 'ResourceCreate')
const isDetail = computed(() => routeName.value === 'ResourceDetail')
const readonly = computed(() => isDetail.value || !isAdmin.value)

const loading = ref(false)
const saving = ref(false)
const coverUploading = ref(false)
const attachmentUploading = ref(false)
const types = ref([])
const categoryTypes = ref([])
const categoryTreesByType = ref({})

const form = reactive({
  resource_type: '',
  title: '',
  subtitle: '',
  summary: '',
  author: '',
  institution: '',
  keywords: [],
  subject_terms: [],
  tags: [],
  cover_url: '',
  content_markdown: '',
  attachments: [],
  category_ids: []
})

const typeOptions = computed(() => types.value.map((item) => ({ label: item.name, value: item.code })))
const categoryTreeData = computed(() =>
  buildCategoryTreeSelectData(categoryTypes.value, categoryTreesByType.value)
)
const categoryPathMap = computed(() => buildCategoryPathMap(categoryTypes.value, categoryTreesByType.value))

const breadcrumbTitle = computed(() => {
  if (isCreate.value) return '新建资源'
  return form.title || '资源详情'
})

function formatSize(size) {
  if (!size) return ''
  if (size < 1024 * 1024) return `${Math.round(size / 1024)} KB`
  return `${(size / 1024 / 1024).toFixed(1)} MB`
}

function goBack() {
  router.push('/resources')
}

async function loadTypes() {
  const res = await resourceApi.getTypes()
  types.value = res.items || []
  if (!form.resource_type && types.value.length) {
    form.resource_type = types.value[0].code
  }
}

async function loadCategoryOptions() {
  const res = await categoryApi.getTypes()
  categoryTypes.value = res.items || []
  const trees = await Promise.all(categoryTypes.value.map((type) => categoryApi.getTree(type.code)))
  const treesByType = {}
  categoryTypes.value.forEach((type, index) => {
    treesByType[type.code] = trees[index]?.items || []
  })
  categoryTreesByType.value = treesByType
}

async function loadDetail() {
  loading.value = true
  try {
    const detail = await resourceApi.getResource(route.params.id)
    Object.assign(form, {
      resource_type: detail.resource_type,
      title: detail.title || '',
      subtitle: detail.subtitle || '',
      summary: detail.summary || '',
      author: detail.author || '',
      institution: detail.institution || '',
      keywords: detail.keywords || [],
      subject_terms: detail.subject_terms || [],
      tags: detail.tags || [],
      cover_url: detail.cover_url || '',
      content_markdown: detail.content_markdown || '',
      attachments: detail.attachments || [],
      category_ids: (detail.categories || []).map((item) => item.id)
    })
  } catch (error) {
    message.error(error.message || '加载资源详情失败')
  } finally {
    loading.value = false
  }
}

function payloadFromForm() {
  return {
    resource_type: form.resource_type,
    title: form.title,
    subtitle: form.subtitle,
    summary: form.summary,
    author: form.author,
    institution: form.institution,
    keywords: form.keywords,
    subject_terms: form.subject_terms,
    tags: form.tags,
    cover_url: form.cover_url,
    content_markdown: form.content_markdown,
    attachments: form.attachments,
    category_ids: form.category_ids
  }
}

async function saveResource() {
  if (!form.title.trim()) {
    message.warning('请填写标题')
    return
  }
  if (!form.resource_type) {
    message.warning('请选择资源类型')
    return
  }
  saving.value = true
  try {
    if (isCreate.value) {
      await resourceApi.createResource(payloadFromForm())
    } else {
      await resourceApi.updateResource(route.params.id, payloadFromForm())
    }
    message.success('保存成功')
    goBack()
  } catch (error) {
    message.error(error.message || '保存失败')
  } finally {
    saving.value = false
  }
}

async function handleCoverUpload(file) {
  coverUploading.value = true
  try {
    const result = await resourceApi.uploadFile(file, 'cover')
    form.cover_url = result.url
    message.success('封面上传成功')
  } catch (error) {
    message.error(error.message || '封面上传失败')
  } finally {
    coverUploading.value = false
  }
  return false
}

async function handleAttachmentUpload(file) {
  if (file.size > 100 * 1024 * 1024) {
    message.warning('附件不能超过 100 MB')
    return false
  }
  attachmentUploading.value = true
  try {
    const result = await resourceApi.uploadFile(file, 'attachment')
    form.attachments.push(result)
    message.success(`${result.name} 上传成功`)
  } catch (error) {
    message.error(error.message || '附件上传失败')
  } finally {
    attachmentUploading.value = false
  }
  return false
}

function removeAttachment(index) {
  form.attachments.splice(index, 1)
}

onMounted(async () => {
  try {
    await loadTypes()
  } catch (error) {
    message.error(error.message || '加载资源类型失败')
  }
  try {
    await loadCategoryOptions()
  } catch (error) {
    message.error(error.message || '加载分类失败')
  }
  if (!isCreate.value && route.params.id) {
    await loadDetail()
  }
})
</script>

<style scoped lang="less">
.resource-edit-view {
  display: flex;
  flex-direction: column;
  height: 100%;
  overflow: hidden;
}

.resource-edit-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  min-height: 56px;
  padding: 0 var(--page-padding);
  border-bottom: 1px solid var(--gray-200);
}

.resource-breadcrumb {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
  font-size: 13px;
}

.resource-breadcrumb-back {
  padding: 4px 6px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: var(--gray-600);
  cursor: pointer;
}

.resource-breadcrumb-back:hover {
  color: var(--main-color);
  background: var(--gray-100);
}

.resource-breadcrumb-current {
  max-width: 420px;
  color: var(--gray-900);
  font-weight: 500;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.resource-edit-body {
  flex: 1;
  min-height: 0;
  padding: 20px var(--page-padding) 32px;
  overflow: auto;
}

.resource-section {
  max-width: 1080px;
  margin-bottom: 24px;
  padding-bottom: 8px;
  border-bottom: 1px solid var(--gray-100);
}

.resource-section:last-of-type {
  border-bottom: 0;
}

.resource-section-title {
  margin: 0 0 16px;
  color: var(--gray-900);
  font-size: 15px;
  font-weight: 600;
}

.cover-field {
  display: flex;
  align-items: center;
  gap: 12px;
}

.cover-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.category-tags {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

.category-empty {
  color: var(--gray-600);
  font-size: 12px;
}

.category-select {
  width: 100%;
}

.markdown-editor {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  gap: 12px;
}

.markdown-input {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 13px;
}

.markdown-preview {
  min-height: 360px;
  max-height: 520px;
  padding: 12px;
  overflow: auto;
  border: 1px solid var(--gray-200);
  border-radius: 8px;
}

.attachment-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.attachment-item {
  display: flex;
  align-items: center;
  gap: 8px;
}

.attachment-size,
.attachment-empty,
.attachment-hint {
  color: var(--gray-600);
  font-size: 12px;
}

@media (max-width: 960px) {
  .markdown-editor {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
