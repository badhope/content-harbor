<template>
  <div ref="el" class="chart-panel" :style="{ height: h + 'px' }" />
</template>

<script setup>
import { ref, onMounted, onBeforeUnmount, watch, nextTick } from 'vue'
import * as echarts from 'echarts/core'
import { PieChart, BarChart } from 'echarts/charts'
import { TooltipComponent, LegendComponent, GridComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'

echarts.use([PieChart, BarChart, TooltipComponent, LegendComponent, GridComponent, CanvasRenderer])

const props = defineProps({
  option: { type: Object, required: true },
  h: { type: Number, default: 220 }
})

const el = ref(null)
let chart = null
let observer = null

const render = () => {
  if (!el.value) return
  if (!chart) chart = echarts.init(el.value)
  chart.setOption(props.option, true)
}

onMounted(async () => {
  await nextTick()
  render()
  observer = new ResizeObserver(() => chart && chart.resize())
  if (el.value) observer.observe(el.value)
})

watch(() => props.option, () => render(), { deep: true })

onBeforeUnmount(() => {
  if (observer) observer.disconnect()
  if (chart) { chart.dispose(); chart = null }
})
</script>

<style scoped>
.chart-panel { width: 100%; }
</style>
