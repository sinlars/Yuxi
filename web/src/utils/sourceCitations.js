import { escapeHtml } from './html.js'

const CITE_PATTERN = /<cite\b[^>]*\bsource\s*=\s*(?:"([^"]+)"|'([^']+)')[^>]*>[\s\S]*?<\/cite>/gi

const decodeCitationSource = (source) =>
  String(source || '')
    .replaceAll('&amp;', '&')
    .replaceAll('&quot;', '"')
    .replaceAll('&#39;', "'")

export const extractCitationSources = (content) => {
  const sources = []
  const seen = new Set()
  const pattern = new RegExp(CITE_PATTERN.source, 'gi')

  for (const match of String(content || '').matchAll(pattern)) {
    const source = decodeCitationSource(match[1] || match[2]).trim()
    if (!source || seen.has(source)) continue
    seen.add(source)
    sources.push(source)
  }
  return sources
}

// `<cite>` is an internal source protocol marker and should never enter the clipboard.
export const stripSourceCitations = (content) => String(content || '').replace(CITE_PATTERN, '')

export const renderSourceCitations = (content, sources = {}) => {
  const citationIndex = new Map()
  const allSources = [
    ...(Array.isArray(sources?.knowledgeChunks) ? sources.knowledgeChunks : []),
    ...(Array.isArray(sources?.webSources) ? sources.webSources : [])
  ]

  allSources.forEach((source) => {
    const citationSource = String(source?.citation_source || '')
    const index = Number(source?.citation_index)
    if (citationSource && Number.isInteger(index) && index > 0) {
      citationIndex.set(citationSource, index)
    }
  })

  return String(content || '').replace(CITE_PATTERN, (_match, doubleQuoted, singleQuoted) => {
    const citationSource = decodeCitationSource(doubleQuoted || singleQuoted).trim()
    const index = citationIndex.get(citationSource)
    if (!index) return ''
    return `<button type="button" class="source-citation" data-citation-source="${escapeHtml(
      citationSource
    )}" aria-label="查看来源 ${index}" title="查看来源 ${index}">${index}</button>`
  })
}
