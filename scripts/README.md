# PDF build (Circle3 / PLoP paper)

Build the Circle3 PLoP paper PDF using pandoc + xelatex.

## Run

```bash
python3 scripts/build-pdf.py scripts/plop.paper.config.yaml
```

- Output: `assets/pdfs/circle3-paper.pdf`
- Intermediate markdown (for debugging): `/tmp/plop.paper.config.debug-intermediate.md` (derived from the config filename)

## Inputs

- `scripts/plop.paper.config.yaml` — build config (strict)
- `scripts/template.tex` — LaTeX template
- `scripts/content/*.md` — longform markdown blocks referenced by the config
- `_moves/*.md`, `_lenses/*.md` — pattern sources (web + PDF share these)

## Config shape (top-level)

- `output`: `filename`, `directory`
- `metadata`: `title`, `author`, `date`, `url` (+ optional `email`)
- `framing`: cover/abstract/TOC labels + cover images + `abstract_file`
- `body`: `introduction_file` + `back_matter_file` (+ labels/anchors/include flags)
- `sections`: ordered pattern sections with `intro_image` and `patterns[]` entries
- `conclusion`: final page (image + main text file + CTA)

Inline markdown in YAML is not supported; use `*_file` keys.
