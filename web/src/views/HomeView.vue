<template>
  <div class="home-container">
    <!-- 加载中状态 -->
    <div v-if="isLoading" class="loading-container">
      <a-spin size="large" />
      <p class="loading-text">正在连接服务...</p>
    </div>

    <!-- 错误状态 -->
    <div v-else-if="error" class="error-container">
      <a-result status="error" :title="error.title" :sub-title="error.message">
        <template #extra>
          <a-button type="primary" @click="retryLoad">重试</a-button>
          <a-button :href="docsUrl" target="_blank" rel="noopener noreferrer">常见问题</a-button>
        </template>
      </a-result>
    </div>

    <!-- 正常内容 -->
    <template v-else>
      <!-- 氛围装饰背景：浅色极光 + 心电监护线 -->
      <div class="ambient" aria-hidden="true">
        <span class="glow glow-blue"></span>
        <span class="glow glow-teal-left"></span>
        <span class="glow glow-teal-right"></span>
        <svg
          class="ecg-line"
          viewBox="0 0 1440 240"
          preserveAspectRatio="xMidYMid slice"
          xmlns="http://www.w3.org/2000/svg"
        >
          <defs>
            <!-- 基础波形：左端浅、右端略深 -->
            <linearGradient id="ecg-gradient" x1="0" y1="0" x2="1" y2="0">
              <stop offset="0" stop-color="#9aafff" stop-opacity="0.18" />
              <stop offset="0.5" stop-color="#7d94ff" stop-opacity="0.16" />
              <stop offset="1" stop-color="#4d6bfe" stop-opacity="0.14" />
            </linearGradient>
          </defs>
          <path
            class="ecg-path"
            d="M0 120 H420 L460 120 L485 70 L520 175 L555 25 L590 185 L615 120 H700 L735 120 L760 85 L800 155 L825 120 H1000 L1040 120 L1065 75 L1100 165 L1125 120 H1440"
            stroke="url(#ecg-gradient)"
          />
          <!-- 流动高亮段：经过处加深，经过后恢复 -->
          <path
            class="ecg-sweep"
            pathLength="1000"
            d="M0 120 H420 L460 120 L485 70 L520 175 L555 25 L590 185 L615 120 H700 L735 120 L760 85 L800 155 L825 120 H1000 L1040 120 L1065 75 L1100 165 L1125 120 H1440"
          />
          <circle class="ecg-dot ecg-dot-blue" cx="555" cy="25" r="3.5" />
          <circle class="ecg-dot ecg-dot-teal" cx="590" cy="185" r="3.5" />
        </svg>
      </div>

      <!-- 顶部导航 -->
      <header class="site-header">
        <div class="logo">
          <img class="logo-img" :src="medLogo" alt="中科医云" />
          <span class="logo-text">{{ infoStore.organization.name || '中科医云·AI智能医学助手' }}</span>
        </div>
        <div class="header-actions">
          <UserInfoComponent :show-button="true" />
        </div>
      </header>

      <!-- Hero -->
      <main class="hero-section">
        <div class="hero-content">
          <p class="hero-eyebrow reveal-up">
            <span class="eyebrow-dot"></span>权威案例 · 医学知识库 × 智能体
          </p>
          <h1 class="title reveal-up delay-1">
            <span class="title-line">探索</span><span class="title-line title-accent">医学知识</span><span class="title-line">的边界</span>
          </h1>
          <div class="subtitle-wrap reveal-up delay-1">
            <Transition name="subtitle-switch">
              <p v-if="currentSubtitle" class="subtitle" :key="currentSubtitle">
                {{ currentSubtitle }}
              </p>
            </Transition>
          </div>
          <div class="hero-actions reveal-up delay-2">
            <button class="button-base primary" @click="goToChat">
              <span>开始体验</span>
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
                <path d="M5 12H19M19 12L13 6M19 12L13 18" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" />
              </svg>
            </button>
            <a
              class="button-base secondary"
              :href="docsUrl"
              target="_blank"
              rel="noopener noreferrer"
            >
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
                <path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20M6.5 2H20V22H6.5A2.5 2.5 0 0 1 4 19.5V2Z" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" />
              </svg>
              <span>其他产品</span>
            </a>
          </div>
          <p class="trust-line reveal-up delay-2">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
              <path d="M2 12H7L9 6L13 18L15 12H22" stroke="#4d6bfe" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" />
            </svg>
            循证医学 · 临床指南 · 科研文献 · 智能问答
          </p>
        </div>
      </main>

      <!-- 底部 -->
      <footer class="site-footer">
        <div class="footer-content">
          <div class="footer-left">
            <span class="footer-copy">{{ infoStore.footer?.copyright || '2025 中科医云·中科数字出版传媒有限公司' }}</span>
            <span class="footer-icp-gap"></span>
            <a
              class="footer-icp-link"
              href="https://beian.miit.gov.cn/"
              target="_blank"
              rel="noopener noreferrer"
            >京ICP备17034810号</a>
            <span class="footer-icp-gap"></span>
            <a
              class="footer-icp-link"
              href="https://beian.mps.gov.cn/#/query/webSearch?code=11010102002940"
              target="_blank"
              rel="noopener noreferrer"
            >京公网安备 11010102002940号</a>
          </div>
          <div class="footer-right">
            <a
              class="footer-link"
              href="https://cspmmed.com"
              target="_blank"
              rel="noopener noreferrer"
            >官网</a>
            <a
              class="footer-link"
              href="https://medline.cspmmed.com"
              target="_blank"
              rel="noopener noreferrer"
            >学术资源库</a>
          </div>
        </div>
      </footer>
    </template>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { useUserStore } from '@/stores/user'
import { useInfoStore } from '@/stores/info'
import { healthApi } from '@/apis/system_api'
import UserInfoComponent from '@/components/UserInfoComponent.vue'
import medLogo from '@/assets/medLogo.png'

const router = useRouter()
const userStore = useUserStore()
const infoStore = useInfoStore()
const docsUrl = 'https://cspmmed.com/'

// 加载状态
const isLoading = ref(true)
const error = ref(null)
const subtitleIndex = ref(0)
let subtitleTimer = null

const subtitleOptions = computed(() => {
  const subtitles = infoStore.branding?.subtitles
  if (Array.isArray(subtitles)) {
    const list = subtitles
      .map((item) => (typeof item === 'string' ? item.trim() : ''))
      .filter(Boolean)
    if (list.length) {
      return list
    }
  }

  const fallback = (infoStore.branding?.subtitle || '').trim()
  return fallback ? [fallback] : []
})

const currentSubtitle = computed(() => subtitleOptions.value[subtitleIndex.value] || '')

const stopSubtitleCarousel = () => {
  if (subtitleTimer) {
    clearInterval(subtitleTimer)
    subtitleTimer = null
  }
}

const startSubtitleCarousel = () => {
  stopSubtitleCarousel()
  subtitleIndex.value = 0

  if (subtitleOptions.value.length <= 1) {
    return
  }

  subtitleTimer = setInterval(() => {
    subtitleIndex.value = (subtitleIndex.value + 1) % subtitleOptions.value.length
  }, 2800)
}

const checkHealth = async () => {
  try {
    const response = await healthApi.checkHealth()
    if (response.status !== 'ok') {
      throw new Error('服务不可用')
    }
  } catch (e) {
    error.value = {
      title: '服务连接失败',
      message: '后端服务无法响应，请检查服务是否正常运行'
    }
    throw e
  }
}

const loadData = async () => {
  isLoading.value = true
  error.value = null

  try {
    // 先检查健康状态
    await checkHealth()
    // 健康检查通过后加载配置
    await infoStore.loadInfoConfig()
    startSubtitleCarousel()
  } catch (e) {
    console.error('加载失败:', e)
    stopSubtitleCarousel()
  } finally {
    isLoading.value = false
  }
}

const retryLoad = () => {
  loadData()
}

const goToChat = async () => {
  if (!userStore.isLoggedIn) {
    sessionStorage.setItem('redirect', '/')
    router.push('/login')
    return
  }

  router.push('/agent')
}

onMounted(() => {
  loadData()
})

onUnmounted(() => {
  stopSubtitleCarousel()
})
</script>

<style lang="less" scoped>
/* =========================================
   DeepSeek 风格首页 · 浅色医学版
   近白背景 · DeepSeek 蓝 · 医疗青绿 · 心电线
   ========================================= */

.home-container {
  position: relative;
  min-height: 100vh;
  display: flex;
  flex-direction: column;
  color: #0d1220;
  background: #f8faff;
  overflow-x: hidden;
  isolation: isolate;
}

// 加载中状态
.loading-container {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  min-height: 100vh;
  gap: 1rem;
  position: relative;
  z-index: 2;

  .loading-text {
    color: #8a93a3;
    font-size: 0.95rem;
    letter-spacing: 0.04em;
  }
}

// 错误状态
.error-container {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 100vh;
  padding: 2rem;
  position: relative;
  z-index: 2;
}

// ============== 氛围背景 ==============
.ambient {
  position: absolute;
  inset: 0;
  z-index: 0;
  overflow: hidden;
  pointer-events: none;
}

.glow {
  position: absolute;
  border-radius: 50%;
  filter: blur(2px);
  will-change: transform;
}

.glow-blue {
  top: -200px;
  left: 50%;
  transform: translateX(-50%);
  width: 920px;
  height: 580px;
  background: radial-gradient(closest-side, rgba(77, 107, 254, 0.08), transparent 70%);
  animation: glowDriftBlue 26s ease-in-out infinite alternate;
}

.glow-teal-left {
  bottom: -60px;
  left: -120px;
  width: 620px;
  height: 460px;
  background: radial-gradient(closest-side, rgba(77, 107, 254, 0.07), transparent 70%);
  animation: glowDriftTeal 34s ease-in-out infinite alternate;
}

.glow-teal-right {
  bottom: 4px;
  right: -100px;
  width: 560px;
  height: 420px;
  background: radial-gradient(closest-side, rgba(147, 197, 253, 0.1), transparent 70%);
  animation: glowDriftTealRight 38s ease-in-out infinite alternate;
}

// 心电监护线
.ecg-line {
  position: absolute;
  left: 0;
  right: 0;
  top: 44%;
  width: 100%;
  height: 240px;
  opacity: 1;
}

.ecg-path {
  stroke-width: 2.5;
  fill: none;
}

// 流动高亮段：短亮段沿线从左向右扫过，经过处颜色加深
.ecg-sweep {
  stroke: #4d6bfe;
  stroke-width: 3;
  stroke-linecap: round;
  fill: none;
  stroke-dasharray: 90 910;
  stroke-dashoffset: 1000;
  animation: ecgSweep 7s linear infinite;
  filter: drop-shadow(0 0 6px rgba(77, 107, 254, 0.55));
}

.ecg-dot-blue {
  fill: #6f88fe;
  opacity: 0.45;
}

.ecg-dot-teal {
  fill: #4d6bfe;
  opacity: 0.45;
}

// ============== 顶部导航 ==============
.site-header {
  position: relative;
  z-index: 10;
  display: flex;
  justify-content: space-between;
  align-items: center;
  width: 100%;
  padding: 0.85rem 3rem;
  flex-shrink: 0;
}

.logo {
  display: flex;
  align-items: center;
  gap: 0.6rem;
  color: #0d1220;
}

.logo-img {
  display: block;
  height: 36px;
  width: auto;
  object-fit: contain;
}

.logo-text {
  font-size: 1.15rem;
  font-weight: 700;
  letter-spacing: -0.01em;
  color: #8a93a3;
}

.header-actions {
  display: flex;
  align-items: center;
  gap: 0.75rem;

  :deep(.ant-btn-primary) {
    background: #4d6bfe;
    border-color: #4d6bfe;

    &:hover {
      background: #3b5bfd;
      border-color: #3b5bfd;
    }
  }
}

// ============== Hero ==============
.hero-section {
  position: relative;
  z-index: 1;
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 100%;
  padding: 2rem 1.5rem 6rem;
}

.hero-content {
  position: relative;
  display: flex;
  flex-direction: column;
  align-items: center;
  text-align: center;
  gap: 1.75rem;
  max-width: 920px;
  width: 100%;
}

// 入场动画
.reveal-up {
  opacity: 0;
  transform: translateY(20px);
  animation: revealUp 0.8s cubic-bezier(0.22, 1, 0.36, 1) forwards;
}

.reveal-up.delay-1 {
  animation-delay: 140ms;
}

.reveal-up.delay-2 {
  animation-delay: 280ms;
}

// 眉标徽章
.hero-eyebrow {
  display: inline-flex;
  align-items: center;
  gap: 0.5rem;
  margin: 0;
  padding: 0.45rem 1.1rem;
  border-radius: 999px;
  background: #eef2ff;
  border: 1px solid #dce3fe;
  color: #3b5bfd;
  font-size: 0.85rem;
  font-weight: 600;
  letter-spacing: 0.06em;
}

.eyebrow-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #4d6bfe;
  box-shadow: 0 0 0 3px rgba(77, 107, 254, 0.15);
  animation: pulse 2.5s ease-in-out infinite;
}

// 主标题
.title {
  font-size: clamp(2.6rem, 5.2vw, 4.4rem);
  font-weight: 800;
  margin: 0;
  letter-spacing: -0.035em;
  line-height: 1.15;
  color: #0d1220;
}

.title-line {
  display: inline-block;
}

.title-accent {
  color: #4d6bfe;
}

// 副标题交叉淡入容器
.subtitle-wrap {
  position: relative;
  width: 100%;
  min-height: calc(1.5em * 1.5);
  max-width: 640px;
}

.subtitle {
  font-size: 1.2rem;
  font-weight: 400;
  color: #5b6472;
  line-height: 1.5;
  margin: 0;
  letter-spacing: 0.01em;
}

.subtitle-switch-enter-active,
.subtitle-switch-leave-active {
  transition:
    opacity 0.55s ease,
    transform 0.55s ease;
}

.subtitle-switch-leave-active {
  position: absolute;
  inset: 0;
}

.subtitle-switch-enter-from {
  opacity: 0;
  transform: translateY(5px);
}

.subtitle-switch-leave-to {
  opacity: 0;
  transform: translateY(-5px);
}

// CTA 按钮组
.hero-actions {
  display: flex;
  flex-wrap: wrap;
  justify-content: center;
  gap: 0.85rem;
  align-items: center;
  margin-top: 0.4rem;
}

.button-base {
  position: relative;
  overflow: hidden;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 0.5rem;
  padding: 0 2rem;
  border-radius: 999px;
  font-size: 1rem;
  font-weight: 600;
  cursor: pointer;
  border: 1px solid transparent;
  text-decoration: none;
  transition:
    background 0.25s ease,
    border-color 0.25s ease,
    box-shadow 0.25s ease,
    transform 0.25s ease;
  min-height: 50px;
  min-width: 9.5rem;
  white-space: nowrap;
}

.button-base.primary {
  background: #4d6bfe;
  color: #ffffff;
  box-shadow:
    0 8px 20px -6px rgba(77, 107, 254, 0.35),
    0 2px 6px -2px rgba(77, 107, 254, 0.15);

  svg {
    transition: transform 0.25s ease;
  }

  &:hover {
    background: #3b5bfd;
    box-shadow:
      0 12px 28px -6px rgba(77, 107, 254, 0.45),
      0 2px 6px -2px rgba(77, 107, 254, 0.2);
    transform: translateY(-1px);

    svg {
      transform: translateX(3px);
    }
  }
}

.button-base.secondary {
  background: #ffffff;
  color: #3d4451;
  border-color: #e3eaf2;

  svg {
    opacity: 0.6;
  }

  &:hover {
    border-color: #c9d6e8;
    color: #0d1220;
    box-shadow: 0 4px 12px -4px rgba(13, 18, 32, 0.08);
  }
}

// 医学信任标签行
.trust-line {
  display: inline-flex;
  align-items: center;
  gap: 0.6rem;
  margin: 0.25rem 0 0;
  color: #7a8494;
  font-size: 0.85rem;
  font-weight: 500;
  letter-spacing: 0.02em;

  svg {
    flex-shrink: 0;
  }
}

// ============== 页脚 ==============
.site-footer {
  position: relative;
  z-index: 2;
  flex-shrink: 0;
  width: 100%;
  border-top: 1px solid #e8edf4;
}

.footer-content {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 1rem 3rem;
  max-width: 100%;
}

.footer-left {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 0.75rem;
  font-size: 0.85rem;
}

.footer-copy {
  color: #8a93a3;
  font-weight: 400;
}

.footer-icp-gap {
  width: 0.25rem;
  flex-shrink: 0;
}

.footer-icp-link {
  color: #8a93a3;
  text-decoration: none;
  font-weight: 400;
  transition: color 0.2s ease;

  &:hover {
    color: #0d1220;
  }
}

.footer-right {
  display: flex;
  align-items: center;
  gap: 1.75rem;
}

.footer-link {
  color: #8a93a3;
  text-decoration: none;
  font-size: 0.85rem;
  font-weight: 500;
  transition: color 0.2s ease;

  &:hover {
    color: #0d1220;
  }
}

// ============== 动画 ==============
@keyframes revealUp {
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

@keyframes pulse {
  0%,
  100% {
    opacity: 1;
    transform: scale(1);
  }
  50% {
    opacity: 0.6;
    transform: scale(1.4);
  }
}

@keyframes glowDriftBlue {
  from {
    transform: translateX(-50%) translate(0, 0) scale(1);
  }
  to {
    transform: translateX(-50%) translate(60px, 40px) scale(1.12);
  }
}

@keyframes glowDriftTeal {
  from {
    transform: translate(0, 0) scale(1);
  }
  to {
    transform: translate(80px, -50px) scale(1.15);
  }
}

@keyframes glowDriftTealRight {
  from {
    transform: translate(0, 0) scale(1);
  }
  to {
    transform: translate(-40px, 30px) scale(1.1);
  }
}

@keyframes ecgSweep {
  from {
    stroke-dashoffset: 1000;
  }
  to {
    stroke-dashoffset: 0;
  }
}

// ============== 响应式 ==============
@media (max-width: 768px) {
  .site-header {
    padding: 0.75rem 1.25rem;
  }

  .logo-img {
    height: 28px;
  }

  .logo-text {
    font-size: 0.95rem;
  }

  .footer-content {
    padding: 1rem 1.25rem;
    flex-direction: column;
    gap: 0.75rem;
    text-align: center;
  }

  .footer-left,
  .footer-right {
    justify-content: center;
  }

  .footer-left {
    align-items: center;
  }

  .hero-section {
    padding: 2rem 1.25rem 4rem;
  }

  .button-base {
    min-width: 8rem;
    padding: 0 1.5rem;
    font-size: 0.95rem;
  }
}

// ============== 减少动画 ==============
@media (prefers-reduced-motion: reduce) {
  .reveal-up {
    opacity: 1;
    transform: none;
    animation: none;
  }

  .glow,
  .eyebrow-dot,
  .ecg-sweep {
    animation: none;
    opacity: 0;
  }

  .subtitle-switch-enter-active,
  .subtitle-switch-leave-active {
    transition: none;
  }
}
</style>
