import assert from 'node:assert/strict'

import { normalizeBrokenMarkdownEmphasis } from '../markdown_normalizer.js'

assert.equal(
  normalizeBrokenMarkdownEmphasis('提出**"三个世界"划分**的战略思想'),
  '提出<strong>"三个世界"划分</strong>的战略思想'
)
assert.equal(
  normalizeBrokenMarkdownEmphasis('提出**“三个世界”划分**的战略思想'),
  '提出<strong>“三个世界”划分</strong>的战略思想'
)
assert.equal(
  normalizeBrokenMarkdownEmphasis('`提出**"三个世界"划分**`'),
  '`提出**"三个世界"划分**`'
)
assert.equal(
  normalizeBrokenMarkdownEmphasis('```markdown\n提出**"三个世界"划分**\n```'),
  '```markdown\n提出**"三个世界"划分**\n```'
)
assert.equal(normalizeBrokenMarkdownEmphasis('正常的 **加粗内容**'), '正常的 **加粗内容**')

console.log('markdown_normalizer: all assertions passed')
