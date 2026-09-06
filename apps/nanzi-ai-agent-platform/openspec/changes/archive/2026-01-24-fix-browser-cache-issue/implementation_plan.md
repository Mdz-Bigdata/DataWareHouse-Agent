# 解决前端编译后浏览器缓存问题的实施计划

## 1. 为什么这么做 (Why)
在前端（Vue/Vite）重新编译并部署后，浏览器往往会缓存旧的 `index.html`。由于 Vite 在构建时会为资源文件（JS/CSS）生成唯一的 Hash 后缀，旧的 `index.html` 依然引用着旧的资源路径。当旧资源被覆盖或删除时，用户就会遇到页面白屏、资源加载失败等问题，必须通过强刷（Ctrl+F5）或无痕模式才能看到更新。

## 2. 准备怎么搞 (How)
通过“后端强制不缓存” + “前端 Meta 辅助”的方案来解决：
1.  **后端干预**：修改 `app/main.py` 中的 `serve_spa` 路由。在返回 `index.html` 时，强制注入 `Cache-Control` 响应头，告诉浏览器“不要缓存这个 HTML 文件，每次都要来服务器问一下”。
2.  **前端辅助**：在 `index.html` 中添加相关的 Meta 标签，防止部分浏览器在没有收到 Header 的情况下过度缓存。
3.  **验证**：编译并重启服务，通过浏览器的开发者工具（Network 标签）确认 `index.html` 的响应头。

## 3. 为什么这么修改 (Rationale)
- **为什么针对 index.html？** 因为 JS/CSS 文件带有 Hash，本身就不怕缓存（只要 HTML 是新的，就会引用新的 Hash）。唯独 `index.html` 文件名是不变的，是缓存的重灾区。
- **为什么使用 `no-store, no-cache, must-revalidate`？** 这是最严厉的缓存控制组合，确保任何环节（浏览器、CDN、代理服务器）都不会存储该文件的副本。
- **为什么不改 Vite 配置？** Vite 默认的 Hash 机制已经非常成熟，问题的根源在于入口 HTML 的分发策略，而非资源文件的命名。

## 4. 修改步骤
1. 修改 `app/main.py`：在 `serve_spa` 函数中增加 headers 参数。
2. 修改 `frontend/index.html`：在 `<head>` 中添加 Meta 标签。
3. 修复 `ChatInput.vue` 中的 TS 错误以支持顺利编译。
4. 执行编译并重启。
