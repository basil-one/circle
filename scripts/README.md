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

### Human publication and recovery

Release Publish reports the changelog-only PR and polls once per minute for up to 30 minutes. After a human merges it through the Quality Gate, publication requires all twelve successful checks for the exact head and a matching tree on `main`. The workflow builds from that immutable revision using the current build wrapper and the source tree's builder and configuration. This also supports the historical v0.2.2 tag, which predates the release PR and build wrapper.

Rerun the same version after timeout or interruption to reuse its branch, PR, tag, or draft. Update a stale branch through GitHub. If a different release is pending or the approved source conflicts with current evidence, reconcile it before rerunning. If an existing release has one verified PDF, recovery copies that asset to the missing name. Diverging PDFs or a published release with no surviving PDF require a human repair decision.

If PR creation is denied, a repository owner should check **Settings → Actions → General → Workflow permissions → Allow GitHub Actions to create and approve pull requests**. The workflow requests only the permissions needed to prepare the PR, read checks, and publish after a human merge.

Paper Publication Integrity verifies the published tag, changelog, release metadata, and public downloads independently of historical check runs. A merged release awaiting publication is reported as pending after the existing publication is verified.

## Inputs

- `scripts/plop.paper.config.yaml` — build config (strict)
- `scripts/template.tex` — LaTeX template
- `scripts/paper_release.py` — version validation, changelog promotion, and shared release logic (historical compatibility path)
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
