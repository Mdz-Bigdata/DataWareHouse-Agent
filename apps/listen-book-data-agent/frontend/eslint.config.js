// ESLint 9 flat config（Phase 6.1）
// React + TypeScript 项目配置，与 Prettier 协同（eslint-config-prettier 关闭冲突规则）。
// 文档：https://eslint.org/docs/latest/use/configure/configuration-files
import js from '@eslint/js';
import tseslint from 'typescript-eslint';
import react from 'eslint-plugin-react';
import reactHooks from 'eslint-plugin-react-hooks';
import prettierConfig from 'eslint-config-prettier';

export default tseslint.config(
  // 全局忽略
  {
    ignores: ['dist', 'coverage', 'node_modules', '*.config.ts'],
  },
  // 基础 JS 推荐规则
  js.configs.recommended,
  // TypeScript 推荐规则（类型感知）
  ...tseslint.configs.recommended,
  // React 配置
  {
    files: ['src/**/*.{ts,tsx}', 'e2e/**/*.{ts,tsx}'],
    plugins: {
      react,
      'react-hooks': reactHooks,
    },
    languageOptions: {
      parserOptions: {
        ecmaFeatures: { jsx: true },
      },
    },
    settings: {
      react: { version: '18.3' },
    },
    rules: {
      ...react.configs.recommended.rules,
      ...reactHooks.configs.recommended.rules,
      // React 18 不再需要 React import（jsx: react-jsx）
      'react/react-in-jsx-scope': 'off',
      'react/prop-types': 'off',
    },
  },
  // Prettier 兼容：关闭所有与 Prettier 冲突的格式化规则
  prettierConfig,
);
