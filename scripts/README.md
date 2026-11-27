# PDF build (Circle3 / PLoP paper)

Build the Circle3 PLoP paper PDF using Pandoc + a LaTeX PDF engine (default: `xelatex`).

## Prerequisites

This pipeline shells out to external tools:

- **Pandoc** (`pandoc`) — converts the assembled Markdown into PDF.
- **TeX/LaTeX** (TeX Live) — provides the PDF engine. Default is **XeLaTeX** (`xelatex`).
- **Fonts + fontconfig** — the LaTeX template uses system fonts (via `fontspec`). The default styling expects **Noto Sans**.

### Linux (Debian/Ubuntu)

Install everything needed for the default config (`--pdf-engine=xelatex` + Noto fonts):

```bash
sudo apt-get update
sudo apt-get install -y \
  pandoc \
  texlive-xetex \
  texlive-latex-recommended \
  texlive-latex-extra \
  texlive-fonts-recommended \
  fontconfig \
  fonts-noto-core \
  fonts-noto-ui-core

# Refresh font cache (usually automatic, but helpful in containers)
fc-cache -f -v
```

Quick sanity checks:

```bash
pandoc --version
xelatex --version
fc-match "Noto Sans"
```

#### Common errors and what to install (Linux)

- `bash: pandoc: command not found`

  ```bash
  sudo apt-get update
  sudo apt-get install -y pandoc
  ```

- `PDF engine 'xelatex' is not installed`

  ```bash
  sudo apt-get update
  sudo apt-get install -y texlive-xetex
  ```

- `Package fontspec Error: The font "Noto Sans" cannot be found`

  ```bash
  sudo apt-get update
  sudo apt-get install -y fonts-noto-core fonts-noto-ui-core fontconfig
  fc-cache -f -v
  ```

  If you don’t want to install Noto, change the template/config to a font that exists in your environment.

### macOS

```bash
brew install pandoc
brew install --cask mactex-no-gui
```

If you hit a `Noto Sans` error, install the font (or change `mainfont`/template settings if applicable):

```bash
brew install --cask font-noto-sans
```

### Notes

- The PDF engine is configurable via `pdf_engine` in `scripts/plop.paper.config.yaml` (or under `output.pdf_engine`). The default is `xelatex`.
- The build script checks for `pandoc` and the selected PDF engine on `PATH`. Missing fonts show up later as a LaTeX `fontspec` error.

## Run

```bash
python3 scripts/build-pdf.py scripts/plop.paper.config.yaml
```

- Output: `assets/pdfs/circle3-paper.pdf`
- Intermediate markdown (for debugging): `/tmp/plop.paper.config.debug-intermediate.md` (derived from the config filename)

## Paper Workflows

The complete Paper capability inventory and canonical ordering are in [Quality Stance → Capabilities in Operation](../.github/QUALITY.md#capabilities-in-operation); this section details the build, review, and publication entry points.

- **[Paper: Build Integrity](../.github/workflows/paper-build-integrity.yml)** — Runs on every pull request to `main` and in Quality Pulse; verifies a fresh, nonempty, readable author-edition PDF and provides a downloadable preview.
- **[Paper: Preview](../.github/workflows/paper-preview.yml)** — Runs the same build on demand.
- **[Paper: Publish](../.github/workflows/paper-publish.yml)** — Run from `main` with the intended version (e.g. `0.2.3`). It promotes the `Unreleased` entries on a dedicated release branch and opens or reuses one changelog-only PR. The workflow reports the PR URL and waits up to 30 minutes, polling once per minute, for the human-reviewed merge and positive evidence for all twelve Quality Gate checks. It does not approve, merge, or bypass the PR.

Each release includes:

- [`circle3-paper.pdf`](https://github.com/basil-one/circle/releases/latest/download/circle3-paper.pdf) — latest PDF, used by the website.
- `circle3-paper-vX.Y.Z.pdf` — versioned PDF for archival and citation links.

Release notes combine version-specific entries from `CHANGELOG.md` with shared content maintained in `scripts/paper_release.py`.

Paper validators and link-audit pins live in `scripts/paper/`; their tests live in `tests/paper/`. The build configuration, template, source content, PDF builder, release helper, and `build-paper-pdf.sh` intentionally retain their established `scripts/` paths so current release tooling can run against historical checkouts such as v0.2.2.

### Human publication and recovery

1. Dispatch **Paper: Publish** from `main` with the intended stable version.
2. Open the reported release PR, review its changelog-only diff, approve the requested GitHub Actions runs if GitHub requires that approval, and merge through the existing Quality Gate.
3. The same run verifies the exact PR, all twelve required checks, and its immutable merge revision before building the PDF, creating the tag, and continuing through the existing draft/assets/verification/release flow.

The workflow waits for up to 30 minutes at one-minute intervals. A timeout, cancellation, closed-unmerged PR, failed/skipped/missing check, inaccessible GitHub evidence, stale release intent, or unexpected diff stops publication without tagging. Rerun **Paper: Publish** with the same version to resume. It reuses a release branch even if PR creation was interrupted, an open PR, a verified merged PR awaiting its tag, or an existing tag awaiting release completion. A branch that is stale or has been changed unexpectedly is never force-pushed; reconcile it without changing the approved changelog intent, then rerun. Another pending release version or a completed release is not replaced.

The workflow uses only the repository `GITHUB_TOKEN` with narrow `contents:write`, `pull-requests:write`, and `checks:read` permissions. Repository or organization Actions settings may need **Settings → Actions → General → Workflow permissions → Allow GitHub Actions to create and approve pull requests** enabled so the token can create the PR. Paper Publish uses this only to create the PR; it never approves or merges. If the setting is unavailable or creation is denied, ask a repository owner to configure it; do not use a PAT, App, deploy key, or bypass.

Publication Integrity reports a verified PR/tag/draft interruption as **pending**, not healthy, only when it can verify the exact merged release PR, changelog-only promotion, ancestry, and twelve successful checks; it also continues verifying the prior published release. Once a release is complete, ongoing verification uses durable PR, source-tree, tag, changelog, and artifact evidence, not historical check-run records that may expire under GitHub retention. Checks remain mandatory before publication and when verifying an in-flight release. Unexplained or contradictory state remains failed or unknown and requires human attention.

Completed releases are protected from replacement. If one asset survives, recovery copies that published asset to the missing name rather than pairing it with a newly rebuilt PDF. If both assets differ, or a published release has no surviving PDF, publication stops for a human repair decision; it does not overwrite either asset.

## Inputs

- `scripts/plop.paper.config.yaml` — build config (strict)
- `scripts/template.tex` — LaTeX template
- `scripts/paper_release.py` — version validation, changelog promotion, and shared release logic (historical compatibility path)
- `scripts/paper/` — content, link, rendering, and publication integrity validators
- `tests/paper/` — paper build, release, and validator tests
- `scripts/content/*.md` — longform markdown blocks referenced by the config
- `_moves/*.md`, `_lenses/*.md` — pattern sources (web + PDF share these)

## Config shape (top-level)

- `output`: `filename`, `directory`
- `metadata`: `title`, `author`, `date`, `url`, `email`, `keywords`
- `framing`: cover/abstract/TOC labels + cover images + `abstract_file` and optional `copyright_notice`
- `body`: `introduction_file` + `back_matter_file` (+ labels/anchors/include flags)
- `sections`: ordered pattern sections with `intro_image` and `patterns[]` entries
- `conclusion`: final page (image + main text file + CTA)
- `citations` (optional): PDF-only in-text citation markers appended after a matched link `url` or literal `text` in pattern/lens bodies (see below)

Inline markdown in YAML is not supported; use `*_file` keys.

### In-text citation markers (`citations`)

`_moves/*.md` and `_lenses/*.md` are shared between the website and the PDF, so
they should stay free of paper-specific citation formatting. To satisfy academic
citation requirements in the PDF only, add rules under top-level `citations` in
`plop.paper.config.yaml`:

```yaml
citations:
  - url: https://fearlesschangepatterns.com   # matches a markdown link target
    annotation: "[3]"                          # appended right after the match; must match the numbered References entry
  - text: "**affect labeling**"                # or match literal text instead
    annotation: "[2]"
    first_only: true                           # only annotate the first occurrence
```

Each rule matches either `url` (a link target, trailing slash-insensitive) or
`text` (literal, matched as-is — include markdown emphasis like `**...**` if
needed), and appends `annotation` immediately after the match. `first_only`
(default `false`) limits annotation to the first match only. Annotations use
ACM-style bracketed numeric citations (e.g. `[3]`) matching the numbered
References list. Keep this list in sync with `content/plop.paper.back-matter.md`,
including citation numbers if the reference order changes.
