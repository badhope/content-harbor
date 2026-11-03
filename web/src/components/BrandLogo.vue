<template>
  <span class="brand" :class="{ big: size === 'big' }">
    <svg class="brand-mark" :style="{ width: px + 'px', height: px + 'px' }" viewBox="0 0 48 48" aria-hidden="true">
      <!-- Harbor mark：圆角方底 + 船锚 + 双波浪（锚定多平台，内容入港） -->
      <rect x="3" y="3" width="42" height="42" rx="12" fill="var(--surface)" stroke="var(--line-strong)" stroke-width="1" />
      <!-- 锚：环 + 杆 + 横杆 + 锚臂 -->
      <circle cx="24" cy="12.4" r="3.1" fill="none" stroke="var(--accent)" stroke-width="2.6" />
      <path d="M24 15.7 V 35.6" stroke="var(--accent)" stroke-width="2.7" stroke-linecap="round" />
      <path d="M17.7 20.2 H 30.3" stroke="var(--accent)" stroke-width="2.3" stroke-linecap="round" />
      <path d="M14.8 27.5 A 9.2 9.2 0 0 0 33.2 27.5" fill="none" stroke="var(--accent)" stroke-width="2.7" stroke-linecap="round" />
      <!-- 双波浪：水面，暗示全网分发 -->
      <path d="M14 40.6 q 2.5 -2 5 0 t 5 0 t 5 0 t 5 0" fill="none" stroke="var(--accent)" stroke-width="1.9" stroke-linecap="round" opacity=".75" />
      <path d="M18 43.7 q 2.4 -1.8 4.8 0 t 4.8 0" fill="none" stroke="var(--accent-hi)" stroke-width="1.5" stroke-linecap="round" opacity=".45" />
    </svg>
    <span v-if="withName" class="brand-txt">
      <span class="brand-name">{{ name }}</span>
      <span v-if="sub" class="brand-sub">{{ sub }}</span>
    </span>
  </span>
</template>

<script setup>
defineProps({
  name: { type: String, default: '内容港 Harbor' },
  sub: { type: String, default: '内容进港 · 全网分发' },
  px: { type: Number, default: 28 },
  withName: { type: Boolean, default: true },
  size: { type: String, default: '' }
})
</script>

<style scoped>
.brand {
  display: inline-flex;
  align-items: center;
  gap: 10px;
  user-select: none;
}
.brand-txt { display: flex; flex-direction: column; }
.brand-name {
  font-family: var(--font-serif);
  font-size: 16px;
  font-weight: 600;
  letter-spacing: .04em;
  line-height: 1;
  white-space: nowrap;
  color: var(--tx-1);
}
.brand-sub {
  font-family: var(--font-disp);
  font-size: 10px;
  color: var(--accent);
  letter-spacing: .1em;
  margin-top: 4px;
  line-height: 1;
  white-space: nowrap;
}

/* 手机：品牌文字让位给导航与主操作，只留图形标 */
@media (max-width: 767px) {
  .brand-txt { display: none; }
}
</style>
