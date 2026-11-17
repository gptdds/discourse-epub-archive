# Changelog

## v1.0.1 - 2026-11-17

### Fixed

- Use the system HTTPS proxy without reading `.netrc` or rotating proxy endpoints.
- Stop automatic retries immediately on HTTP 429 and persist the global cooldown.
- Fall back to a valid category path when the API returns an empty category slug.
- Save categories only after a successful public API test and show explicit testing,
  success, and failure states.
- Keep checked categories selected when the category search filter hides them.
- Preserve failed-job state so success callbacks do not run after an API test failure.

### Verified

- 52 automated tests pass.
- GUI end-to-end run completed with 2 newly collected topics, 20 unchanged topics
  reused, and 0 task failures.
- Generated EPUB passed EPUBCheck with
  `0 fatals / 0 errors / 0 warnings / 0 infos`.
