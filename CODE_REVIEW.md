# Code review: strigil vs Morgan Library pages

**Tested URLs (all succeeded with `--js`):**

| URL | Result |
|-----|--------|
| `https://ica.themorgan.org/` | Text + 1 image |
| `https://ica.themorgan.org/manuscript/thumbs/160011` | Text + 16 images |
| `https://ica.themorgan.org/manuscript/thumbs/160012` | Text only |
| `https://corsair.themorgan.org/vwebv/holdingsInfo?bibId=160011` | Text only |

---

## What’s working well

- **Single browser context (`fetcher.py`)**  
  With `--js`, one Playwright context is reused for the initial page and all asset requests. Cookies from the first load apply to images/PDFs, which avoids 403 on Morgan (and similar) sites.

- **Retries and backoff**  
  Both httpx and browser paths retry on 429 and 5xx (500, 502, 503, 504). For 5xx the fetcher uses more attempts (6) and a longer base wait (5s) so flaky IIIF/library servers often recover.

- **Extractors**  
  `find_image_urls` covers `img[src]`, `srcset`, `data-src`, `data-lazy-src`, `data-original`, `data-srcset`, and `<source srcset>`. Thumb→full heuristics and favicon skip are in place.

- **Storage**  
  Domain-based dirs, unique filenames via `_ensure_unique`, and manifest for idempotent re-runs are clear and consistent.

- **Text**  
  Readability-lxml with tag-heuristic fallback and script/style/nav removal gives good main-content extraction on these pages.

---

## Suggestions (optional)

1. **Text filename for query-heavy URLs**  
  `slug_from_url()` uses only `path`, so e.g. `holdingsInfo?bibId=160011` and `?bibId=160012` both map to the same base slug and differ only by `_ensure_unique` suffix. If you want stable, human-readable names per query, consider including a short query hash or sanitized query in the slug (e.g. `vwebv_holdingsInfo_bib160011.txt`).

2. **JS-heavy image discovery**  
  Manuscript 160012 returned no images; 160011 did. If some Morgan (or other) pages inject images only after `domcontentloaded`, you could add an optional “wait for images” mode: e.g. `wait_until="networkidle"` or a short delay before `page.content()` when `--js` is set. Not required for current Morgan tests.

3. **Browser `head_metadata` timeout**  
  Playwright’s `request.head()` is called with `timeout=timeout*1000` (seconds→ms). If you see timeouts on slow HEAD responses, confirm the API expects this parameter name/unit in your Playwright version.

4. **Robots and `--js`**  
  `can_fetch()` uses a fixed User-Agent; when running with `--js`, the actual requests are from the browser. If a site’s robots.txt is UA-specific, consider passing a browser-like UA into `can_fetch` when `--js` is used, so allow/deny rules match real requests.

---

## Verdict

Behavior on the sampled Morgan pages is correct: no 403s with `--js`, text and images are extracted where present, and the design (shared context, extractors, storage) is solid. The items above are small refinements, not blockers.
