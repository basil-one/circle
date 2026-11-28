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
- **[Release Publish](../.github/workflows/release-publish.yml)** — Publishes a versioned milestone and its reviewed paper. Follow the [release conventions](../.github/CONVENTIONS.md#changelog-and-releases).

Each release includes [`circle3-paper.pdf`](https://github.com/basil-one/circle/releases/latest/download/circle3-paper.pdf) for the website and `circle3-paper-vX.Y.Z.pdf` for citation and archival links. The PDFs must be readable and byte-identical. Release notes use the version entry from `CHANGELOG.md`.

### PDFs and interrupted drafts

Release Publish builds and validates the paper in a detached checkout of the approved revision when the draft has neither required PDF. If one verified draft PDF survives, the workflow uploads those same bytes under the missing name. If both are present, it verifies them and can finish the draft without rebuilding. Each PDF must match its asset metadata and the other download before publication.

Paper Publication Integrity checks the published tag, changelog, asset metadata, and public PDFs. It verifies the existing publication while a newer release is pending. Missing or conflicting assets on a published release require a human repair decision.

## Inputs

- `scripts/plop.paper.config.yaml` — build config (strict)
- `scripts/template.tex` — LaTeX template
- `scripts/paper/` — content, link, rendering, and publication integrity validators
- `tests/paper/` — paper build, release helper, and validator tests
- `scripts/orchestration/release_publish.py` and `tests/orchestration/` — release workflow orchestration and tests
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
