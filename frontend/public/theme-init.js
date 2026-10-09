// Runs before the first paint (a blocking script in index.html), so a reload in
// dark mode never flashes light. A file, not an inline script: the CSP allows
// scripts from the app only. Keep in step with src/theme.ts.
;(function () {
  var stored = null
  try {
    stored = localStorage.getItem('theme')
  } catch (e) {
    // Storage blocked: follow the system
  }
  var dark =
    stored === 'dark' ||
    (stored !== 'light' && window.matchMedia('(prefers-color-scheme: dark)').matches)
  document.documentElement.classList.toggle('dark', dark)
})()
