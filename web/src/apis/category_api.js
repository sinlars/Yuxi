import { apiDelete, apiGet, apiPost, apiPut, buildQuery } from './base'

export const categoryApi = {
  getTypes: () => apiGet('/api/categories/types'),

  getTree: (categoryType) => apiGet(`/api/categories?${buildQuery({ category_type: categoryType })}`),

  createCategory: (payload) => apiPost('/api/categories', payload),

  updateCategory: (id, payload) => apiPut(`/api/categories/${id}`, payload),

  deleteCategory: (id) => apiDelete(`/api/categories/${id}`)
}
