import { apiDelete, apiGet, apiPost, apiPut, apiRequest, buildQuery } from './base'

export const resourceApi = {
  getTypes: () => apiGet('/api/resources/types'),

  createType: (payload) => apiPost('/api/resources/types', payload),

  updateType: (code, payload) => apiPut(`/api/resources/types/${encodeURIComponent(code)}`, payload),

  deleteType: (code) => apiDelete(`/api/resources/types/${encodeURIComponent(code)}`),

  getResources: (params = {}) => apiGet(`/api/resources?${buildQuery(params)}`),

  getResource: (id) => apiGet(`/api/resources/${id}`),

  createResource: (payload) => apiPost('/api/resources', payload),

  updateResource: (id, payload) => apiPut(`/api/resources/${id}`, payload),

  deleteResource: (id) => apiDelete(`/api/resources/${id}`),

  publishResources: (ids) => apiPost('/api/resources/publish', { ids }),

  assignCategories: (resourceIds, categoryIds) =>
    apiPost('/api/resources/categories/assign', { resource_ids: resourceIds, category_ids: categoryIds }),

  uploadFile: (file, kind = 'attachment') => {
    const formData = new FormData()
    formData.append('file', file)
    formData.append('kind', kind)
    return apiRequest('/api/resources/uploads', { method: 'POST', body: formData }, true)
  }
}
