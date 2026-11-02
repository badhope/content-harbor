import js from '@eslint/js'
import globals from 'globals'
import pluginVue from 'eslint-plugin-vue'

export default [
  { ignores: ['dist/**', '../server/static/**', 'node_modules/**', 'src-tauri/**'] },
  js.configs.recommended,
  ...pluginVue.configs['flat/recommended'],
  {
    files: ['**/*.{js,vue}'],
    languageOptions: {
      ecmaVersion: 'latest',
      sourceType: 'module',
      globals: { ...globals.browser, ...globals.node }
    },
    rules: {
      // 保持与现有代码风格一致：不强制多词组件名、允许单文件多组件
      'vue/multi-word-component-names': 'off',
      'vue/max-attributes-per-line': 'off',
      'vue/singleline-html-element-content-newline': 'off',
      'vue/html-self-closing': 'off',
      'vue/attributes-order': 'warn',
      // 项目以 v-model 直绑对象字段的方式编辑文章；Vue 3 允许改对象 prop 内部字段，
      // 降为 warn 提示后续可用 defineModel 重构
      'vue/no-mutating-props': 'warn',
      'no-unused-vars': ['error', { argsIgnorePattern: '^_', caughtErrors: 'none' }],
      'no-console': 'off',
      eqeqeq: ['warn', 'smart']
    }
  },
  {
    files: ['**/*.test.js', 'src/test/**'],
    languageOptions: { globals: { ...globals.node, ...globals.vitest } }
  }
]
