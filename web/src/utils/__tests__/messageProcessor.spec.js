import assert from 'node:assert/strict'

import { MessageProcessor } from '../messageProcessor.js'

const databases = [{ kb_id: 'db-1', name: '财税库' }, { name: 'DifyKB' }, { name: 'LightGraphKB' }]

const run = () => {
  const conv = {
    messages: [
      {
        type: 'ai',
        tool_calls: [
          {
            name: '财税库',
            tool_call_result: {
              content: JSON.stringify([
                {
                  content: 'A',
                  score: 0.9,
                  metadata: { source: 'doc-a', chunk_id: 'c1', file_id: 'f1', chunk_index: 1 }
                },
                {
                  content: 'A',
                  score: 0.8,
                  metadata: { source: 'doc-a', chunk_id: 'c1', file_id: 'f1', chunk_index: 1 }
                }
              ])
            }
          },
          {
            name: 'LightGraphKB',
            tool_call_result: {
              content: JSON.stringify({
                data: {
                  chunks: [
                    {
                      content: 'B',
                      score: 0.4,
                      metadata: { source: 'doc-b', chunk_id: 'c2', file_id: 'f2', chunk_index: 2 }
                    }
                  ]
                }
              })
            }
          },
          {
            name: 'not_kb_tool',
            tool_call_result: {
              content: JSON.stringify([{ content: 'X', score: 0.99, metadata: { chunk_id: 'cx' } }])
            }
          },
          {
            name: 'DifyKB',
            tool_call_result: { content: 'not-json' }
          }
        ]
      }
    ]
  }

  const chunks = MessageProcessor.extractKnowledgeChunksFromConversation(conv, databases)

  // 1. Milvus/Dify 数组提取
  assert.equal(
    chunks.some((c) => c.content === 'A' && c.kb_name === '财税库'),
    true
  )

  // 2. 对象包装的 data.chunks 提取
  assert.equal(
    chunks.some((c) => c.content === 'B' && c.kb_name === 'LightGraphKB'),
    true
  )

  // 3. 非知识库工具忽略
  assert.equal(
    chunks.some((c) => c.content === 'X'),
    false
  )

  // 4. 非法 JSON 自动跳过
  assert.equal(
    chunks.some((c) => c.kb_name === 'DifyKB'),
    false
  )

  // 5. 去重生效（chunk_id=c1 仅一条）
  assert.equal(chunks.filter((c) => c.metadata?.chunk_id === 'c1').length, 1)

  // 6. 分数排序（A 0.9 在 B 0.4 前）
  const idxA = chunks.findIndex((c) => c.content === 'A')
  const idxB = chunks.findIndex((c) => c.content === 'B')
  assert.equal(idxA < idxB, true)

  const unifiedSources = MessageProcessor.extractSourcesFromConversation(
    {
      messages: [
        {
          type: 'ai',
          content:
            '网页依据<cite source="https://example.com/source"></cite>，制度依据<cite source="kb://db-1/file-9?chunk=chunk-9"></cite>',
          tool_calls: [
            {
              name: 'query_kb',
              tool_call_result: {
                content: JSON.stringify({
                  kb_id: 'db-1',
                  results: [
                    {
                      id: 'chunk-9',
                      kb_id: 'db-1',
                      file_id: 'file-9',
                      content: '知识库新结构',
                      citation_source: 'kb://db-1/file-9?chunk=chunk-9',
                      metadata: { source: '制度.pdf' }
                    }
                  ]
                })
              }
            },
            {
              name: 'tavily_search',
              tool_call_result: {
                content: JSON.stringify({
                  results: [
                    {
                      title: '官方网页',
                      url: 'https://example.com/source',
                      content: '网络来源'
                    }
                  ]
                })
              }
            }
          ]
        }
      ]
    },
    databases
  )

  assert.equal(unifiedSources.knowledgeChunks[0].kb_name, '财税库')
  assert.equal(unifiedSources.webSources[0].citation_index, 1)
  assert.equal(unifiedSources.knowledgeChunks[0].citation_index, 2)

  const citedOpenSource = 'kb://db-1/file-open?lines=2001-2100'
  const openWindowSources = MessageProcessor.extractSourcesFromConversation(
    {
      messages: [
        {
          type: 'ai',
          content: `采用的全文范围<cite source="${citedOpenSource}"></cite>`,
          tool_calls: [
            {
              name: 'open_kb_document',
              tool_call_result: {
                content: JSON.stringify({
                  kb_id: 'db-1',
                  file_id: 'file-open',
                  citation_source: citedOpenSource,
                  content: '正文实际引用的阅读窗口',
                  start_line: 2001,
                  end_line: 2100
                })
              }
            },
            {
              name: 'open_kb_document',
              tool_call_result: {
                content: JSON.stringify({
                  kb_id: 'db-1',
                  file_id: 'file-open',
                  citation_source: 'kb://db-1/file-open?lines=1-2000',
                  content: '未被正文引用的大范围阅读窗口',
                  start_line: 1,
                  end_line: 2000
                })
              }
            }
          ]
        }
      ]
    },
    databases
  )

  assert.equal(openWindowSources.knowledgeChunks.length, 1)
  assert.equal(openWindowSources.knowledgeChunks[0].citation_source, citedOpenSource)
  assert.equal(openWindowSources.knowledgeChunks[0].source_type, 'document_window')
  assert.equal(openWindowSources.knowledgeChunks[0].citation_index, 1)

  const previousConversation = {
    messages: [
      {
        type: 'ai',
        tool_calls: [
          {
            name: 'query_kb',
            tool_call_result: {
              content: JSON.stringify({
                kb_id: 'db-1',
                results: [
                  {
                    id: 'chunk-history',
                    kb_id: 'db-1',
                    file_id: 'file-history',
                    content: '前一轮已经核验的知识库依据',
                    citation_source: 'kb://db-1/file-history?chunk=chunk-history',
                    metadata: { source: '历史教材.pdf' }
                  },
                  {
                    id: 'chunk-unused',
                    kb_id: 'db-1',
                    file_id: 'file-history',
                    content: '本轮没有引用的历史候选',
                    citation_source: 'kb://db-1/file-history?chunk=chunk-unused'
                  }
                ]
              })
            }
          }
        ]
      }
    ]
  }
  const followUpSources = MessageProcessor.extractSourcesFromConversation(
    {
      messages: [
        {
          type: 'ai',
          content:
            '历史依据<cite source="kb://db-1/file-history?chunk=chunk-history"></cite>，网页补充<cite source="https://example.com/follow-up"></cite>',
          tool_calls: [
            {
              name: 'tavily_search',
              tool_call_result: {
                content: JSON.stringify({
                  results: [
                    {
                      title: '追问网页',
                      url: 'https://example.com/follow-up',
                      content: '本轮网络补充'
                    }
                  ]
                })
              }
            }
          ]
        }
      ]
    },
    databases,
    [previousConversation]
  )

  assert.equal(followUpSources.knowledgeChunks.length, 1)
  assert.equal(followUpSources.knowledgeChunks[0].content, '前一轮已经核验的知识库依据')
  assert.equal(followUpSources.knowledgeChunks[0].citation_index, 1)
  assert.equal(followUpSources.webSources[0].citation_index, 2)

  const manifest = JSON.stringify({
    version: 1,
    knowledge_chunks: [
      {
        id: 'chunk-large',
        kb_id: 'db-1',
        file_id: 'file-large',
        content: '大型工具结果中的教材依据',
        citation_source: 'kb://db-1/file-large?chunk=chunk-large',
        metadata: { source: '教材.pdf' }
      }
    ]
  })
  const offloadedSources = MessageProcessor.extractSourcesFromConversation(
    {
      messages: [
        {
          type: 'ai',
          tool_calls: [
            {
              name: 'query_kb',
              tool_call_result: {
                content: `Tool result was saved to /large-results/call-query\n\n<yuxi-citation-manifest-v1>${manifest}</yuxi-citation-manifest-v1>`
              }
            }
          ]
        },
        {
          type: 'ai',
          content:
            '教材结论<cite source="kb://db-1/file-large?chunk=chunk-large"></cite>'
        }
      ]
    },
    databases
  )

  assert.equal(offloadedSources.knowledgeChunks.length, 1)
  assert.equal(offloadedSources.knowledgeChunks[0].content, '大型工具结果中的教材依据')
  assert.equal(offloadedSources.knowledgeChunks[0].citation_index, 1)

  const liveMessages = MessageProcessor.convertToolResultToMessages([
    {
      type: 'ai',
      tool_calls: [{ id: 'call-query', name: 'query_kb', args: {} }]
    },
    {
      type: 'tool',
      tool_call_id: 'call-query',
      content: `Tool result was saved\n\n<yuxi-citation-manifest-v1>${manifest}</yuxi-citation-manifest-v1>`
    },
    {
      type: 'ai',
      content: '流式完成正文<cite source="kb://db-1/file-large?chunk=chunk-large"></cite>'
    }
  ]).filter((item) => item.type !== 'tool')
  const liveSources = MessageProcessor.extractSourcesFromConversation(
    { messages: liveMessages },
    databases
  )
  assert.equal(liveSources.knowledgeChunks.length, 1)
  assert.equal(liveSources.knowledgeChunks[0].citation_index, 1)

  const uncitedSources = MessageProcessor.assignCitationIndexes({
    knowledgeChunks: [
      { citation_source: 'kb://db-1/file-1?chunk=unused', content: '仅检索未引用' }
    ],
    citedSources: []
  })
  assert.equal(uncitedSources.knowledgeChunks[0].citation_index, null)

  const conversations = MessageProcessor.convertServerHistoryToMessages([
    { type: 'human', content: '请选择语言' },
    { type: 'ai', content: '请选择输出语言' },
    {
      type: 'human',
      content: '{"language":"python"}',
      extra_metadata: { source: 'ask_user_question_resume' }
    },
    { type: 'ai', content: '这是 Python 版本' }
  ])

  assert.equal(conversations.length, 1)
  assert.equal(conversations[0].messages.length, 3)
  assert.equal(conversations[0].messages.at(-1).content, '这是 Python 版本')
  assert.equal(conversations[0].messages.at(-1).isLast, true)
  assert.equal(conversations[0].status, 'finished')

  const assistantBody = MessageProcessor.parseAssistantMessageBody({
    type: 'ai',
    content: '<think>推理过程</think>最终答案'
  })
  assert.deepEqual(assistantBody, { content: '最终答案', reasoningContent: '推理过程' })

  console.log('messageProcessor extractKnowledgeChunksFromConversation: all assertions passed')
}

run()
