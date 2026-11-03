import { createApp } from 'vue'
import { createPinia } from 'pinia'
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import 'element-plus/theme-chalk/dark/css-vars.css'
import './styles/main.scss'
import { applyTheme, getTheme } from './composables/useTheme'

// 首屏渲染前应用已保存的主题，避免暗色用户看到白屏闪烁
applyTheme(getTheme())

import App from './App.vue'

const app = createApp(App)
app.use(createPinia())
app.use(ElementPlus, { locale: zhCn })
app.mount('#app')
