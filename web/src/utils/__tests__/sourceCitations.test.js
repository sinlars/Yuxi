import assert from 'node:assert/strict'

import {
  extractCitationSources,
  renderSourceCitations,
  stripSourceCitations
} from '../sourceCitations.js'

const sources = {
  knowledgeChunks: [
    {
      citation_source: 'kb://db-1/file-1?chunk=chunk-1',
      citation_index: 1
    }
  ],
  webSources: [
    {
      citation_source: 'https://example.com/search?a=1&b=2',
      citation_index: 2
    }
  ]
}

const rendered = renderSourceCitations(
  [
    '知识库结论<cite source="kb://db-1/file-1?chunk=chunk-1"></cite>',
    '重复引用<cite source="kb://db-1/file-1?chunk=chunk-1">99</cite>',
    '网页结论<cite source="https://example.com/search?a=1&amp;b=2"></cite>',
    '伪造来源<cite source="kb://unknown/file"></cite>'
  ].join('\n'),
  sources
)

assert.equal((rendered.match(/>1<\/button>/g) || []).length, 2)
assert.equal((rendered.match(/>2<\/button>/g) || []).length, 1)
assert.equal(rendered.includes('kb://unknown/file'), false)
assert.equal(rendered.includes('<cite'), false)
assert.equal(
  rendered.includes('data-citation-source="https://example.com/search?a=1&amp;b=2"'),
  true
)
assert.deepEqual(
  extractCitationSources(
    '先引用<cite source="https://example.com/search?a=1&amp;b=2"></cite>，重复引用<cite source="https://example.com/search?a=1&amp;b=2"></cite>'
  ),
  ['https://example.com/search?a=1&b=2']
)
assert.equal(
  stripSourceCitations(
    '正文内容<cite source="kb://db-1/file-1?chunk=chunk-1"></cite>，网页内容<cite source="https://example.com/search">内部标记</cite>。'
  ),
  '正文内容，网页内容。'
)

console.log('sourceCitations: all assertions passed')
