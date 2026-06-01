# Circle3 Single-Pattern PDF Publication Pipeline

A fully configurable, pattern-agnostic PDF publication pipeline for creating focused, conference-ready single-pattern papers.

## Quick Start

```bash
cd /workspaces/circle
python3 scripts/build-pdf.py
```

This generates `circle-paper.pdf` based on configuration in `scripts/config.yaml`.

## Architecture

### Files

- **`scripts/build-pdf.py`** — Build orchestrator
  - Loads pattern file (configurable)
  - Cleans content dynamically (removes subtitle, images, trailing sections)
  - Filters Jekyll syntax
  - Passes cleaned content + config to pandoc

- **`scripts/template.tex`** — Custom LaTeX template
  - Uses config variables for all text content (framing abstract, conclusion text, CTAs)
  - Uses config variables for all image paths
  - 3-page structure: Framing → Content → Learn More
  - Optimized spacing and typography for readability

- **`scripts/config.yaml`** — Complete configuration
  - No hardcoded strings in template or script
  - All content, images, metadata configurable
  - Designed to work for any Circle3 pattern

### Content Pipeline

```
config.yaml (text, images, metadata)
           ↓
      build-pdf.py
           ↓
      establish.md → [Clean: remove subtitle, images, explore section, Jekyll syntax]
           ↓
      template.tex [Use config variables to populate all content]
           ↓
      pandoc + xelatex
           ↓
      circle-paper.pdf
```

## Configuration Structure

Edit `scripts/config.yaml` to customize:

```yaml
output:
  filename: circle-paper.pdf      # Output PDF filename
  directory: ./                    # Output directory

metadata:
  title: Circle3 - Establish the Circle    # PDF title
  author: Michael Basil                     # PDF author
  date: June 2026                           # Publication date

pattern_file: moves/establish.md  # Pattern markdown file to process

framing:
  images:                           # 3 images for cascade layout (2.8in width)
    - images/full/index.png
    - images/full/method.png
    - images/full/move-establish.png
  abstract: |                       # Framing page abstract text
    Circle3 is a pattern language...

conclusion:
  image: images/full/origin.png     # Learn More page image (3.0in width)
  main_text: |                      # Main paragraph
    Establish the Circle is the signature move...
  details: |                        # Details section
    The full Circle3 language includes...
  cta_text: "Explore the full pattern language:"
  cta_url: https://circle.basil.one
  cta_label: circle.basil.one
```

## Using with Other Patterns

To create a PDF for a different Circle3 pattern (e.g., Balance the Conversation):

1. **Update config.yaml:**
   ```yaml
   pattern_file: moves/balance.md
   metadata:
     title: Circle3 - Balance the Conversation
   framing:
     images:
       - images/full/index.png
       - images/full/method.png
       - images/full/move-balance.png
   # Update abstract, conclusion text, etc.
   ```

2. **Rebuild:**
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

### Last Page: Learn More
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

% Learn More page image
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
