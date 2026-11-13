/** 分类树的 TreeSelect 与只读展示辅助函数。 */

function nodeToTreeSelect(item) {
  const node = {
    value: item.id,
    title: item.code ? `${item.name} (${item.code})` : item.name
  }
  if (item.children?.length) {
    node.children = item.children.map(nodeToTreeSelect)
  }
  return node
}

export function buildCategoryTreeSelectData(types, treesByType) {
  return types
    .map((type) => ({
      value: `type:${type.code}`,
      title: type.name,
      selectable: false,
      children: (treesByType[type.code] || []).map(nodeToTreeSelect)
    }))
    .filter((group) => group.children.length)
}

export function buildCategoryPathMap(types, treesByType) {
  const pathMap = {}
  function walk(items, prefix) {
    items.forEach((item) => {
      const path = `${prefix} / ${item.name}`
      pathMap[item.id] = path
      if (item.children?.length) {
        walk(item.children, path)
      }
    })
  }
  types.forEach((type) => walk(treesByType[type.code] || [], type.name))
  return pathMap
}
