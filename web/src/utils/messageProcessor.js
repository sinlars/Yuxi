import { extractCitationSources } from './sourceCitations.js'

const CITATION_MANIFEST_PATTERN =
  /<yuxi-citation-manifest-v1>([\s\S]*?)<\/yuxi-citation-manifest-v1>/

const parseJsonContent = (content) => {
  if (Array.isArray(content)) return content
  if (content && typeof content === 'object') return content
  if (typeof content !== 'string') return null
  try {
    return JSON.parse(content)
  } catch {
    return null
  }
}

const parseCitationManifest = (content) => {
  // 引用清单由后端在大型结果落盘前生成，刷新后无需解析沙箱文件或截断 JSON。
  if (typeof content !== 'string') return null
  const match = content.match(CITATION_MANIFEST_PATTERN)
  if (!match) return null
  try {
    const manifest = JSON.parse(match[1])
    return manifest?.version === 1 ? manifest : null
  } catch {
    return null
  }
}

/**
 * 消息处理工具类
 */
export class MessageProcessor {
  /**
   * 将工具结果与消息合并
   * @param {Array} msgs - 消息数组
   * @returns {Array} 合并后的消息数组
   */
  static convertToolResultToMessages(msgs) {
    const toolResponseMap = new Map()

    // 构建工具响应映射
    for (const item of msgs) {
      if (item.type === 'tool') {
        // 使用多种可能的ID字段来匹配工具调用
        const toolCallId = item.tool_call_id || item.id
        if (toolCallId) {
          toolResponseMap.set(toolCallId, item)
        }
      }
    }

    // 合并工具调用和响应
    const convertedMsgs = msgs.map((item) => {
      if (item.type === 'ai' && item.tool_calls && item.tool_calls.length > 0) {
        return {
          ...item,
          tool_calls: item.tool_calls.map((toolCall) => {
            const toolResponse = toolResponseMap.get(toolCall.id)
            return {
              ...toolCall,
              tool_call_result: toolResponse || null
            }
          })
        }
      }
      return item
    })

    return convertedMsgs
  }

  /**
   * 将服务器历史记录转换为对话格式
   * @param {Array} serverHistory - 服务器历史记录
   * @returns {Array} 对话数组
   */
  static convertServerHistoryToMessages(serverHistory) {
    // Filter out standalone 'tool' messages since tool results are already in AI messages' tool_calls
    // Backend new storage: tool results are embedded in AI messages' tool_calls array with tool_call_result field
    const filteredHistory = serverHistory.filter(
      (item) =>
        item.type !== 'tool' &&
        !(item.type === 'human' && item.extra_metadata?.source === 'ask_user_question_resume')
    )

    // 按照对话分组
    const conversations = []
    let currentConv = null

    for (const item of filteredHistory) {
      if (item.type === 'human') {
        // Start new conversation, finalize previous one
        if (currentConv) {
          // Find the last AI message and mark it as final
          for (let i = currentConv.messages.length - 1; i >= 0; i--) {
            if (currentConv.messages[i].type === 'ai') {
              currentConv.messages[i].isLast = true
              currentConv.status = 'finished'
              break
            }
          }
        }
        currentConv = {
          messages: [item],
          status: 'loading'
        }
        conversations.push(currentConv)
      } else if (item.type === 'ai' && currentConv) {
        currentConv.messages.push(item)
      }
    }

    // Mark the last conversation as finished
    if (currentConv && currentConv.messages.length > 0) {
      // Find the last AI message and mark it as final
      for (let i = currentConv.messages.length - 1; i >= 0; i--) {
        if (currentConv.messages[i].type === 'ai') {
          currentConv.messages[i].isLast = true
          currentConv.status = 'finished'
          break
        }
      }
    }

    return conversations
  }

  /**
   * 提取一轮对话中所有知识库检索块
   * @param {Object} conv - 单轮对话
   * @param {Array} databases - 知识库列表
   * @returns {Array} 归一化后的检索块
   */
  static extractKnowledgeChunksFromConversation(conv, databases = []) {
    if (!conv || !Array.isArray(conv.messages) || conv.messages.length === 0) return []

    const databaseById = new Map(
      (databases || [])
        .filter((db) => db && (db.kb_id || db.id))
        .map((db) => [String(db.kb_id || db.id), db])
    )
    const databaseNames = new Set(
      (databases || [])
        .map((db) => db?.name)
        .filter((name) => typeof name === 'string' && name.trim())
    )
    const normalizedChunks = []
    const dedupSet = new Set()

    const appendChunk = (
      chunk,
      { kbId = '', kbName = '', fileId = '', toolName = '' } = {}
    ) => {
      if (!chunk || typeof chunk !== 'object') return
      const content = typeof chunk.content === 'string' ? chunk.content.trim() : ''
      if (!content) return

      const metadata = chunk.metadata && typeof chunk.metadata === 'object' ? chunk.metadata : {}
      const resolvedKbId = String(chunk.kb_id || kbId || '')
      const resolvedKb = databaseById.get(resolvedKbId)
      const resolvedKbName = resolvedKb?.name || kbName || resolvedKbId || '知识库'
      const resolvedFileId = String(chunk.file_id || metadata.file_id || fileId || '')
      const citationSource = String(chunk.citation_source || metadata.citation_source || '')
      const chunkId = String(chunk.id || metadata.chunk_id || '')
      const dedupKey = citationSource || `${resolvedKbId}::${chunkId || content}`
      if (dedupSet.has(dedupKey)) return
      dedupSet.add(dedupKey)

      const score =
        typeof chunk.score === 'number'
          ? chunk.score
          : typeof metadata.score === 'number'
            ? metadata.score
            : null
      const source =
        metadata.source ||
        metadata.file_name ||
        metadata.filename ||
        chunk.file_name ||
        (resolvedFileId ? `${resolvedKbName} / ${resolvedFileId}` : resolvedKbName)
      normalizedChunks.push({
        kb_id: resolvedKbId,
        kb_name: resolvedKbName,
        file_id: resolvedFileId,
        citation_source: citationSource,
        source_type: toolName === 'open_kb_document' ? 'document_window' : 'retrieval_chunk',
        content,
        score,
        metadata: {
          ...metadata,
          source,
          file_id: resolvedFileId,
          chunk_id: chunkId,
          chunk_index: metadata.chunk_index,
          start_line: chunk.start_line || metadata.start_line,
          end_line: chunk.end_line || metadata.end_line
        }
      })
    }

    const appendStructuredKnowledgeOutput = (parsed, toolName) => {
      if (!parsed || typeof parsed !== 'object') return false

      if (Array.isArray(parsed.results)) {
        for (const chunk of parsed.results) appendChunk(chunk, { kbId: parsed.kb_id, toolName })
        return true
      }
      if (Array.isArray(parsed.windows)) {
        for (const window of parsed.windows) {
          appendChunk(window, { kbId: parsed.kb_id, fileId: parsed.file_id, toolName })
        }
        return true
      }
      if (typeof parsed.content === 'string' && parsed.citation_source) {
        appendChunk(parsed, { kbId: parsed.kb_id, fileId: parsed.file_id, toolName })
        return true
      }
      return false
    }

    for (const msg of conv.messages) {
      if (!msg || msg.type !== 'ai' || !Array.isArray(msg.tool_calls)) continue

      for (const toolCall of msg.tool_calls) {
        const toolName = toolCall?.name || toolCall?.function?.name || ''

        const content = toolCall?.tool_call_result?.content
        const manifest = parseCitationManifest(content)
        for (const chunk of manifest?.knowledge_chunks || []) appendChunk(chunk, { toolName })

        const parsed = parseJsonContent(content)
        if (!parsed) continue

        if (toolName === 'query_kb' && appendStructuredKnowledgeOutput(parsed, toolName)) {
          continue
        }

        if (toolName === 'find_kb_document' && appendStructuredKnowledgeOutput(parsed, toolName)) {
          continue
        }

        if (toolName === 'open_kb_document' && appendStructuredKnowledgeOutput(parsed, toolName)) {
          continue
        }

        if (!databaseNames.has(toolName)) continue

        // 兼容旧知识库工具：工具名为知识库名称，结果直接返回 chunks。
        if (Array.isArray(parsed)) {
          for (const chunk of parsed) appendChunk(chunk, { kbName: toolName })
          continue
        }

        const wrappedChunks = parsed?.data?.chunks
        if (Array.isArray(wrappedChunks)) {
          for (const chunk of wrappedChunks) appendChunk(chunk, { kbName: toolName })
        }
      }
    }

    normalizedChunks.sort((a, b) => {
      const scoreA = typeof a.score === 'number' ? a.score : Number.NEGATIVE_INFINITY
      const scoreB = typeof b.score === 'number' ? b.score : Number.NEGATIVE_INFINITY
      return scoreB - scoreA
    })

    return normalizedChunks
  }

  /**
   * 提取一轮对话中的网络搜索来源
   * @param {Object} conv - 单轮对话
   * @returns {Array} 归一化后的网络来源
   */
  static extractWebSourcesFromConversation(conv) {
    if (!conv || !Array.isArray(conv.messages) || conv.messages.length === 0) return []

    const webSources = []
    const dedupSet = new Set()

    for (const msg of conv.messages) {
      if (!msg || msg.type !== 'ai' || !Array.isArray(msg.tool_calls)) continue

      for (const toolCall of msg.tool_calls) {
        const toolName = (toolCall?.name || toolCall?.function?.name || '').toLowerCase()
        if (!toolName.includes('tavily_search')) continue

        const content = toolCall?.tool_call_result?.content
        const manifest = parseCitationManifest(content)
        const parsed = parseJsonContent(content)
        const results = Array.isArray(parsed?.results)
          ? parsed.results
          : Array.isArray(manifest?.web_sources)
            ? manifest.web_sources
            : []
        if (results.length === 0) continue

        for (const item of results) {
          const title = typeof item?.title === 'string' ? item.title.trim() : ''
          const url = typeof item?.url === 'string' ? item.url.trim() : ''
          if (!title || !url) continue
          if (dedupSet.has(url)) continue
          dedupSet.add(url)

          webSources.push({
            tool_name: toolCall?.name || toolCall?.function?.name || '网络搜索',
            title,
            url,
            citation_source: url,
            score: typeof item?.score === 'number' ? item.score : null,
            content: typeof item?.content === 'string' ? item.content : '',
            published_date: typeof item?.published_date === 'string' ? item.published_date : ''
          })
        }
      }
    }

    webSources.sort((a, b) => {
      const scoreA = typeof a.score === 'number' ? a.score : Number.NEGATIVE_INFINITY
      const scoreB = typeof b.score === 'number' ? b.score : Number.NEGATIVE_INFINITY
      return scoreB - scoreA
    })

    return webSources
  }

  /**
   * 提取单个消息中的来源
   * @param {Object} message - 消息对象
   * @param {Array} databases - 知识库列表
   * @returns {{knowledgeChunks: Array, webSources: Array}}
   */
  static extractSourcesFromMessage(message, databases = []) {
    if (!message || message.type !== 'ai') return { knowledgeChunks: [], webSources: [] }

    // 复用提取逻辑，通过构建临时对话对象
    const mockConv = { messages: [message] }
    const citedSources = extractCitationSources(message.content)
    const citedSourceSet = new Set(citedSources)
    return MessageProcessor.assignCitationIndexes({
      knowledgeChunks: MessageProcessor.extractKnowledgeChunksFromConversation(
        mockConv,
        databases
      ).filter(
        (source) =>
          source.source_type !== 'document_window' ||
          citedSourceSet.has(source.citation_source)
      ),
      webSources: MessageProcessor.extractWebSourcesFromConversation(mockConv),
      citedSources
    })
  }

  /**
   * 提取一轮对话中的全部来源（知识库+网络搜索）
   * @param {Object} conv - 单轮对话
   * @param {Array} databases - 知识库列表
   * @returns {{knowledgeChunks: Array, webSources: Array}}
   */
  static extractSourcesFromConversation(conv, databases = [], previousConversations = []) {
    const citedSources = []
    for (const message of conv?.messages || []) {
      if (message?.type === 'ai') citedSources.push(...extractCitationSources(message.content))
    }

    const citedSourceSet = new Set(citedSources)
    const knowledgeChunks = MessageProcessor.extractKnowledgeChunksFromConversation(
      conv,
      databases
    ).filter(
      (source) =>
        source.source_type !== 'document_window' || citedSourceSet.has(source.citation_source)
    )
    const webSources = MessageProcessor.extractWebSourcesFromConversation(conv)
    const knownKnowledgeSources = new Set(
      knowledgeChunks.map((source) => source.citation_source).filter(Boolean)
    )
    const knownWebSources = new Set(webSources.map((source) => source.citation_source).filter(Boolean))

    // 后续追问可能直接沿用前一轮已经核验过的引用标识。仅补回本轮正文实际引用的
    // 历史来源，避免把整段会话中的所有检索候选都混入当前来源面板。
    for (const previousConv of previousConversations || []) {
      for (const source of MessageProcessor.extractKnowledgeChunksFromConversation(
        previousConv,
        databases
      )) {
        if (
          citedSourceSet.has(source.citation_source) &&
          !knownKnowledgeSources.has(source.citation_source)
        ) {
          knowledgeChunks.push(source)
          knownKnowledgeSources.add(source.citation_source)
        }
      }
      for (const source of MessageProcessor.extractWebSourcesFromConversation(previousConv)) {
        if (citedSourceSet.has(source.citation_source) && !knownWebSources.has(source.citation_source)) {
          webSources.push(source)
          knownWebSources.add(source.citation_source)
        }
      }
    }

    return MessageProcessor.assignCitationIndexes({
      knowledgeChunks,
      webSources,
      citedSources
    })
  }

  static assignCitationIndexes({ knowledgeChunks = [], webSources = [], citedSources = [] } = {}) {
    const availableSources = new Set(
      [...knowledgeChunks, ...webSources]
        .map((source) => String(source?.citation_source || ''))
        .filter(Boolean)
    )
    const indexBySource = new Map()
    for (const citationSource of citedSources) {
      if (!availableSources.has(citationSource) || indexBySource.has(citationSource)) continue
      indexBySource.set(citationSource, indexBySource.size + 1)
    }
    const assign = (source) => {
      const citationSource = String(source?.citation_source || '')
      if (!citationSource) return { ...source, citation_index: null }
      return { ...source, citation_index: indexBySource.get(citationSource) || null }
    }

    return {
      knowledgeChunks: knowledgeChunks.map(assign),
      webSources: webSources.map(assign)
    }
  }

  /**
   * 解析助手消息正文与推理内容，保持渲染和列表拆分使用同一套规则。
   * @param {Object} message - AI 消息对象
   * @returns {{content: string, reasoningContent: string}}
   */
  static parseAssistantMessageBody(message) {
    let content = typeof message?.content === 'string' ? message.content.trim() : ''
    let reasoningContent = message?.additional_kwargs?.reasoning_content || ''

    if (!reasoningContent && content) {
      const thinkRegex = /<think>(.*?)<\/think>|<think>(.*?)$/s
      const thinkMatch = content.match(thinkRegex)

      if (thinkMatch) {
        reasoningContent = (thinkMatch[1] || thinkMatch[2] || '').trim()
        content = content.replace(thinkMatch[0], '').trim()
      }
    }

    return { content, reasoningContent }
  }

  /**
   * 合并消息块
   * @param {Array} chunks - 消息块数组
   * @returns {Object|null} 合并后的消息
   */
  static mergeMessageChunk(chunks) {
    if (chunks.length === 0) return null

    // 深拷贝第一个chunk作为结果
    const result = JSON.parse(JSON.stringify(chunks[0]))

    // 处理用户消息的内容格式 - 确保显示纯文本
    if (result.type === 'human' || result.role === 'user') {
      // 如果content是数组格式（LangChain多模态消息），提取文本部分
      if (Array.isArray(result.content)) {
        const textPart = result.content.find((item) => item.type === 'text')
        result.content = textPart ? textPart.text : ''
      } else {
        result.content = result.content || ''
      }
    } else {
      result.content = result.content || ''
    }

    // 合并后续chunks
    for (let i = 1; i < chunks.length; i++) {
      const chunk = chunks[i]

      // 合并内容
      if (chunk.content) {
        result.content += chunk.content
      }

      // 合并reasoning_content
      if (chunk.reasoning_content) {
        if (!result.reasoning_content) {
          result.reasoning_content = ''
        }
        result.reasoning_content += chunk.reasoning_content
      }

      // 合并additional_kwargs中的reasoning_content
      if (chunk.additional_kwargs?.reasoning_content) {
        if (!result.additional_kwargs) result.additional_kwargs = {}
        if (!result.additional_kwargs.reasoning_content) {
          result.additional_kwargs.reasoning_content = ''
        }
        result.additional_kwargs.reasoning_content += chunk.additional_kwargs.reasoning_content
      }

      // 合并tool_calls (处理新的数据结构)
      MessageProcessor._mergeToolCalls(result, chunk)
    }

    // 处理AIMessageChunk类型
    if (result.type === 'AIMessageChunk') {
      result.type = 'ai'
    }

    return result
  }

  /**
   * 合并工具调用
   * @private
   * @param {Object} result - 结果对象
   * @param {Object} chunk - 当前块
   */
  static _mergeToolCalls(result, chunk) {
    if (chunk.tool_call_chunks && chunk.tool_call_chunks.length > 0) {
      // 确保 result 有 tool_calls 数组
      if (!result.tool_calls) result.tool_calls = []

      for (const toolCallChunk of chunk.tool_call_chunks) {
        // 使用 index 来标识工具调用（因为可能有多个工具调用）
        const existingToolCallIndex = result.tool_calls.findIndex(
          (t) => t.index === toolCallChunk.index
        )

        if (existingToolCallIndex !== -1) {
          // 合并相同index的tool call
          const existingToolCall = result.tool_calls[existingToolCallIndex]

          // 更新名称和ID（如果存在）
          if (toolCallChunk.name && !existingToolCall.function?.name) {
            if (!existingToolCall.function) existingToolCall.function = {}
            existingToolCall.function.name = toolCallChunk.name
          }

          if (toolCallChunk.id && !existingToolCall.id) {
            existingToolCall.id = toolCallChunk.id
          }

          // 合并参数
          if (toolCallChunk.args) {
            if (!existingToolCall.function) existingToolCall.function = {}
            if (!existingToolCall.function.arguments) existingToolCall.function.arguments = ''
            existingToolCall.function.arguments += toolCallChunk.args
          }
        } else {
          // 添加新的tool call
          const newToolCall = {
            index: toolCallChunk.index,
            id: toolCallChunk.id,
            function: {
              name: toolCallChunk.name || null,
              arguments: toolCallChunk.args || ''
            }
          }
          result.tool_calls.push(newToolCall)
        }
      }
    }
  }
}

export default MessageProcessor
