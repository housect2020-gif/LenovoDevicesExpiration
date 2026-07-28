# Fleet Warranty Check

A single static page. Upload a CSV of your device inventory, and it looks up
Lenovo warranty status per serial number and flags devices 4+ years old —
entirely in the browser, no backend.

## Deploy to GitHub Pages (step by step)

1. **Create a new repository**
   Go to github.com → **New repository** → name it something like
   `fleet-warranty-check` → Public → Create repository.

2. **Add the file**
   On the repo page, click **Add file → Upload files**, drag in `index.html`
   (and this `README.md` if you want), then **Commit changes**.

3. **Turn on Pages**
   Go to **Settings → Pages** (left sidebar).
   Under "Build and deployment", set **Source** to `Deploy from a branch`.
   Set **Branch** to `main` and folder to `/ (root)`. Click **Save**.

4. **Wait ~1 minute, then visit your site**
   GitHub will show the live URL at the top of the Pages settings page —
   it'll look like:
   `https://<your-username>.github.io/fleet-warranty-check/`

5. **Use it**
   Open that URL, click "Choose CSV file", pick your device export, then
   "Run warranty check." Leave the tab open — for ~350 devices at the
   default 700ms delay, expect it to take roughly 10–15 minutes since it's
   two web requests per device done politely (not in parallel).

6. **Download results**
   Once it finishes, click "Download results CSV" to get your original
   spreadsheet with `Warranty Status`, `Warranty Start`, `Warranty End`,
   `Age (years)`, and `4+ Years Old` columns added.

## Important limitation: CORS

Lenovo's endpoint isn't designed for cross-origin browser requests. The page
tries a direct request first; if that's blocked, it automatically retries
through a public CORS proxy (`api.allorigins.win`). That proxy is free,
third-party, and can occasionally be slow or briefly down — if you see many
rows failing with an error, try again in a few minutes, or swap in a
different CORS proxy by editing the `fetchThroughProxy` function near the
top of the `<script>` block in `index.html`.

Only serial numbers are sent through the proxy — nothing else from your
spreadsheet leaves your browser.

## Updating it later

Any time you want to change the page, edit `index.html` in the repo (or
push a new version) — GitHub Pages redeploys automatically within a minute
or two of every commit to `main`.
