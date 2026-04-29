<template>
  <div class="public-chat-view">
    <div class="public-chat-header">
      <div class="public-chat-title">AI 问答</div>
    </div>
    <div class="public-chat-body">
      <div class="chat-content-container">
        <div class="chat-main" ref="chatMessagesRef">
          <div class="chat-box">
            <div v-if="messages.length === 0" class="start-screen">
              <div class="start-content">
                <h2>👋 您好，有什么可以帮您？</h2>
                <div class="example-questions">
                  <div class="example-chips">
                    <div
                      v-for="(question, index) in exampleQuestions"
                      :key="index"
                      class="example-chip"
                      @click="handleExampleClick(question)"
                    >
                      {{ question }}
                    </div>
                  </div>
                </div>
              </div>
            </div>
            <div v-else class="messages-list">
              <div
                v-for="(message, index) in messages"
                :key="index"
                class="message" :class="message.role"
              >
                <div class="message-content">
                  <div class="message-role">{{ message.role === 'user' ? '您' : 'AI' }}</div>
                  <div class="message-text">{{ message.content }}</div>
                </div>
              </div>
            </div>
            <div class="generating-status" v-if="isLoading">
              <div class="generating-indicator">
                <div class="loading-dots">
                  <div></div>
                  <div></div>
                  <div></div>
                </div>
                <span class="generating-text">正在生成回复...</span>
              </div>
            </div>
          </div>
          <div class="bottom" :class="{ 'start-screen': messages.length === 0 }">
            <div class="message-input-wrapper">
              <div class="input-box">
                <textarea
                  ref="inputRef"
                  v-model="userInput"
                  class="user-input"
                  placeholder="问点什么？"
                  :disabled="isLoading"
                  @input="handleInput"
                  @keydown="handleKeyDown"
                ></textarea>
                <div class="send-button-container">
                  <a-tooltip :title="isLoading ? '停止回答' : ''">
                    <a-button
                      @click="handleSendOrStop"
                      :disabled="!userInput.trim() && !isLoading"
                      type="link"
                      class="send-button"
                    >
                      <template #icon>
                        <component :is="getIcon" class="send-btn" />
                      </template>
                    </a-button>
                  </a-tooltip>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, nextTick } from 'vue'
import { ArrowUpOutlined, PauseOutlined } from '@ant-design/icons-vue'
import { publicChatApi } from '@/apis/agent_api'

const userInput = ref('')
const messages = ref([])
const isLoading = ref(false)
const chatMessagesRef = ref(null)
const inputRef = ref(null)

const exampleQuestions = [
  '什么是人工智能？',
  '如何学习编程？',
  '今天天气怎么样？',
  '推荐一本好书',
  '如何保持健康？'
]

const scrollToBottom = async () => {
  await nextTick()
  if (chatMessagesRef.value) {
    chatMessagesRef.value.scrollTop = chatMessagesRef.value.scrollHeight
  }
}

const adjustTextareaHeight = () => {
  if (!inputRef.value) {
    return
  }

  const textarea = inputRef.value
  textarea.style.height = 'auto'
  textarea.style.height = `${textarea.scrollHeight}px`
}

const handleInput = (e) => {
  const value = e.target.value
  userInput.value = value
  nextTick(() => {
    adjustTextareaHeight()
  })
}

const handleKeyDown = (e) => {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    handleSendOrStop()
  }
}

const handleSendOrStop = async () => {
  if (isLoading.value) {
    isLoading.value = false
    return
  }

  const text = userInput.value.trim()
  if (!text) return

  messages.value.push({
    role: 'user',
    content: text
  })
  userInput.value = ''
  nextTick(() => {
    adjustTextareaHeight()
  })
  await scrollToBottom()

  isLoading.value = true
  try {
    const response = await publicChatApi.publicCall(text)
    messages.value.push({
      role: 'ai',
      content: response.response
    })
  } catch (error) {
    console.error('发送消息失败:', error)
    messages.value.push({
      role: 'ai',
      content: '抱歉，处理您的请求时出错，请稍后再试。'
    })
  } finally {
    isLoading.value = false
    await scrollToBottom()
  }
}

const handleExampleClick = (question) => {
  userInput.value = question
  nextTick(() => {
    adjustTextareaHeight()
  })
  handleSendOrStop()
}

const getIcon = computed(() => {
  return isLoading.value ? PauseOutlined : ArrowUpOutlined
})

onMounted(() => {
  nextTick(() => {
    if (inputRef.value) {
      adjustTextareaHeight()
      inputRef.value.focus()
    }
  })
})
</script>

<style lang="less" scoped>
@import '@/assets/css/main.css';

.public-chat-view {
  display: flex;
  flex-direction: column;
  width: 100%;
  height: 100vh;
  overflow: hidden;
  background: var(--gray-50);
}

.public-chat-header {
  user-select: none;
  z-index: 10;
  height: var(--header-height);
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 1rem 16px;
  flex-shrink: 0;
  background: var(--gray-0);
  border-bottom: 1px solid var(--gray-150);
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.05);

  .public-chat-title {
    font-size: 18px;
    font-weight: 500;
    color: var(--text-primary);
  }
}

.public-chat-body {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.chat-content-container {
  flex: 1;
  display: flex;
  flex-direction: row;
  overflow: hidden;
  position: relative;
  width: 100%;
  contain: layout;
}

.chat-main {
  flex: 1 1 0;
  display: flex;
  flex-direction: column;
  overflow-y: auto;
  position: relative;
  transition:
    flex-basis 0.3s cubic-bezier(0.4, 0, 0.2, 1),
    width 0.3s cubic-bezier(0.4, 0, 0.2, 1);
  min-width: 0;

  scrollbar-width: none;

  &::-webkit-scrollbar {
    display: none;
  }
}

.chat-box {
  width: 100%;
  max-width: 800px;
  margin: 0 auto;
  flex-grow: 1;
  padding: 1rem 1.5rem;
  display: flex;
  flex-direction: column;
}

.messages-list {
  flex: 1;
  display: flex;
  flex-direction: column;

  .message {
    margin-bottom: 16px;
    max-width: 100%;
    position: relative;
    display: flex;
    flex-direction: column;

    &.user {
      align-self: flex-end;
      max-width: 95%;

      .message-content {
        background: var(--main-50);
        border-radius: 0.5rem;
        padding: 0.5rem 1rem;
        position: relative;

        .message-role {
          font-size: 12px;
          font-weight: 500;
          margin-bottom: 4px;
          color: var(--main-700);
        }

        .message-text {
          font-size: 15px;
          line-height: 24px;
          color: var(--gray-1000);
        }
      }
    }

    &.ai {
      align-self: flex-start;
      width: 100%;
      text-align: left;
      margin: 0 0 16px 0;

      .message-content {
        width: 100%;
        background: var(--gray-0);
        border: 1px solid var(--gray-150);
        border-radius: 0.5rem;
        padding: 0.5rem 1rem;

        .message-role {
          font-size: 12px;
          font-weight: 500;
          margin-bottom: 4px;
          color: var(--gray-600);
        }

        .message-text {
          font-size: 15px;
          line-height: 24px;
          color: var(--gray-1000);
        }
      }
    }
  }
}

.generating-status {
  display: flex;
  justify-content: flex-start;
  padding: 1rem 0;
  animation: fadeInUp 0.4s ease-out;
  transition: all 0.2s;
}

.generating-indicator {
  display: flex;
  align-items: center;
  padding: 0.75rem 0rem;

  .generating-text {
    margin-left: 12px;
    font-size: 14px;
    font-weight: 500;
    letter-spacing: 0.025em;
    background: linear-gradient(
      90deg,
      var(--gray-700) 0%,
      var(--gray-700) 40%,
      var(--gray-300) 45%,
      var(--gray-200) 50%,
      var(--gray-300) 55%,
      var(--gray-700) 60%,
      var(--gray-700) 100%
    );
    background-size: 200% auto;
    -webkit-background-clip: text;
    background-clip: text;
    color: transparent;
    animation: waveFlash 2s linear infinite;
  }
}

.loading-dots {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 3px;

  div {
    width: 6px;
    height: 6px;
    background: linear-gradient(135deg, var(--main-color), var(--main-700));
    border-radius: 50%;
    animation: dotPulse 1.4s infinite ease-in-out both;

    &:nth-child(1) {
      animation-delay: -0.32s;
    }

    &:nth-child(2) {
      animation-delay: -0.16s;
    }

    &:nth-child(3) {
      animation-delay: 0s;
    }
  }
}

.bottom {
  position: sticky;
  bottom: 0;
  width: 100%;
  margin: 0 auto;
  padding: 4px 1rem 0 1rem;
  z-index: 1000;
  background: var(--gray-0);
  border-top: 1px solid var(--gray-150);

  .message-input-wrapper {
    width: 100%;
    max-width: 800px;
    margin: 0 auto;
  }

  &.start-screen {
    position: absolute;
    top: 45%;
    left: 50%;
    transform: translate(-50%, -50%);
    bottom: auto;
    max-width: 800px;
    width: 90%;
    background: transparent;
    padding: 0;
    border-top: none;
    z-index: 100;
  }
}

.input-box {
  display: grid;
  width: 100%;
  margin: 0 auto;
  border: 1px solid var(--gray-150);
  border-radius: 0.8rem;
  box-shadow: 0 2px 8px var(--shadow-1);
  transition: all 0.3s ease;
  background: var(--gray-0);
  gap: 0px;
  position: relative;
  padding: 0.8rem 0.75rem 0.6rem 0.75rem;
  grid-template-columns: auto 1fr;
  grid-template-rows: auto auto auto;
  grid-template-areas:
    'top top'
    'input input'
    'options send';

  .top-slot {
    display: flex;
    grid-area: top;
  }

  .expand-options {
    grid-area: options;
    justify-self: start;
    display: flex;
    align-items: center;
    gap: 8px;
  }

  .user-input {
    grid-area: input;
  }

  .send-button-container {
    grid-area: send;
    justify-self: end;
  }

  .bottom-slot {
    grid-column: 1 / -1;
  }
}

.user-input {
  grid-area: input;
  width: 100%;
  padding: 0;
  background-color: transparent;
  border: none;
  margin: 0;
  margin-bottom: 0.5rem;
  color: var(--gray-1000);
  font-size: 15px;
  outline: none;
  resize: none;
  line-height: 1.5;
  font-family: inherit;
  min-height: 44px;
  max-height: 200px;

  &:focus {
    outline: none;
    box-shadow: none;
  }

  &::placeholder {
    color: var(--gray-400);
  }
}

.send-button-container {
  grid-area: send;
  display: flex;
  align-items: center;
  justify-content: center;
}

.send-button.ant-btn-icon-only {
  height: 32px;
  width: 32px;
  cursor: pointer;
  background-color: var(--main-500);
  border-radius: 50%;
  border: none;
  transition: all 0.2s ease;
  box-shadow: 0 2px 6px var(--shadow-2);
  color: var(--gray-0);
  padding: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 14px;

  &:hover {
    background-color: var(--main-color);
    box-shadow: 0 4px 8px var(--shadow-3);
    color: var(--gray-0);
  }

  &:active {
    box-shadow: 0 2px 4px var(--shadow-2);
  }

  &:disabled {
    opacity: 0.5;
    cursor: not-allowed;
    transform: none;
    box-shadow: none;
  }

  .send-btn {
    font-size: 14px;
  }
}

.start-screen {
  text-align: center;

  .start-content {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 24px;

    h2 {
      font-size: 1.4rem;
      color: var(--gray-1000);
      margin: 0;
    }
  }
}

.example-questions {
  width: 100%;
  text-align: center;

  .example-chips {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    justify-content: center;

    .example-chip {
      padding: 6px 12px;
      background: var(--gray-25);
      border-radius: 16px;
      cursor: pointer;
      font-size: 0.8rem;
      color: var(--gray-700);
      transition: all 0.15s ease;
      white-space: nowrap;
      max-width: 200px;
      overflow: hidden;
      text-overflow: ellipsis;

      &:hover {
        background: var(--main-25);
        color: var(--main-700);
        transform: translateY(-1px);
      }
    }
  }
}

@keyframes dotPulse {
  0%, 80%, 100% {
    transform: scale(0);
  }
  40% {
    transform: scale(1);
  }
}

@keyframes waveFlash {
  0% {
    background-position: 200% center;
  }
  100% {
    background-position: -200% center;
  }
}

@keyframes fadeInUp {
  from {
    opacity: 0;
    transform: translateY(10px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

// 响应式设计
@media (max-width: 768px) {
  .public-chat-body {
    padding: 0;
  }

  .chat-box {
    padding: 16px;
  }

  .messages-list .message.user .message-content {
    max-width: 85%;
  }

  .bottom {
    padding: 16px;

    &.start-screen {
      padding: 16px 0;
    }
  }

  .message-input-wrapper {
    max-width: 100%;
  }

  .example-questions {
    max-width: 100%;
    padding: 16px 0;

    .example-chips {
      gap: 6px;

      .example-chip {
        padding: 5px 10px;
        font-size: 12px;
      }
    }
  }
}
</style>