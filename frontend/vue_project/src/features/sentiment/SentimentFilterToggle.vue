<template>
  <div :class="variant === 'drawer' ? 'drawer-sentiment-toggle' : 'sentiment-toggle'">
    <button
      v-for="option in SENTIMENT_OPTIONS"
      :key="option.value"
      type="button"
      :class="[
        variant === 'drawer' ? 'drawer-toggle-btn' : 'toggle-btn',
        { active: modelValue === option.value },
      ]"
      @click="$emit('select', option.value)"
    >
      {{ option.label }}
    </button>
  </div>
</template>

<script setup>
import { SENTIMENT_OPTIONS } from './dto.js'

defineProps({
  modelValue: {
    type: String,
    default: 'all',
  },
  variant: {
    type: String,
    default: 'chart',
    validator: (value) => ['chart', 'drawer'].includes(value),
  },
})

defineEmits(['select'])
</script>

<style scoped>
.sentiment-toggle {
  position: absolute;
  top: 6px;
  left: 20px;
  z-index: 5;
  display: flex;
  gap: 2px;
  padding: 3px;
  background: rgba(255, 255, 255, 0.7);
  border: 1px solid rgba(148, 163, 184, 0.18);
  border-radius: 10px;
  backdrop-filter: blur(8px);
}

.toggle-btn {
  padding: 5px 14px;
  color: #94a3b8;
  font-size: 0.78rem;
  font-weight: 700;
  cursor: pointer;
  background: transparent;
  border: none;
  border-radius: 7px;
  transition: all 0.25s ease;
}

.toggle-btn:hover {
  color: #64748b;
  background: rgba(255, 255, 255, 0.5);
}

.toggle-btn.active {
  color: #1e293b;
  background: #fff;
  box-shadow: 0 1px 4px rgba(15, 23, 42, 0.08);
}

.toggle-btn:nth-child(2).active {
  color: #5b72df;
}

.toggle-btn:nth-child(3).active {
  color: #ef4444;
}

.drawer-sentiment-toggle {
  display: flex;
  gap: 2px;
  padding: 3px;
  margin-bottom: 12px;
  background: rgba(255, 255, 255, 0.6);
  border: 1px solid rgba(148, 163, 184, 0.15);
  border-radius: 8px;
}

.drawer-toggle-btn {
  flex: 1;
  padding: 5px 10px;
  color: #94a3b8;
  font-size: 0.78rem;
  font-weight: 700;
  cursor: pointer;
  background: transparent;
  border: none;
  border-radius: 6px;
  transition: all 0.2s ease;
}

.drawer-toggle-btn:hover {
  color: #64748b;
}

.drawer-toggle-btn.active {
  color: #1e293b;
  background: #fff;
  box-shadow: 0 1px 3px rgba(15, 23, 42, 0.08);
}

.drawer-toggle-btn:nth-child(2).active {
  color: #5b72df;
}

.drawer-toggle-btn:nth-child(3).active {
  color: #ef4444;
}
</style>
