const BROKEN_QUOTED_STRONG_PATTERN =
  /\*\*((?:"[^"\n]+"|'[^'\n]+'|“[^”\n]+”|‘[^’\n]+’)[^*\n]*?)\*\*/g
const INLINE_CODE_PATTERN = /(`+)([\s\S]*?)\1/g
const FENCE_PATTERN = /^\s{0,3}(`{3,}|~{3,})/

const normalizeTextSegment = (content) =>
  content.replace(BROKEN_QUOTED_STRONG_PATTERN, '<strong>$1</strong>')

const normalizeLine = (line) => {
  let output = ''
  let cursor = 0

  for (const match of line.matchAll(INLINE_CODE_PATTERN)) {
    output += normalizeTextSegment(line.slice(cursor, match.index))
    output += match[0]
    cursor = match.index + match[0].length
  }

  return output + normalizeTextSegment(line.slice(cursor))
}

// Model output occasionally wraps a quoted phrase in emphasis delimiters that
// CommonMark treats as plain text. Normalize only prose outside code regions.
export const normalizeBrokenMarkdownEmphasis = (content) => {
  let fence = null

  return String(content || '')
    .split('\n')
    .map((line) => {
      const fenceMatch = line.match(FENCE_PATTERN)
      if (fenceMatch) {
        const marker = fenceMatch[1]
        if (!fence) {
          fence = { character: marker[0], length: marker.length }
        } else if (marker[0] === fence.character && marker.length >= fence.length) {
          fence = null
        }
        return line
      }

      return fence ? line : normalizeLine(line)
    })
    .join('\n')
}
