# Circle3 PDF Publication Pipeline (Multi-Pattern)

A configurable PDF publication pipeline for creating a single, conference-ready PDF containing multiple Circle3 patterns (with the same existing framing + conclusion styling).

## Quick Start

```bash
cd /workspaces/circle
python3 scripts/build-pdf.py
```

This generates `circle-paper.pdf` based on configuration in `scripts/config.yaml`.

## Architecture

### Files

- **`scripts/build-pdf.py`** — Build orchestrator
  - Loads a **patterns list** from config (data-driven)
  - Inserts a full-page move image before each pattern
  - Cleans pattern content dynamically (removes subtitle, images, trailing sections)
  - Filters Jekyll syntax
  - Passes combined markdown + config to pandoc

- **`scripts/template.tex`** — Custom LaTeX template
  - Uses config variables for all text content (framing abstract, conclusion text, CTAs)
  - Uses config variables for all image paths
  - 3-page structure: Framing → Content → Conclusion (title/anchor configured in `config.yaml`)
  - Optimized spacing and typography for readability

- **`scripts/config.yaml`** — Configuration
  - Framing + conclusion content
  - Metadata + output settings
  - Data-driven `patterns:` list (multi-pattern)

### Content Pipeline

```
config.yaml
           ↓
      build-pdf.py
           ↓
      establish.md / balance.md / reconcile.md → [Clean: remove subtitle, images, explore section, Jekyll syntax]
           ↓
      template.tex [Use config variables to populate all content]
           ↓
      pandoc + xelatex
           ↓
      circle-paper.pdf
```

## Configuration Structure

Edit `scripts/config.yaml` to customize (notably the `patterns:` list):

```yaml
output:
  filename: circle-paper.pdf      # Output PDF filename
  directory: ./                    # Output directory

metadata:
  title: Circle3 - Establish the Circle    # PDF title
  author: Michael Basil                     # PDF author
  date: June 2026                           # Publication date

patterns:                         # Patterns (in order)
  - file: moves/establish.md
    intro_image: images/full/move-establish.png
  - file: moves/balance.md
    intro_image: images/full/move-balance.png
  - file: moves/reconcile.md
    intro_image: images/full/move-reconcile.png

framing:
  images:                           # 3 images for cascade layout (2.8in width)
    - images/full/index.png
    - images/full/method.png
    - images/full/move-establish.png
  abstract_label: Abstract           # Required
  toc_label: Table of Contents       # Required
  abstract: |                        # Framing page abstract text
    Circle3 is a pattern language...

conclusion:
  title: Learn More                 # Required page title (used in TOC + page heading)
  anchor: learn-more                # Required (used for internal PDF links)
  image: images/full/origin.png     # Conclusion page image (3.0in width)
  main_text: |                      # Main paragraph
    Establish the Circle is the signature move...
  details: |                        # Details section
    The full Circle3 language includes...
  cta_text: "Explore the full pattern language:"
  cta_url: https://circle.basil.one
  cta_label: circle.basil.one
```

## Using with Other Patterns

Edit `scripts/config.yaml` and update the `patterns:` list (order, files, and per-pattern intro images). Then rebuild:

```bash
python3 scripts/build-pdf.py
```

## Content Cleaning

The build script automatically:
- ✓ Removes the "A Pattern for Adaptive Change Leadership by..." subtitle
- ✓ Removes pattern image references
- ✓ Removes the entire "Explore in Your Context" section
- ✓ Filters Jekyll kramdown attributes (`{: data-ga-event="..." }`)
- ✓ Cleans excess whitespace

**No manual editing required** — source markdown files are left untouched.

## Design Notes

### Page 1: Framing Page
- **Title**: "Circle3" + "A Pattern Language for..."
- **Visual**: 3-image cascade (2.8in × 2.8in each, staggered horizontally)
- **Abstract**: Pulls from `framing.abstract` in config
- Introduces the pattern and context

### Page 2+: Pattern Content
- Pattern title + "For leaders, facilitators..." context
- Standard sections: Summary, Story, Context, Problem, Forces, Solution, etc.
- Professional typography with optimized spacing
- Tight list formatting for readability

### Last Page: Conclusion
- **Title**: From `conclusion.title`
- **Image**: Single large image (3.0in, centered) from `conclusion.image`
- **Main text**: From `conclusion.main_text`
- **Details list**: From `conclusion.details`
- **CTA**: Call-to-action with hyperlinked URL

## Customization

### Typography Adjustments

Edit `scripts/template.tex`:

```latex
% Change font size
\documentclass[12pt]{article}  # Change 11pt to 12pt

% Adjust margins
\usepackage[margin=0.5in]{geometry}  # Change margin values

% Adjust heading spacing
\titlespacing*{\section}{0pt}{8pt}{4pt}  # Adjust values
```

### Image Sizing

Edit `scripts/template.tex`:

```latex
% Framing page cascade images
\includegraphics[width=2.8in]{...}  # Change 2.8in to desired width

% Conclusion page image
\includegraphics[width=3.0in]{...}  # Change 3.0in to desired width
```

### List Spacing

Edit `scripts/template.tex`:

```latex
% Tighter list spacing
\setlist[itemize]{topsep=2pt, itemsep=2pt, parsep=0pt}
```

## CI/CD Ready

This pipeline is designed for GitHub Actions integration:
- Single command: `python3 scripts/build-pdf.py`
- No external dependencies beyond pandoc (pre-installed)
- Config-driven (no code changes needed for new patterns)
- Automatic PDF generation on config/pattern changes

## Dependencies

- **Python 3.13+** (stdlib only, no pip packages)
- **Pandoc 3.1+** (for markdown → PDF conversion)
- **texlive-xetex** + **fonts-noto** (for LaTeX rendering with Unicode/emoji support)

All included in the dev container.

## Output

- **File**: `circle-paper.pdf`
- **Size**: ~8-9 MB per pattern
- **Format**: Professional PDF with:
  - Hyperlinked content
  - Unicode/emoji rendering
  - Print-ready styling

---

**Status**: Production ready  
**Last Updated**: June 1, 2026  
**Tested With**: Python 3.13.5, Pandoc 3.1.11.1, xelatex
