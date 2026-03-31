<script setup lang="ts">
import type { Tone } from "../../utils/format";

defineProps({
  label: { type: String, required: true },
  value: { type: String, default: "-" },
  detail: { type: String, default: "" },
  tone: { type: String as () => Tone | "", default: "" },
  wide: { type: Boolean, default: false },
  testId: { type: String, default: "" },
});
</script>

<template>
  <div class="metric-cell" :class="[tone ? `tone-${tone}` : '', wide ? 'wide' : '']">
    <span class="metric-label">{{ label }}</span>
    <strong class="metric-value" :data-testid="testId || undefined">{{ value }}</strong>
    <small class="metric-detail" v-if="detail">{{ detail }}</small>
    <slot />
  </div>
</template>

<style scoped>
.metric-cell {
  padding: 8px 10px;
  border-radius: 6px;
  background: rgba(16, 22, 32, 0.6);
  border: 1px solid rgba(55, 90, 120, 0.12);
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.metric-cell.wide {
  grid-column: span 2;
}

.metric-label {
  font-size: 10px;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: #6b7f94;
}

.metric-value {
  font-family: "JetBrains Mono", "Fira Code", monospace;
  font-size: 13px;
  color: #e0e6ed;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.metric-detail {
  font-size: 11px;
  color: #6b7f94;
  line-height: 1.3;
}

.tone-ok {
  border-color: rgba(20, 217, 180, 0.2);
}
.tone-ok .metric-value {
  color: #14d9b4;
}

.tone-warning {
  border-color: rgba(245, 165, 36, 0.2);
}
.tone-warning .metric-value {
  color: #f5a524;
}

.tone-danger {
  border-color: rgba(255, 71, 87, 0.2);
}
.tone-danger .metric-value {
  color: #ff4757;
}
</style>
