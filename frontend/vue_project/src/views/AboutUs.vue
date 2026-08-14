<script setup>
import { onMounted, onActivated, onUnmounted, nextTick, ref } from 'vue'

const pageContainer = ref(null)
const brandLogoUrl = '/imgs/logo.png'

// 重置滚动位置到顶部
const resetScrollPosition = () => {
  // 重置window滚动位置
  window.scrollTo(0, 0)

  // 重置容器内部滚动位置
  nextTick(() => {
    if (pageContainer.value) {
      pageContainer.value.scrollTop = 0
    }
    // 再次确保window滚动位置为0
    window.scrollTo(0, 0)
  })
}

// 处理浏览器返回/前进按钮
const handlePopState = () => {
  // 延迟执行，确保页面已渲染
  setTimeout(() => {
    resetScrollPosition()
  }, 100)
}

// 组件挂载时重置滚动位置
onMounted(() => {
  // 禁用浏览器的自动滚动恢复
  if ('scrollRestoration' in history) {
    history.scrollRestoration = 'manual'
  }

  // 重置滚动位置
  resetScrollPosition()

  // 监听浏览器历史记录导航（返回/前进按钮）
  window.addEventListener('popstate', handlePopState)

  // 使用nextTick确保DOM已渲染后再重置一次
  nextTick(() => {
    resetScrollPosition()
  })
})

// 如果使用了keep-alive，在激活时也重置滚动位置
onActivated(() => {
  resetScrollPosition()
})

// 组件卸载时清理事件监听器
onUnmounted(() => {
  window.removeEventListener('popstate', handlePopState)
})
</script>

<template>
  <div class="about-us-page" ref="pageContainer">
    <!-- 页面头部 -->
    <div class="page-header">
      <div class="page-kicker">Moonlit Data Intelligence Lab</div>
      <h1 class="page-title">关于我们</h1>
      <p class="page-subtitle">团队介绍页 · 组织信息待核验</p>
      <p class="page-deck">
        在多语种文本、数据挖掘与研究辅助之间建立观测坐标，把科研训练、工程实践和国际化数据理解汇聚成一座面向真实问题的月光观测台。
      </p>
      <div class="page-header-brand" aria-hidden="true">
        <img :src="brandLogoUrl" alt="" />
      </div>
    </div>

    <!-- 主要内容区域 -->
    <div class="content-container">
      <!-- 简介卡片 -->
      <div class="intro-card">
        <div class="card-icon">🏛️</div>
        <p class="intro-text">
          本页保留团队介绍框架。成立时间、负责人、成员规模和历史成果尚未绑定可核验出处、统计口径与更新时间；补齐并复核前，不作为当前组织事实或成果证明。
        </p>
      </div>
      <div class="focus-strip">
        <div class="focus-item">
          <div class="focus-value">待核验</div>
          <div class="focus-label">成员规模</div>
        </div>
        <div class="focus-item">
          <div class="focus-value">待核验</div>
          <div class="focus-label">培养记录</div>
        </div>
        <div class="focus-item">
          <div class="focus-value">待核验</div>
          <div class="focus-label">论文清单</div>
        </div>
        <div class="focus-item">
          <div class="focus-value">待核验</div>
          <div class="focus-label">奖项清单</div>
        </div>
      </div>
      <div class="highlight-banner">
        <div class="highlight-title">公开前置条件</div>
        <div class="highlight-points">
          <span>具名负责人和更新时间待补齐</span>
          <span>论文、项目、奖项需绑定可核验目录</span>
          <span>当前页面不作数量、级别或影响力背书</span>
        </div>
      </div>

      <!-- 一、基本概况 -->
      <section class="content-section">
        <div class="section-header">
          <span class="section-number">01</span>
          <h2 class="section-title">基本概况</h2>
        </div>
        <div class="section-content">
          <div class="info-grid">
            <div class="info-item">
              <div class="info-label">定位</div>
              <div class="info-value">介绍材料将围绕科研训练、数据挖掘与自然语言处理展开；组织性质和当前活动范围待具名负责人确认。</div>
            </div>
            <div class="info-item">
              <div class="info-label">规模</div>
              <div class="info-value">成员、研究小组、毕业生和参与范围尚无带截止时间的可核验名册，本页暂不展示数量。</div>
            </div>
            <div class="info-item">
              <div class="info-label">特色</div>
              <div class="info-value">页面以“多语种 + 数据智能”为主题描述交叉方向，不据此评价培养成效或代表性。</div>
            </div>
          </div>
        </div>
      </section>

      <!-- 二、研究主题标签 -->
      <section class="content-section">
        <div class="section-header">
          <span class="section-number">02</span>
          <h2 class="section-title">研究主题标签</h2>
        </div>
        <div class="section-content">
          <div class="research-grid">
            <div class="research-card">
              <div class="research-icon">🔬</div>
              <h3 class="research-title">数据挖掘基础理论</h3>
              <p class="research-desc">聚类、分类、关联规则、异常检测等算法研究</p>
            </div>
            <div class="research-card research-card-emphasis">
              <div class="research-icon">💬</div>
              <h3 class="research-title">自然语言处理（NLP）</h3>
              <p class="research-desc">多语种文本分析、情感计算、低资源语言处理、文本生成与摘要</p>
            </div>
            <div class="research-card">
              <div class="research-icon">📊</div>
              <h3 class="research-title">网络文本与舆情分析</h3>
              <p class="research-desc">热点话题检测、公共事件舆情预警、观点挖掘</p>
            </div>
            <div class="research-card">
              <div class="research-icon">🌐</div>
              <h3 class="research-title">社会网络分析</h3>
              <p class="research-desc">网络可视化、链路预测、群体行为建模</p>
            </div>
            <div class="research-card">
              <div class="research-icon">🎯</div>
              <h3 class="research-title">个性化推荐系统</h3>
              <p class="research-desc">电商 / 新闻 / 教育场景的智能推荐、可监管推荐算法</p>
            </div>
            <div class="research-card">
              <div class="research-icon">🔍</div>
              <h3 class="research-title">企业竞争情报</h3>
              <p class="research-desc">多语种情报提取、行业态势推理、跨境数据应用</p>
            </div>
          </div>
        </div>
      </section>

      <!-- 三、师资信息 -->
      <section class="content-section">
        <div class="section-header">
          <span class="section-number">03</span>
          <h2 class="section-title">师资信息</h2>
        </div>
        <div class="section-content">
          <div class="team-info">
            <p class="team-lead">
              师资姓名、职称、职责、任期与联系方式尚待具名负责人提供并复核。
            </p>
            <ul class="team-list">
              <li>匿名占位称谓不作为人员身份、专业资历或项目经历证明。</li>
              <li>完成公开授权前，本页不列出个人履历或联系方式。</li>
            </ul>
            <p class="team-note">
              研究方向标签仅用于页面导航；当前人员与项目归属需要独立核验。
            </p>
          </div>
        </div>
      </section>

      <!-- 四、成果目录状态 -->
      <section class="content-section">
        <div class="section-header">
          <span class="section-number">04</span>
          <h2 class="section-title">成果目录状态</h2>
        </div>
        <div class="section-content">
          <div class="achievement-tabs">
            <div class="achievement-item achievement-item-featured">
              <div class="achievement-icon">📝</div>
              <h3 class="achievement-title">论文与会议记录</h3>
              <ul class="achievement-list">
                <li>作者、题名、出版物、DOI、收录口径与归属证明待绑定。</li>
                <li>目录复核前不展示论文数量、会议级别或期刊影响力结论。</li>
              </ul>
            </div>
            <div class="achievement-item">
              <div class="achievement-icon">🔬</div>
              <h3 class="achievement-title">科研项目</h3>
              <ul class="achievement-list">
                <li>项目编号、资助机构、起止时间、参与角色和公开链接待复核。</li>
                <li>仅出现项目名称不能证明承担单位、负责人或完成状态。</li>
              </ul>
            </div>
            <div class="achievement-item">
              <div class="achievement-icon">💡</div>
              <h3 class="achievement-title">知识产权与技术成果</h3>
              <ul class="achievement-list">
                <li>登记号、权利人、法律状态和产品对应关系待绑定。</li>
                <li>完成证据目录前不声明申请数量、授权状态或应用成效。</li>
              </ul>
            </div>
            <div class="achievement-item">
              <div class="achievement-icon">🏆</div>
              <h3 class="achievement-title">竞赛记录</h3>
              <ul class="achievement-list">
                <li>赛事全称、届次、主办方、获奖名单和证书链接待复核。</li>
                <li>目录复核前不作奖项数量、级别或名次声明。</li>
              </ul>
            </div>
            <div class="achievement-item">
              <div class="achievement-icon">⭐</div>
              <h3 class="achievement-title">荣誉称号</h3>
              <ul class="achievement-list">
                <li>授予机构、正式称号、获授主体、日期和公告链接待复核。</li>
                <li>未绑定公告的荣誉描述不作为公开背书。</li>
              </ul>
            </div>
          </div>
        </div>
      </section>

      <!-- 五、人才培养资料 -->
      <section class="content-section">
        <div class="section-header">
          <span class="section-number">05</span>
          <h2 class="section-title">人才培养资料</h2>
        </div>
        <div class="section-content">
          <div class="talent-grid">
            <div class="talent-card">
              <div class="talent-number">待核验</div>
              <div class="talent-label">升学深造</div>
              <div class="talent-desc">去向、统计周期、是否去重和授权披露状态待核验</div>
            </div>
            <div class="talent-card">
              <div class="talent-number">待核验</div>
              <div class="talent-label">奖学金记录</div>
              <div class="talent-desc">奖项名称、年度、获奖主体与公告链接待核验</div>
            </div>
            <div class="talent-card">
              <div class="talent-number">待核验</div>
              <div class="talent-label">学生项目</div>
              <div class="talent-desc">项目编号、参与角色、起止时间与状态待核验</div>
            </div>
            <div class="talent-card">
              <div class="talent-icon">💼</div>
              <div class="talent-label">就业去向</div>
              <div class="talent-desc">聚合统计口径、样本覆盖和个人披露授权待核验</div>
            </div>
          </div>
        </div>
      </section>

      <!-- 六、影响材料状态 -->
      <section class="content-section">
        <div class="section-header">
          <span class="section-number">06</span>
          <h2 class="section-title">影响材料状态</h2>
        </div>
        <div class="section-content">
          <div class="impact-grid">
            <div class="impact-card impact-card-emphasis">
              <div class="impact-icon">🚀</div>
              <h3 class="impact-title">交叉培养材料</h3>
              <p class="impact-desc">培养模式、课程变化和学习成效需要方案、样本和评估材料支持。</p>
            </div>
            <div class="impact-card">
              <div class="impact-icon">🌏</div>
              <h3 class="impact-title">研究主题材料</h3>
              <p class="impact-desc">课题、委托关系、交付物和实际采用情况尚待逐项核验。</p>
            </div>
            <div class="impact-card">
              <div class="impact-icon">🤝</div>
              <h3 class="impact-title">合作项目材料</h3>
              <p class="impact-desc">合作方、授权范围、项目状态和应用成效尚未形成公开证据目录。</p>
            </div>
            <div class="impact-card">
              <div class="impact-icon">✨</div>
              <h3 class="impact-title">组织影响材料</h3>
              <p class="impact-desc">影响范围和因果归属尚未评估，本页不作代表性或示范性结论。</p>
            </div>
          </div>
        </div>
      </section>

      <section class="content-section" aria-labelledby="governance-heading">
        <div class="section-header">
          <span class="section-number">07</span>
          <h2 id="governance-heading" class="section-title">产品治理与联系</h2>
        </div>
        <div class="governance-panel">
          <p>
            GlobeMind 的数据、模型、服务、隐私与安全具名负责人尚待指定。下列公开页面提供当前可验证的使用边界、责任矩阵和共享受理入口；未取得批准的许可、保留期或 SLA 不会在页面中虚构。
          </p>
          <nav class="governance-links" aria-label="产品治理页面">
            <router-link to="/data-service/help-docs">帮助文档</router-link>
            <router-link to="/privacy">隐私说明</router-link>
            <router-link to="/terms">服务条款</router-link>
            <router-link to="/security">安全报告</router-link>
            <router-link to="/methodology">方法说明</router-link>
            <router-link to="/sources">来源与许可说明</router-link>
            <router-link to="/status">数据与服务状态</router-link>
            <router-link to="/corrections">纠错与权利请求</router-link>
          </nav>
        </div>
      </section>
    </div>
  </div>
</template>

<style scoped>
.about-us-page {
  --about-bg: #030813;
  --about-ink: #e2ebf6;
  --about-copy: #aebcd3;
  --about-muted: #8fa1bc;
  --about-moon: #fff1bd;
  --about-cyan: #68d8ff;
  --about-card: rgba(8, 22, 43, 0.74);
  --about-card-strong: rgba(13, 29, 54, 0.86);
  --about-line: rgba(177, 210, 255, 0.2);
  --about-line-strong: rgba(245, 220, 156, 0.38);
  --about-shadow: rgba(0, 0, 0, 0.38);
  position: fixed;
  top: 64px;
  left: 0;
  right: 0;
  bottom: 0;
  overflow-y: auto;
  padding: 0;
  color: var(--about-copy);
  font-family: "Noto Sans CJK SC", "Source Han Sans SC", "PingFang SC", "Microsoft YaHei", "Heiti SC", system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  font-weight: 500;
  background-color: var(--about-bg);
  background-image:
    linear-gradient(180deg, rgba(3, 8, 19, 0.22), rgba(3, 8, 19, 0.82) 74%, rgba(3, 8, 19, 0.96)),
    url('/assets/backgrounds/8k_stars_milky_way.webp');
  background-repeat: no-repeat;
  background-position: center top;
  background-size: cover;
  background-attachment: fixed;
  scrollbar-color: rgba(130, 166, 228, 0.72) rgba(3, 8, 19, 0.88);
}

.about-us-page::before,
.about-us-page::after {
  content: '';
  position: fixed;
  inset: 64px 0 0;
  pointer-events: none;
}

.about-us-page::before {
  z-index: 0;
  background:
    radial-gradient(circle at 78% 14%, rgba(255, 240, 190, 0.42) 0, rgba(255, 240, 190, 0.16) 9rem, rgba(112, 155, 255, 0.08) 21rem, transparent 34rem),
    radial-gradient(circle at 18% 8%, rgba(104, 216, 255, 0.16), transparent 18rem),
    linear-gradient(90deg, rgba(3, 8, 19, 0.82) 0%, rgba(3, 8, 19, 0.45) 46%, rgba(3, 8, 19, 0.78) 100%);
}

.about-us-page::after {
  z-index: 0;
  opacity: 0.72;
  background-image:
    linear-gradient(rgba(161, 195, 255, 0.08) 1px, transparent 1px),
    linear-gradient(90deg, rgba(161, 195, 255, 0.07) 1px, transparent 1px),
    radial-gradient(circle at 18% 32%, rgba(247, 226, 164, 0.85) 0 1px, transparent 2px),
    radial-gradient(circle at 54% 16%, rgba(104, 216, 255, 0.72) 0 1px, transparent 2px),
    radial-gradient(circle at 72% 48%, rgba(143, 145, 255, 0.68) 0 1px, transparent 2px);
  background-size: 150px 150px, 150px 150px, 340px 280px, 420px 300px, 520px 360px;
  mask-image: linear-gradient(180deg, rgba(0, 0, 0, 0.88), rgba(0, 0, 0, 0.38) 55%, transparent 86%);
}

.about-us-page * {
  box-sizing: border-box;
}

.page-header,
.content-container {
  position: relative;
  z-index: 1;
}

.page-header {
  max-width: 1220px;
  min-height: 390px;
  margin: 0 auto;
  padding: 76px 430px 54px 22px;
  text-align: left;
  position: relative;
  overflow: hidden;
  isolation: isolate;
}

.page-header::before {
  content: '';
  position: absolute;
  left: 22px;
  top: 48px;
  width: min(48vw, 520px);
  height: 1px;
  z-index: 1;
  background: linear-gradient(90deg, rgba(247, 226, 164, 0.82), rgba(104, 216, 255, 0.38), transparent);
}

.page-header::after {
  content: '';
  position: absolute;
  right: 56px;
  top: 62px;
  width: 400px;
  height: 252px;
  z-index: 0;
  border: 1px solid rgba(190, 216, 255, 0.15);
  border-left-color: transparent;
  border-radius: 50%;
  transform: rotate(-13deg);
}

.page-kicker {
  width: fit-content;
  margin: 0 0 18px;
  padding: 7px 12px;
  border: 1px solid rgba(247, 226, 164, 0.28);
  border-radius: 999px;
  background: rgba(255, 241, 189, 0.08);
  color: var(--about-moon);
  font-size: 0.78rem;
  line-height: 1.2;
  font-weight: 700;
  letter-spacing: 0;
  position: relative;
  z-index: 2;
}

.page-header-brand {
  position: absolute;
  right: 48px;
  top: 58%;
  width: 290px;
  height: 290px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  background:
    radial-gradient(circle at 32% 24%, rgba(255, 255, 246, 0.98), rgba(255, 242, 196, 0.96) 38%, rgba(215, 197, 156, 0.92) 70%, rgba(126, 148, 180, 0.44) 100%);
  border: 1px solid rgba(255, 244, 209, 0.72);
  box-shadow:
    inset -36px -42px 60px rgba(96, 92, 119, 0.24),
    inset 18px 18px 34px rgba(255, 255, 255, 0.36),
    0 0 40px rgba(255, 241, 189, 0.48),
    0 0 120px rgba(104, 216, 255, 0.22),
    0 28px 90px rgba(0, 0, 0, 0.48);
  pointer-events: none;
  transform: translateY(-50%);
  z-index: 1;
}

.page-header-brand::before {
  content: '';
  position: absolute;
  inset: 0;
  border-radius: 50%;
  background:
    radial-gradient(circle at 34% 32%, rgba(104, 94, 116, 0.2) 0 16px, transparent 17px),
    radial-gradient(circle at 68% 28%, rgba(92, 86, 112, 0.16) 0 11px, transparent 12px),
    radial-gradient(circle at 58% 66%, rgba(88, 82, 109, 0.18) 0 22px, transparent 23px),
    radial-gradient(circle at 30% 70%, rgba(96, 87, 114, 0.13) 0 9px, transparent 10px);
  mix-blend-mode: multiply;
}

.page-header-brand::after {
  content: '';
  position: absolute;
  inset: -34px;
  border-radius: 50%;
  border: 1px solid rgba(190, 216, 255, 0.18);
  border-top-color: rgba(247, 226, 164, 0.34);
  transform: rotate(24deg);
}

.page-header-brand img {
  display: block;
  width: 230px;
  height: auto;
  object-fit: contain;
  opacity: 0.78;
  filter:
    saturate(0.88)
    drop-shadow(0 0 10px rgba(255, 255, 255, 0.54))
    drop-shadow(0 14px 22px rgba(31, 41, 75, 0.22));
  position: relative;
  z-index: 1;
}

.page-title {
  max-width: 700px;
  margin: 0 0 14px;
  font-size: clamp(3rem, 6vw, 5.7rem);
  line-height: 1.02;
  font-weight: 900;
  letter-spacing: 0;
  color: var(--about-ink);
  text-shadow:
    0 0 20px rgba(255, 241, 189, 0.22),
    0 12px 38px rgba(0, 0, 0, 0.38);
  position: relative;
  z-index: 2;
}

.page-subtitle {
  margin: 0;
  font-size: 1.2rem;
  line-height: 1.75;
  color: var(--about-moon);
  font-weight: 700;
  position: relative;
  z-index: 2;
  text-shadow: none;
}

.page-deck {
  max-width: 690px;
  margin: 18px 0 0;
  font-size: 1.02rem;
  line-height: 1.95;
  color: #acbad0;
  font-weight: 500;
  position: relative;
  z-index: 2;
  text-shadow: none;
}

.content-container {
  max-width: 1220px;
  margin: 0 auto;
  padding: 0 22px 72px;
}

.content-section {
  margin-bottom: 34px;
  padding: 34px 0 0;
  border-top: 1px solid rgba(177, 210, 255, 0.16);
}

.governance-panel {
  padding: 24px;
  border: 1px solid var(--about-line-strong);
  border-radius: 8px;
  background:
    linear-gradient(135deg, rgba(255, 241, 189, 0.09), rgba(104, 216, 255, 0.07)),
    var(--about-card);
  box-shadow: 0 20px 54px var(--about-shadow);
}

.governance-panel p {
  margin: 0;
  color: var(--about-copy);
  font-size: 0.98rem;
  line-height: 1.85;
}

.governance-links {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  margin-top: 20px;
}

.governance-links a {
  display: inline-flex;
  align-items: center;
  min-height: 44px;
  box-sizing: border-box;
  padding: 9px 13px;
  border: 1px solid rgba(177, 210, 255, 0.26);
  border-radius: 8px;
  color: var(--about-moon);
  background: rgba(237, 245, 255, 0.06);
  font-weight: 700;
  text-decoration: none;
}

.governance-links a:hover {
  border-color: rgba(247, 226, 164, 0.52);
  background: rgba(255, 241, 189, 0.11);
}

.governance-links a:focus-visible {
  outline: 3px solid rgba(104, 216, 255, 0.55);
  outline-offset: 3px;
}

.intro-card,
.info-item,
.research-card,
.team-info,
.achievement-item,
.talent-card,
.impact-card,
.focus-item,
.highlight-banner {
  border-radius: 8px;
  background:
    linear-gradient(145deg, rgba(255, 241, 189, 0.06), rgba(104, 216, 255, 0.04)),
    var(--about-card);
  border: 1px solid var(--about-line);
  box-shadow: 0 20px 54px var(--about-shadow);
  backdrop-filter: blur(14px);
  -webkit-backdrop-filter: blur(14px);
  transition: transform 0.2s ease, box-shadow 0.25s ease, border-color 0.25s ease, background 0.25s ease;
}

.intro-card:hover,
.info-item:hover,
.research-card:hover,
.achievement-item:hover,
.talent-card:hover,
.impact-card:hover,
.focus-item:hover {
  transform: translateY(-3px);
  border-color: rgba(247, 226, 164, 0.42);
  box-shadow: 0 28px 68px rgba(0, 0, 0, 0.46);
}

.intro-card {
  padding: 28px 30px;
  margin-bottom: 14px;
  display: flex;
  gap: 18px;
  align-items: center;
}

.card-icon {
  margin-top: 2px;
  margin-bottom: 0;
}

.intro-text {
  margin: 0;
  color: var(--about-copy);
  font-size: 1.03rem;
  font-weight: 500;
  line-height: 1.9;
  text-shadow: none;
}

.focus-strip {
  margin-bottom: 24px;
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
}

.focus-item {
  padding: 18px 14px;
  text-align: center;
}

.focus-value {
  font-size: 1.75rem;
  line-height: 1.1;
  font-weight: 850;
  color: var(--about-moon);
  text-shadow: none;
}

.focus-label {
  margin-top: 8px;
  font-size: 0.9rem;
  color: var(--about-muted);
  font-weight: 650;
}

.highlight-banner {
  margin-bottom: 30px;
  padding: 20px 22px;
  background:
    linear-gradient(135deg, rgba(255, 241, 189, 0.12), rgba(104, 216, 255, 0.08)),
    rgba(7, 20, 39, 0.76);
  border-color: var(--about-line-strong);
}

.highlight-title {
  font-size: 1.14rem;
  font-weight: 800;
  letter-spacing: 0;
  margin-bottom: 12px;
  color: var(--about-ink);
  text-shadow: none;
}

.highlight-points {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
}

.highlight-points span {
  display: inline-flex;
  align-items: center;
  min-height: 34px;
  padding: 0 12px;
  border-radius: 999px;
  background: rgba(237, 245, 255, 0.07);
  border: 1px solid rgba(177, 210, 255, 0.18);
  font-size: 0.92rem;
  font-weight: 600;
  color: #b4c0d2;
  text-shadow: none;
}

.section-header {
  display: flex;
  align-items: baseline;
  gap: 14px;
  margin-bottom: 18px;
  padding-bottom: 13px;
  border-bottom: 1px solid rgba(177, 210, 255, 0.16);
}

.section-number {
  font-size: 2.2rem;
  font-weight: 850;
  line-height: 1;
  color: rgba(247, 226, 164, 0.62);
}

.section-title {
  margin: 0;
  font-size: 1.62rem;
  line-height: 1.3;
  color: var(--about-ink);
  font-weight: 800;
  text-shadow: none;
}

.info-grid,
.research-grid,
.achievement-tabs,
.talent-grid,
.impact-grid {
  display: grid;
  gap: 16px;
  align-items: start;
}

.info-grid { grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); }
.research-grid { grid-template-columns: repeat(3, minmax(0, 1fr)); }
.achievement-tabs { grid-template-columns: repeat(2, minmax(0, 1fr)); }
.talent-grid { grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); }
.impact-grid { grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); }

.info-item,
.research-card,
.achievement-item,
.talent-card,
.impact-card {
  padding: 22px 20px;
  height: fit-content;
}

.info-label,
.research-title,
.achievement-title,
.talent-label,
.impact-title {
  margin: 0 0 10px;
  color: var(--about-ink);
  font-weight: 800;
  text-shadow: none;
}

.info-label { font-size: 1.06rem; }
.research-title { font-size: 1.05rem; }
.achievement-title { font-size: 1.08rem; }
.talent-label { font-size: 1.04rem; }
.impact-title { font-size: 1.06rem; }

.info-value,
.research-desc,
.team-lead,
.team-list li,
.team-note,
.achievement-list li,
.talent-desc,
.impact-desc {
  color: var(--about-copy);
  font-weight: 520;
  line-height: 1.8;
  text-shadow: none;
}

.info-value strong,
.research-desc strong,
.team-lead strong,
.team-list strong,
.achievement-list strong,
.talent-desc strong,
.impact-desc strong {
  color: var(--about-moon);
  font-weight: 850;
}

.info-item-emphasis,
.research-card-emphasis,
.impact-card-emphasis {
  background:
    linear-gradient(145deg, rgba(104, 216, 255, 0.18), rgba(143, 145, 255, 0.13)),
    var(--about-card-strong);
  border-color: rgba(104, 216, 255, 0.34);
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.08),
    0 24px 60px rgba(0, 0, 0, 0.44);
}

.info-item-emphasis .info-label,
.research-card-emphasis .research-title,
.impact-card-emphasis .impact-title {
  color: var(--about-ink);
  text-shadow: none;
}

.info-item-emphasis .info-value,
.research-card-emphasis .research-desc,
.impact-card-emphasis .impact-desc {
  color: #bdcbe0;
}

.info-item-emphasis strong,
.research-card-emphasis strong,
.impact-card-emphasis strong {
  color: var(--about-moon);
}

.info-value,
.research-desc,
.achievement-list li,
.talent-desc,
.impact-desc {
  font-size: 0.96rem;
}

.research-card,
.talent-card,
.impact-card {
  text-align: center;
}

.research-icon,
.achievement-icon,
.impact-icon,
.talent-icon {
  margin-bottom: 10px;
}

.card-icon,
.research-icon,
.achievement-icon,
.impact-icon,
.talent-icon {
  position: relative;
  display: inline-flex;
  width: 42px;
  height: 42px;
  flex: 0 0 auto;
  border-radius: 8px;
  border: 1px solid rgba(247, 226, 164, 0.26);
  background:
    radial-gradient(circle at 34% 30%, rgba(255, 241, 189, 0.38), transparent 44%),
    rgba(255, 255, 255, 0.05);
  color: transparent;
  font-size: 0;
  line-height: 0;
}

.card-icon::before,
.research-icon::before,
.achievement-icon::before,
.impact-icon::before,
.talent-icon::before {
  content: '';
  position: absolute;
  left: 11px;
  top: 10px;
  width: 16px;
  height: 16px;
  border: 2px solid var(--about-moon);
  border-radius: 50%;
}

.card-icon::after,
.research-icon::after,
.achievement-icon::after,
.impact-icon::after,
.talent-icon::after {
  content: '';
  position: absolute;
  right: 9px;
  bottom: 11px;
  width: 14px;
  height: 2px;
  border-radius: 999px;
  background: var(--about-cyan);
  transform: rotate(-34deg);
  transform-origin: center;
}

.research-card-emphasis .research-icon,
.impact-card-emphasis .impact-icon {
  border-color: rgba(237, 245, 255, 0.3);
  background: rgba(237, 245, 255, 0.1);
}

.research-card-emphasis .research-icon::before,
.impact-card-emphasis .impact-icon::before {
  border-color: rgba(255, 255, 255, 0.92);
}

.research-card-emphasis .research-icon::after,
.impact-card-emphasis .impact-icon::after {
  background: var(--about-moon);
}

.team-info {
  padding: 26px 24px;
}

.team-lead {
  font-size: 1.03rem;
  margin-bottom: 14px;
}

.team-list {
  list-style: none;
  padding: 0;
  margin: 0 0 14px;
}

.team-list li {
  position: relative;
  padding: 10px 0 10px 22px;
  font-size: 0.97rem;
}

.team-list li::before {
  content: '';
  position: absolute;
  left: 6px;
  top: 21px;
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--about-moon);
  box-shadow: 0 0 12px rgba(255, 241, 189, 0.5);
}

.team-note {
  margin: 0;
  padding: 14px 16px;
  border-radius: 8px;
  background: rgba(237, 245, 255, 0.06);
  border: 1px solid rgba(177, 210, 255, 0.14);
  font-size: 0.96rem;
}

.achievement-list {
  list-style: none;
  padding: 0;
  margin: 0;
}

.achievement-list li {
  position: relative;
  padding: 8px 0 8px 18px;
}

.achievement-list li::before {
  content: '';
  position: absolute;
  left: 2px;
  top: 17px;
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--about-moon);
  box-shadow: 0 0 12px rgba(255, 241, 189, 0.44);
}

.achievement-item-featured {
  grid-column: 1 / -1;
  padding: 24px 22px;
  background:
    linear-gradient(135deg, rgba(255, 241, 189, 0.12), rgba(104, 216, 255, 0.08)),
    var(--about-card-strong);
  border-color: var(--about-line-strong);
}

.achievement-item-featured .achievement-title {
  font-size: 1.18rem;
}

.talent-number {
  color: var(--about-moon);
  font-size: 2.6rem;
  line-height: 1;
  font-weight: 850;
  margin-bottom: 8px;
  text-shadow: none;
}

@media (prefers-reduced-motion: reduce) {
  .intro-card,
  .info-item,
  .research-card,
  .achievement-item,
  .talent-card,
  .impact-card,
  .focus-item,
  .highlight-banner {
    transition: none;
  }

  .intro-card:hover,
  .info-item:hover,
  .research-card:hover,
  .achievement-item:hover,
  .talent-card:hover,
  .impact-card:hover,
  .focus-item:hover {
    transform: none;
  }
}

@media (max-width: 900px) {
  .about-us-page {
    top: 64px;
    background-position: center top;
  }

  .about-us-page::before,
  .about-us-page::after {
    inset: 64px 0 0;
  }

  .page-header {
    min-height: 350px;
    padding: 58px 220px 38px 18px;
  }

  .page-header::before {
    left: 18px;
    top: 36px;
  }

  .page-header::after {
    right: 18px;
    top: 68px;
    width: 260px;
    height: 180px;
  }

  .page-header-brand {
    width: 172px;
    height: 172px;
    right: 34px;
    top: 56%;
  }

  .page-header-brand::after {
    inset: -22px;
  }

  .page-header-brand img {
    width: 138px;
  }

  .page-title {
    font-size: 2.9rem;
  }

  .page-subtitle {
    font-size: 1rem;
  }

  .page-deck {
    font-size: 0.95rem;
    line-height: 1.8;
  }

  .content-container {
    padding: 0 14px 38px;
  }

  .content-section {
    padding-top: 24px;
    margin-bottom: 24px;
  }

  .intro-card {
    flex-direction: column;
    padding: 20px 16px;
    gap: 12px;
    align-items: flex-start;
  }

  .focus-strip {
    grid-template-columns: repeat(2, minmax(0, 1fr));
    margin-bottom: 18px;
  }

  .highlight-banner {
    margin-bottom: 22px;
    padding: 16px 14px;
  }

  .highlight-title {
    font-size: 1.03rem;
  }

  .highlight-points span {
    min-height: 30px;
    font-size: 0.86rem;
  }

  .section-number {
    font-size: 1.55rem;
  }

  .section-title {
    font-size: 1.22rem;
  }

  .info-grid,
  .research-grid,
  .achievement-tabs,
  .talent-grid,
  .impact-grid {
    grid-template-columns: 1fr;
  }

  .achievement-item-featured {
    grid-column: auto;
  }
}

@media (max-width: 560px) {
  .about-us-page {
    top: 56px;
    background-position: 52% top;
  }

  .about-us-page::before,
  .about-us-page::after {
    inset: 56px 0 0;
  }

  .page-header {
    min-height: 430px;
    padding: 38px 14px 196px;
  }

  .page-header::before {
    left: 14px;
    top: 24px;
    width: 72vw;
  }

  .page-header::after {
    right: 50%;
    top: auto;
    bottom: 32px;
    width: 224px;
    height: 132px;
    transform: translateX(50%) rotate(-13deg);
  }

  .page-kicker {
    margin-bottom: 14px;
    font-size: 0.72rem;
  }

  .page-header-brand {
    width: 150px;
    height: 150px;
    top: auto;
    right: 50%;
    bottom: 24px;
    transform: translateX(50%);
  }

  .page-header-brand::after {
    inset: -18px;
  }

  .page-header-brand img {
    width: 120px;
  }

  .page-title {
    font-size: 2.35rem;
    margin-bottom: 10px;
  }

  .page-subtitle {
    font-size: 0.92rem;
  }

  .page-deck {
    margin-top: 14px;
    font-size: 0.88rem;
  }

  .content-container {
    padding: 0 10px 28px;
  }

  .content-section {
    padding-top: 20px;
    margin-bottom: 20px;
  }

  .intro-card {
    padding: 16px 14px;
  }

  .intro-text {
    font-size: 0.92rem;
  }

  .focus-strip {
    grid-template-columns: repeat(2, 1fr);
    gap: 8px;
    margin-bottom: 14px;
  }

  .focus-value {
    font-size: 1.3rem;
  }

  .focus-label {
    font-size: 0.8rem;
  }

  .highlight-banner {
    padding: 14px 12px;
    margin-bottom: 16px;
  }

  .highlight-title {
    font-size: 0.95rem;
  }

  .highlight-points span {
    font-size: 0.8rem;
    min-height: 28px;
    padding: 0 10px;
  }

  .section-header {
    gap: 10px;
    margin-bottom: 14px;
    padding-bottom: 10px;
  }

  .section-number {
    font-size: 1.3rem;
  }

  .section-title {
    font-size: 1.05rem;
  }

  .info-item,
  .research-card,
  .achievement-item,
  .talent-card,
  .impact-card {
    padding: 16px 14px;
  }

  .info-label,
  .research-title,
  .achievement-title,
  .talent-label,
  .impact-title {
    font-size: 0.95rem;
  }

  .info-value,
  .research-desc,
  .achievement-list li,
  .talent-desc,
  .impact-desc {
    font-size: 0.88rem;
  }

  .research-icon,
  .achievement-icon,
  .impact-icon,
  .talent-icon,
  .card-icon {
    width: 36px;
    height: 36px;
  }

  .research-icon,
  .achievement-icon,
  .impact-icon,
  .talent-icon {
    margin-bottom: 6px;
  }

  .card-icon::before,
  .research-icon::before,
  .achievement-icon::before,
  .impact-icon::before,
  .talent-icon::before {
    left: 9px;
    top: 8px;
    width: 14px;
    height: 14px;
  }

  .card-icon::after,
  .research-icon::after,
  .achievement-icon::after,
  .impact-icon::after,
  .talent-icon::after {
    right: 8px;
    bottom: 10px;
    width: 12px;
  }

  .talent-number {
    font-size: 2rem;
  }

  .team-info {
    padding: 18px 16px;
  }

  .team-lead {
    font-size: 0.92rem;
  }

  .team-list li {
    font-size: 0.88rem;
    padding: 8px 0 8px 20px;
  }

  .team-list li::before {
    top: 18px;
  }

  .team-note {
    font-size: 0.88rem;
    padding: 10px 12px;
  }

  .achievement-item-featured {
    padding: 18px 16px;
  }

  .achievement-list li {
    padding: 6px 0 6px 16px;
  }

  .talent-card {
    text-align: left;
  }
}
</style>
