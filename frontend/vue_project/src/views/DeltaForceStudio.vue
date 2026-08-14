<script setup>
import { computed, ref } from 'vue'

const categories = [
  { id: 'all', label: '全部素材' },
  { id: 'gameplay', label: '实机画面' },
  { id: 'trailer', label: '官方预告' },
  { id: 'prompt', label: 'H3 镜头方案' },
]

const activeCategory = ref('all')
const copiedId = ref('')

const materials = [
  { id: 'desert-assault', type: 'gameplay', typeLabel: '实机画面', title: '沙漠据点突入', description: '第一人称突击视角；棕榈、低矮建筑与小队推进构成清晰的战术空间层次。', image: 'https://shared.fastly.steamstatic.com/store_item_assets/steam/apps/2507950/ss_77974f7b3602b89f690def79b8e0f45f741107e0.jpg', sourceName: 'SteamDB · Delta Force Screenshots', sourceUrl: 'https://steamdb.info/app/2507950/screenshots/', tags: ['推进', '沙漠', '第一人称'] },
  { id: 'urban-advance', type: 'gameplay', typeLabel: '实机画面', title: '雨后城区推进', description: '湿润街面、装甲车与破损建筑适合做压迫感强的转场与环境声设计参考。', image: 'https://shared.fastly.steamstatic.com/store_item_assets/steam/apps/2507950/ss_567c8081ef9b2df8afec0090a39cd97b1600191b.jpg', sourceName: 'SteamDB · Delta Force Screenshots', sourceUrl: 'https://steamdb.info/app/2507950/screenshots/', tags: ['城区', '雨天', '载具'] },
  { id: 'official-channel', type: 'trailer', typeLabel: '官方预告', title: '官方频道：战术节奏参考', description: '适合拆解镜头长度、击杀前后的停顿与音乐起落；请仅将其作为风格和剪辑参考。', image: 'https://i.ytimg.com/vi/G1xZbZuU86M/maxresdefault.jpg', sourceName: 'Delta Force Game · YouTube', sourceUrl: 'https://www.youtube.com/watch?v=G1xZbZuU86M', tags: ['官方', '预告', '节奏'] },
  { id: 'black-hawk-down', type: 'trailer', typeLabel: '官方预告', title: 'Black Hawk Down 发售预告', description: '用于研究低空飞行、尘土、队伍协作和大场面镜头衔接，不建议直接搬运片段。', image: 'https://i.ytimg.com/vi/k-KWpTjWYVs/maxresdefault.jpg', sourceName: 'Delta Force · Official Trailer', sourceUrl: 'https://www.youtube.com/watch?v=k-KWpTjWYVs', tags: ['直升机', '战役', '协作'] },
]

const h3Prompts = [
  { id: 'h3-breach', type: 'prompt', typeLabel: 'H3 镜头方案', title: '10 秒 · 黄昏突入', description: '原创战术短片方案：避免复刻游戏人物、标志、UI 或具体关卡；以现代战术氛围完成同类情绪表达。', tags: ['10s', '原创', '动作'], prompt: `integrated_multimodal_description: [Shot 1] Cinematic live-action tactical fiction, a medium-wide shot shows an original four-person rescue squad approaching an abandoned desert relay station at dusk. No game logos, recognizable characters, interfaces, or copied map layouts appear. The camera tracks behind the squad with small amplitude at a steady pace as dust moves across the ground and the team silently signals toward a glowing doorway. [Shot 2] At 00:05.500, the camera cuts to a low close shot of one operator pushing the door open; warm light spills across the concrete while the squad moves through the entrance in a single coordinated motion.

overall_soundscape: Dry wind moves through loose metal panels, with restrained boot steps, fabric movement, radio static without intelligible speech, and a distant helicopter-like rumble.

non_diegetic_music: Low pulsing synthesizer and muted taiko hits at a moderate tempo, rising once at the door opening before ending abruptly.` },
  { id: 'h3-extraction', type: 'prompt', typeLabel: 'H3 镜头方案', title: '10 秒 · 雨夜撤离', description: '原创撤离段落；把雨夜、车灯、紧张协作转成可直接用于 MiniMax H3 的完整音视频提示词。', tags: ['10s', '雨夜', '撤离'], prompt: `integrated_multimodal_description: [Shot 1] Cinematic live-action tactical fiction, a wide night shot frames an original rain-soaked industrial alley lit by a white evacuation van and sodium-vapor streetlights. No game logos, recognizable characters, interfaces, or copied map layouts appear. The camera pushes in with small amplitude at slow speed as two rescue operators guide an injured civilian toward the van, their reflective rain capes catching the headlights. [Shot 2] At 00:06.000, the shot cuts to a close-up from inside the van as the door slides shut; rain streaks blur the outside lights and the vehicle pulls away.

overall_soundscape: Heavy rain strikes sheet metal and puddles. Fast footsteps splash through shallow water, the van door rolls closed, and a diesel engine accelerates into the distance.

non_diegetic_music: Sparse piano notes over a slow electronic bass pulse, with the final note sustained as the van exits frame.` },
]

const visibleCards = computed(() => {
  const cards = [...materials, ...h3Prompts]
  return activeCategory.value === 'all' ? cards : cards.filter((item) => item.type === activeCategory.value)
})

async function copyPrompt(item) {
  try {
    await navigator.clipboard.writeText(item.prompt)
    copiedId.value = item.id
    window.setTimeout(() => { if (copiedId.value === item.id) copiedId.value = '' }, 1800)
  } catch { copiedId.value = `${item.id}-manual` }
}
</script>

<template>
  <main class="delta-studio">
    <section class="hero"><div class="hero-grid" aria-hidden="true" /><p class="eyebrow">DELTA FORCE · FAN CREATION STUDIO</p><h1>三角洲二创<br /><em>素材指挥台</em></h1><p class="lede">收集画面与预告参考，按用途整理；同时提供可用于 MiniMax H3 的原创战术短片镜头方案。</p><div class="hero-meta"><span>04 个参考条目</span><span>02 个 H3 方案</span><span>10 秒镜头规格</span></div></section>
    <section class="library" aria-labelledby="library-title"><div class="library-topline"><div><p class="section-kicker">SOURCE LIBRARY</p><h2 id="library-title">按创作用途分类</h2></div><div class="filters" role="tablist" aria-label="素材分类"><button v-for="category in categories" :key="category.id" role="tab" :aria-selected="activeCategory === category.id" :class="{ active: activeCategory === category.id }" @click="activeCategory = category.id">{{ category.label }}</button></div></div>
      <div class="cards"><article v-for="item in visibleCards" :key="item.id" class="card" :class="`card--${item.type}`"><template v-if="item.image"><a class="image-link" :href="item.sourceUrl" target="_blank" rel="noopener noreferrer" :aria-label="`打开 ${item.title} 来源`"><img :src="item.image" :alt="item.title" loading="lazy" /><span class="open-mark">↗</span></a></template><div v-else class="prompt-cover"><span>H3</span><i>ORIGINAL<br />SHOT PLAN</i></div><div class="card-body"><span class="type-chip">{{ item.typeLabel }}</span><h3>{{ item.title }}</h3><p>{{ item.description }}</p><div class="tags"><span v-for="tag in item.tags" :key="tag">{{ tag }}</span></div><template v-if="item.sourceUrl"><a class="source" :href="item.sourceUrl" target="_blank" rel="noopener noreferrer">来源：{{ item.sourceName }} <b>↗</b></a></template><template v-else><button class="copy" @click="copyPrompt(item)">{{ copiedId === item.id ? '已复制 H3 提示词' : '复制 H3 提示词' }}</button><p v-if="copiedId === `${item.id}-manual`" class="copy-note">浏览器未授予剪贴板权限，请手动复制。</p></template></div></article></div>
    </section>
    <aside class="rights-note"><strong>二创使用提示</strong><span>本站仅展示第三方公开来源的缩略图与跳转链接，用于创作研究。发布、商用或直接使用游戏画面/音频前，请自行确认 Delta Force、Steam 与视频平台的版权及使用规则；H3 方案默认要求原创人物、场景和标识。</span></aside>
  </main>
</template>

<style scoped>
.delta-studio { min-height:100vh; color:#eef3f6; background:#080d10; font-family:Inter,ui-sans-serif,system-ui,sans-serif; }.hero{position:relative;isolation:isolate;min-height:470px;overflow:hidden;padding:104px max(6vw,28px) 68px;background:radial-gradient(circle at 80% 18%,rgba(217,126,51,.24),transparent 25%),linear-gradient(120deg,#121d20 0%,#0c1214 48%,#0a0e10 100%)}.hero-grid{position:absolute;z-index:-1;inset:0;opacity:.24;background-image:linear-gradient(rgba(196,211,211,.15) 1px,transparent 1px),linear-gradient(90deg,rgba(196,211,211,.15) 1px,transparent 1px);background-size:44px 44px;mask-image:linear-gradient(90deg,#000 15%,transparent 86%)}.eyebrow,.section-kicker{margin:0 0 16px;color:#e18b46;font-weight:700;font-size:11px;letter-spacing:.18em}h1{max-width:820px;margin:0;font-size:clamp(48px,8.3vw,112px);line-height:.88;letter-spacing:-.065em;text-transform:uppercase}h1 em{color:#e18b46;font-family:Georgia,serif;font-weight:400;text-transform:none}.lede{max-width:570px;margin:30px 0 0;color:#adbcc1;font-size:17px;line-height:1.75}.hero-meta{display:flex;flex-wrap:wrap;gap:14px 26px;margin-top:48px;color:#dbe3e5;font-size:12px;font-weight:700;letter-spacing:.08em;text-transform:uppercase}.hero-meta span::before{content:'●';margin-right:8px;color:#e18b46}.library{padding:64px max(6vw,28px) 78px}.library-topline{display:flex;align-items:end;justify-content:space-between;gap:28px;margin-bottom:35px}.library h2{margin:0;font-size:clamp(28px,3.2vw,44px);letter-spacing:-.045em}.filters{display:flex;flex-wrap:wrap;gap:8px}.filters button{cursor:pointer;border:1px solid #334047;border-radius:100px;padding:9px 14px;color:#adbbc0;background:transparent;font:inherit;font-size:13px;transition:.2s ease}.filters button:hover,.filters button.active{border-color:#e18b46;color:#111619;background:#e18b46}.cards{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:18px}.card{overflow:hidden;border:1px solid #273136;border-radius:15px;background:#101719;box-shadow:0 16px 44px rgba(0,0,0,.12)}.image-link{position:relative;display:block;overflow:hidden;aspect-ratio:16 / 8.8;background:#1a2428}.image-link img{width:100%;height:100%;object-fit:cover;transition:transform .45s ease}.image-link:hover img{transform:scale(1.045)}.open-mark{position:absolute;top:13px;right:13px;display:grid;width:31px;height:31px;place-items:center;border-radius:50%;color:#0b0e10;background:#f1f4f1;font-size:18px}.prompt-cover{display:flex;align-items:end;justify-content:space-between;box-sizing:border-box;aspect-ratio:16 / 8.8;padding:20px;background:linear-gradient(135deg,#28373a,#141b1d 65%)}.prompt-cover span{color:#e18b46;font-size:78px;font-weight:800;letter-spacing:-.09em;line-height:.8}.prompt-cover i{color:#9babad;font-size:10px;font-style:normal;font-weight:700;line-height:1.5;letter-spacing:.14em;text-align:right}.card-body{padding:20px}.type-chip{display:inline-block;margin-bottom:11px;color:#e9a469;font-size:11px;font-weight:750;letter-spacing:.1em;text-transform:uppercase}h3{margin:0;font-size:20px;letter-spacing:-.03em}.card-body>p{min-height:66px;margin:10px 0 13px;color:#9eafb4;font-size:13px;line-height:1.65}.tags{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:20px}.tags span{padding:4px 8px;border-radius:4px;color:#b8c4c6;background:#1d282c;font-size:11px}.source,.copy{display:inline-flex;align-items:center;gap:5px;color:#e8edf0;font-size:12px;font-weight:650;text-decoration:none}.source b{color:#e18b46;font-size:17px}.copy{cursor:pointer;border:0;padding:0;background:transparent;font:inherit}.copy:hover{color:#e18b46}.copy-note{min-height:0!important;margin:7px 0 0!important;color:#e9a469!important;font-size:11px!important}.rights-note{display:grid;grid-template-columns:auto 1fr;gap:18px;margin:0 max(6vw,28px);border-top:1px solid #2a3539;padding:24px 0 46px;color:#9cadb2;font-size:12px;line-height:1.65}.rights-note strong{color:#e18b46;white-space:nowrap}@media(max-width:900px){.cards{grid-template-columns:repeat(2,minmax(0,1fr))}.library-topline{align-items:start;flex-direction:column}}@media(max-width:600px){.hero{min-height:420px;padding-top:92px}.cards{grid-template-columns:1fr}.rights-note{grid-template-columns:1fr;gap:5px}}
</style>
