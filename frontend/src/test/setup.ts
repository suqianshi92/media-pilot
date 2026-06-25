import '@testing-library/jest-dom/vitest'

// 测试运行在 mock 模式: createTaskService() 默认走 mock,
// 避免测试中触发真实 fetch / 后端契约.
import.meta.env.VITE_API_MODE = 'mock'

// jsdom polyfill for matchMedia (used by theme system)
if (typeof window !== 'undefined' && !window.matchMedia) {
  Object.defineProperty(window, 'matchMedia', {
    writable: true,
    value: (query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    }),
  })
}

// jsdom polyfill: Element.scrollTo 不存在, AgentPanel 的 useFollowScroll hook
// 在 useEffect 里调用. 同时 scrollHeight / scrollTop / clientHeight 默认
// 都是 0 — 不影响 useFollowScroll 的距离判定 (0 - 0 - 0 = 0 ≤ 80 → 一直
// 处于 follow=true 状态, 自动跟随. scrollTo 桩本身记空跑即可.
if (typeof Element !== 'undefined' && !Element.prototype.scrollTo) {
  Element.prototype.scrollTo = function (
    this: HTMLElement,
    arg?: number | ScrollToOptions,
  ) {
    const opts = typeof arg === 'number' ? { top: arg, left: 0 } : (arg ?? {})
    if (typeof opts.top === 'number') this.scrollTop = opts.top
    if (typeof opts.left === 'number') this.scrollLeft = opts.left
  }
}
