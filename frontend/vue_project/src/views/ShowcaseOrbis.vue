<script setup>
/**
 * Orbis.Nft 风格展示页（由 React 稿移植为 Vue + Tailwind）。
 * Tailwind 仅对带 data-tw-important 的根后代生效（与知识图谱一致，避免重复 id）。
 */
import '@/assets/kg-tailwind.css'
import { Bot, ChevronRight, Mail, Search } from 'lucide-vue-next'

const navItems = [
  { label: 'Homepage', target: 'orbis-home' },
  { label: 'Story', target: 'orbis-story' },
  { label: 'Gallery', target: 'orbis-gallery' },
  { label: 'Join', target: 'orbis-join' },
]

const contactLinks = [
  { label: 'Email', href: 'mailto:contact@globemind.top', icon: Mail },
  { label: 'Data Search', href: '/data-service/data-search', icon: Search },
  { label: 'Data Assistant', href: '/data-assistant', icon: Bot },
]

const nftCards = [
  {
    imageSrc: '/imgs/home/home-bg2.webp',
    score: '8.7/10',
  },
  {
    imageSrc: '/imgs/home/home-bg3.webp',
    score: '9.0/10',
  },
  {
    imageSrc: '/imgs/home/home-bg4.webp',
    score: '8.2/10',
  },
]

const blurb =
  'A digital object fixed beyond time and place. An exploration of distance, form, and silence in space'
</script>

<template>
  <div
    id="orbis-showcase-root"
    data-tw-important
    class="relative bg-background text-cream min-h-screen uppercase font-grotesk selection:bg-neon selection:text-background pt-16"
  >
    <div class="fixed inset-0 z-50 pointer-events-none texture-overlay" />

    <!-- SECTION 1: HERO -->
    <section id="orbis-home" class="relative min-h-[calc(100vh-4rem)] w-full flex flex-col rounded-b-[32px] overflow-hidden">
      <div class="absolute inset-0 z-0 bg-cover bg-center" style="background-image:url('/imgs/home/home-bg1.webp')" />

      <div class="relative z-10 w-full max-w-[1831px] mx-auto px-6 lg:px-12 pt-8 flex-grow flex flex-col">
        <header class="flex justify-between items-center">
          <div class="text-[16px] tracking-wider">Orbis.Nft</div>
          <nav class="hidden lg:flex liquid-glass rounded-[28px] px-[52px] py-[24px] gap-8">
	            <a
	              v-for="item in navItems"
	              :key="item.target"
	              :href="`#${item.target}`"
	              class="text-[13px] hover:text-neon transition-colors duration-300"
	            >
	              {{ item.label }}
	            </a>
	          </nav>
          <div class="w-[80px] lg:hidden" />
        </header>

        <div class="flex-grow flex flex-col justify-center relative mt-20 lg:mt-0">
          <div class="relative max-w-[780px] lg:ml-32 w-full">
            <h1 class="text-[40px] sm:text-[60px] md:text-[75px] lg:text-[90px] leading-[1.05] sm:leading-[1]">
              Beyond earth <br />
              and ( its ) familiar boundaries
            </h1>
            <div
              class="absolute top-0 right-0 lg:-right-20 -rotate-1 text-neon mix-blend-exclusion opacity-90 font-condiment lowercase capitalize text-[24px] sm:text-[36px] md:text-[48px]"
            >
              Nft collection
            </div>
          </div>

          <div class="flex lg:hidden justify-center gap-4 mt-12">
	            <a
	              v-for="link in contactLinks"
	              :key="`mobile-${link.label}`"
	              :href="link.href"
	              :aria-label="link.label"
	              class="w-[56px] h-[56px] liquid-glass rounded-[1rem] flex items-center justify-center hover:bg-white/10 transition-colors duration-300"
	            >
	              <component :is="link.icon" :size="20" />
	            </a>
	          </div>
        </div>

        <div class="hidden lg:flex absolute top-8 right-12 flex-col gap-4">
	          <a
	            v-for="link in contactLinks"
	            :key="`desktop-${link.label}`"
	            :href="link.href"
	            :aria-label="link.label"
	            class="w-[56px] h-[56px] liquid-glass rounded-[1rem] flex items-center justify-center hover:bg-white/10 transition-colors duration-300"
	          >
	            <component :is="link.icon" :size="20" />
	          </a>
	        </div>
      </div>
    </section>

    <!-- SECTION 2: ABOUT -->
    <section id="orbis-story" class="relative min-h-screen w-full flex flex-col py-16 sm:py-24 lg:py-[96px]">
      <div class="absolute inset-0 z-0 bg-cover bg-center" style="background-image:url('/imgs/home/home-bg2.webp')" />

      <div
        class="relative z-10 w-full max-w-[1831px] mx-auto px-6 lg:px-12 h-full flex flex-col justify-between flex-grow"
      >
        <div class="flex flex-col lg:flex-row justify-between items-start lg:items-end gap-12">
          <div class="relative">
            <h2 class="text-[32px] sm:text-[48px] lg:text-[60px] leading-tight">
              Hello!<br />I'm orbis
            </h2>
            <div
              class="absolute -bottom-4 sm:-bottom-8 -right-8 sm:-right-16 -rotate-[4deg] text-neon mix-blend-exclusion font-condiment lowercase capitalize text-[36px] sm:text-[52px] lg:text-[68px]"
            >
              Orbis
            </div>
          </div>
          <p class="font-mono text-[14px] sm:text-[16px] max-w-[266px] leading-relaxed">
            {{ blurb }}
          </p>
        </div>

        <div class="flex justify-between items-end mt-24">
          <div class="w-full lg:w-1/2 flex flex-col gap-8 text-[#010828] sm:text-cream/10">
            <p class="font-mono text-[14px] sm:text-[16px] max-w-[266px]">{{ blurb }}</p>
            <p class="font-mono text-[14px] sm:text-[16px] max-w-[266px]">{{ blurb }}</p>
          </div>
          <div class="hidden lg:flex w-1/2 flex-col gap-8 items-end text-cream/10">
            <p class="font-mono text-[14px] sm:text-[16px] max-w-[266px]">{{ blurb }}</p>
            <p class="font-mono text-[14px] sm:text-[16px] max-w-[266px]">{{ blurb }}</p>
          </div>
        </div>
      </div>
    </section>

    <!-- SECTION 3: GRID -->
    <section id="orbis-gallery" class="relative w-full bg-background py-24">
      <div class="w-full max-w-[1831px] mx-auto px-6 lg:px-12">
        <div class="flex flex-col lg:flex-row justify-between items-start lg:items-end mb-16 gap-10">
          <h2 class="text-[32px] sm:text-[48px] lg:text-[60px] leading-[1.1]">
            Collection of <br />
            <span class="inline-block ml-12 sm:ml-24 lg:ml-32">
              <span class="font-condiment lowercase capitalize text-neon mix-blend-exclusion">Space</span> objects
            </span>
          </h2>

	          <a href="#orbis-gallery" class="group relative flex items-baseline gap-3">
	            <span class="text-[32px] sm:text-[48px] lg:text-[60px]">SEE</span>
	            <div class="flex flex-col text-left text-[20px] sm:text-[28px] lg:text-[36px] leading-[0.9]">
	              <span>ALL</span>
              <span>CREATORS</span>
            </div>
            <div
	              class="absolute -bottom-2 left-0 w-full h-[6px] sm:h-[8px] lg:h-[10px] bg-neon transform scale-x-100 origin-left transition-transform duration-300 group-hover:scale-x-110"
	            />
	          </a>
        </div>

        <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-[24px]">
          <div
            v-for="(card, idx) in nftCards"
            :key="idx"
            class="liquid-glass rounded-[32px] p-[18px] hover:bg-white/10 transition-colors duration-500 group"
          >
            <div class="relative w-full pb-[100%] rounded-[24px] overflow-hidden">
              <img
                loading="lazy"
                decoding="async"
                class="absolute inset-0 w-full h-full object-cover transform group-hover:scale-105 transition-transform duration-700"
                :src="card.imageSrc"
                alt=""
              />
            </div>
            <div class="relative mt-4">
              <div class="liquid-glass rounded-[20px] px-5 py-4 flex justify-between items-center">
                <div class="flex flex-col">
                  <span class="text-[11px] text-cream/70">RARITY SCORE:</span>
                  <span class="text-[16px]">{{ card.score }}</span>
                </div>
	                <a
	                  href="#orbis-join"
	                  aria-label="Join Orbis collection"
	                  class="w-[48px] h-[48px] rounded-full bg-gradient-to-br from-[#b724ff] to-[#7c3aed] flex items-center justify-center shadow-lg shadow-purple-500/50 hover:scale-110 transition-transform duration-300"
	                >
	                  <ChevronRight class="text-white" :size="24" />
	                </a>
              </div>
            </div>
          </div>
        </div>
      </div>
    </section>

    <!-- SECTION 4: CTA -->
    <section id="orbis-join" class="relative w-full overflow-hidden bg-background">
      <div class="min-h-[70vh] w-full bg-cover bg-center" style="background-image:url('/imgs/home/home-bg4.webp')" />

      <div
        class="absolute inset-0 z-10 flex flex-col justify-center items-end pr-[10%] lg:pr-[20%] pl-[10%] lg:pl-[15%]"
      >
        <div class="relative text-right w-full lg:w-auto">
          <div
            class="absolute -top-6 sm:-top-10 lg:-top-14 left-0 text-neon mix-blend-exclusion font-condiment lowercase capitalize text-[17px] sm:text-[40px] lg:text-[68px]"
          >
            Go beyond
          </div>
          <h2 class="text-[16px] sm:text-[36px] lg:text-[60px] leading-[1.1]">
            <div class="mb-4 sm:mb-8 lg:mb-12">JOIN US.</div>
            <div>REVEAL WHAT'S HIDDEN.</div>
            <div>DEFINE WHAT'S NEXT.</div>
            <div>FOLLOW THE SIGNAL.</div>
          </h2>
        </div>
      </div>

      <div class="absolute left-[8%] bottom-[12%] sm:bottom-[15%] lg:bottom-[20%] z-20">
        <div
          class="liquid-glass rounded-[0.5rem] sm:rounded-[1rem] lg:rounded-[1.25rem] flex flex-col overflow-hidden w-[14vw] sm:w-[14.375rem] md:w-[10.78125rem] lg:w-[16.77rem]"
        >
	          <a
	            v-for="link in contactLinks"
	            :key="`cta-${link.label}`"
	            :href="link.href"
	            :aria-label="link.label"
	            class="w-full aspect-square flex items-center justify-center hover:bg-white/10 transition-colors duration-300 border-b border-white/10"
	          >
	            <component :is="link.icon" class="w-[40%] h-[40%]" />
	          </a>
	        </div>
      </div>
    </section>
  </div>
</template>
