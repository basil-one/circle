#!/usr/bin/env python3
"""
Circle3 PDF Builder (Multi-Pattern)

Generates a publication-ready PDF with the existing Circle3 styling:
- Cover (template-driven)
- Abstract (template-driven)
- Patterns section (data-driven, multiple patterns)
  - A full-page, full-width "move" image before each pattern
  - Cleaned pattern markdown content
- Conclusion (template-driven)

The framing + conclusion sections remain fully template-driven; this script only
builds the *body* content that pandoc injects into the LaTeX template.
"""

import sys
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Tuple, Union
import logging

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def _parse_scalar(value: str) -> str:
    value = value.strip()
    if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
        return value[1:-1]
    return value


def simple_yaml_load(content: str) -> dict:
    """A tiny YAML subset parser.

    Supported:
    - dicts via indentation ("key: value" and "key:" for nested)
    - lists via "- item"
    - lists of dict items via "- key: value" with subsequent indented keys
    - block scalars with "|" / ">" (content captured as a single string)

    This is intentionally limited, but sufficient for the Circle3 PDF configs.
    """

    lines = content.splitlines()

    root: Dict[str, Any] = {}
    # stack entries: (indent, container)
    stack: List[Tuple[int, Union[Dict[str, Any], List[Any]]]] = [(-1, root)]

    def current_container() -> Union[Dict[str, Any], List[Any]]:
        return stack[-1][1]

    def pop_to_indent(target_indent: int) -> None:
        # Pop until the current container's indent is *less than or equal to* the
        # current line. (We only pop when indentation decreases.)
        while stack and stack[-1][0] > target_indent:
            stack.pop()

    def peek_next_content_line(start_idx: int) -> Tuple[int, str, int]:
        """Return (idx, stripped_line, indent) for the next non-empty, non-comment line."""
        i = start_idx
        while i < len(lines):
            raw = lines[i]
            stripped = raw.strip()
            if stripped and not stripped.startswith('#'):
                return i, stripped, (len(raw) - len(raw.lstrip()))
            i += 1
        return -1, '', -1

    i = 0
    while i < len(lines):
        raw = lines[i]
        line = raw.strip()

        if not line or line.startswith('#'):
            i += 1
            continue

        indent = len(raw) - len(raw.lstrip())
        pop_to_indent(indent)
        container = current_container()

        # ------------------------------------------------------------------
        # List item
        # ------------------------------------------------------------------
        if line.startswith('- '):
            if not isinstance(container, list):
                raise ValueError(f"YAML parse error (line {i+1}): list item without list context")

            item_text = line[2:].strip()

            # List item that starts a dict inline: "- key: value"
            if ':' in item_text:
                key, value = item_text.split(':', 1)
                key = key.strip()
                value = value.strip()

                item_dict: Dict[str, Any] = {}
                container.append(item_dict)

                # block scalars inside list items are not needed for this repo right now
                if value in ('|', '|-', '|+', '>', '>-', '>+'):
                    # Collect block scalar content
                    block_lines: List[str] = []
                    block_indent: int | None = None
                    i += 1
                    while i < len(lines):
                        nxt_raw = lines[i]
                        nxt_line = nxt_raw.strip('\n')
                        nxt_stripped = nxt_line.strip()
                        nxt_indent = len(nxt_raw) - len(nxt_raw.lstrip())

                        if nxt_stripped == '':
                            block_lines.append('')
                            i += 1
                            continue

                        if block_indent is None:
                            block_indent = nxt_indent

                        if nxt_indent < (block_indent or 0):
                            break

                        block_lines.append(nxt_raw[block_indent:])
                        i += 1

                    while block_lines and not block_lines[-1].strip():
                        block_lines.pop()

                    item_dict[key] = '\n'.join(block_lines)
                    # don't i += 1 here; loop continues with current i
                    stack.append((indent + 2, item_dict))
                    continue

                item_dict[key] = _parse_scalar(value)
                # Subsequent indented lines belong to this dict item
                stack.append((indent + 2, item_dict))
                i += 1
                continue

            # Simple scalar list item
            container.append(_parse_scalar(item_text))
            i += 1
            continue
                
        # ------------------------------------------------------------------
        # Dict entry
        # ------------------------------------------------------------------
        if ':' not in line:
            raise ValueError(f"YAML parse error (line {i+1}): expected 'key: value' -> {line}")

        if not isinstance(container, dict):
            raise ValueError(f"YAML parse error (line {i+1}): mapping entry inside list without dict item")

        key, value = line.split(':', 1)
        key = key.strip()
        value = value.strip()

        # Block scalar
        if value in ('|', '|-', '|+', '>', '>-', '>+'):
            block_lines: List[str] = []
            block_indent: int | None = None
            i += 1

            while i < len(lines):
                nxt_raw = lines[i]
                nxt_stripped = nxt_raw.strip()
                nxt_indent = len(nxt_raw) - len(nxt_raw.lstrip())

                if nxt_stripped == '':
                    block_lines.append('')
                    i += 1
                    continue

                if block_indent is None:
                    block_indent = nxt_indent

                if nxt_indent < (block_indent or 0):
                    break

                block_lines.append(nxt_raw[block_indent:])
                i += 1

            while block_lines and not block_lines[-1].strip():
                block_lines.pop()

            container[key] = '\n'.join(block_lines)
            continue

        # Nested container
        if value == '':
            next_idx, next_line, next_indent = peek_next_content_line(i + 1)
            if next_idx != -1 and next_indent > indent and next_line.startswith('- '):
                new_list: List[Any] = []
                container[key] = new_list
                stack.append((indent + 2, new_list))
            else:
                new_dict: Dict[str, Any] = {}
                container[key] = new_dict
                stack.append((indent + 2, new_dict))

            i += 1
            continue

        # Simple scalar
        container[key] = _parse_scalar(value)
        i += 1

    return root


def deep_merge_dicts(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """Deep-merge override into base (override wins)."""
    merged: Dict[str, Any] = dict(base)
    for k, v in override.items():
        if k in merged and isinstance(merged[k], dict) and isinstance(v, dict):
            merged[k] = deep_merge_dicts(merged[k], v)
        else:
            merged[k] = v
    return merged


class CirclePDFBuilder:
    """Build Circle3 multi-pattern PDF."""

    def __init__(self, config_path: str, workspace_root: str | None = None):
        self.config_path = Path(config_path)
        self.root_dir = Path(workspace_root) if workspace_root else self.config_path.parent.parent
        
        logger.info(f"Workspace root: {self.root_dir}")
        
        self.config = self._load_config()
    
    def _load_config(self) -> dict:
        """Load configuration (optionally layering on top of a base config)."""
        try:
            with open(self.config_path, 'r', encoding='utf-8') as f:
                content = f.read()
            config = simple_yaml_load(content)

            base_config_rel = config.get('base_config')
            if base_config_rel:
                base_path = (self.config_path.parent / str(base_config_rel)).resolve()
                with open(base_path, 'r', encoding='utf-8') as f:
                    base = simple_yaml_load(f.read())
                config = deep_merge_dicts(base, config)

            logger.info("Configuration loaded successfully")
            return config
        except Exception as e:
            logger.error(f"Error loading config: {e}")
            sys.exit(1)

    def _get_pdf_engine(self) -> str:
        """Return the configured PDF engine (default: xelatex)."""
        output_cfg = self.config.get('output', {})
        if not isinstance(output_cfg, dict):
            output_cfg = {}

        engine = (
            str(self.config.get('pdf_engine') or '')
            or str(output_cfg.get('pdf_engine') or '')
            or 'xelatex'
        )
        return engine

    def _require_config_str(self, section: str, key: str) -> str:
        cfg = self.config.get(section, {})
        if not isinstance(cfg, dict):
            raise ValueError(f"Config section '{section}' must be a mapping/object")

        value = cfg.get(key)
        value = '' if value is None else str(value).strip()
        if not value:
            raise ValueError(f"Missing required config value: {section}.{key}")
        return value

    def _get_framing_labels(self) -> Tuple[str, str]:
        abstract_label = self._require_config_str('framing', 'abstract_label')
        toc_label = self._require_config_str('framing', 'toc_label')
        return abstract_label, toc_label

    def _latex_id(self, raw: str, *, default: str) -> str:
        """Return a LaTeX-safe id for \hypertarget/\label."""
        raw = (raw or '').strip()
        if raw and re.match(r'^[A-Za-z0-9][A-Za-z0-9:-]*$', raw):
            return raw

        slug = self._slugify(raw or default)
        if re.match(r'^\d', slug):
            slug = 'sec-' + slug
        return slug or default

    def _get_conclusion_meta(self) -> Tuple[str, str]:
        title = self._require_config_str('conclusion', 'title')
        raw_anchor = self._require_config_str('conclusion', 'anchor')
        anchor = self._latex_id(raw_anchor, default='conclusion')
        return title, anchor

    def _check_dependencies(self) -> bool:
        """Check required external system dependencies."""
        if shutil.which('pandoc') is None:
            logger.error("Pandoc is not installed. Install with: brew install pandoc (macOS) or apt install pandoc (Linux)")
            return False

        pdf_engine = self._get_pdf_engine()
        if shutil.which(pdf_engine) is None:
            logger.error(
                f"PDF engine '{pdf_engine}' is not installed. Install a TeX distribution "
                "(e.g. texlive-xetex) or configure a different pdf_engine."
            )
            return False

        return True
    
    def _parse_frontmatter(self, content: str) -> Tuple[Dict, str]:
        """Parse YAML frontmatter from markdown."""
        if not content.startswith('---'):
            return {}, content
        
        try:
            parts = content.split('---', 2)
            if len(parts) < 3:
                return {}, content
            
            frontmatter_str = parts[1]
            body = parts[2].strip()
            
            frontmatter = simple_yaml_load(frontmatter_str)
            return frontmatter, body
        except Exception as e:
            logger.warning(f"Error parsing frontmatter: {e}")
            return {}, content
    
    def _filter_jekyll_syntax(self, content: str) -> str:
        """Remove Jekyll/kramdown-specific syntax.

        Important: do *not* delete the line break following an inline kramdown
        attribute ("{: ...}") block; doing so can concatenate a link line with the
        next heading (e.g. "](...)### Heading").
        """
        # Remove inline attribute lists like: {: data-ga-event="..." }
        content = re.sub(r'\s*\{:\s*[^}]*\}', '', content)
        # Trim trailing whitespace that can be left behind after attribute removal.
        content = re.sub(r'[ \t]+$', '', content, flags=re.MULTILINE)
        return content

    def _normalize_relative_links(self, content: str) -> str:
        """Convert relative site links into absolute URLs using root_url."""
        root_url = self.config.get('root_url', '').rstrip('/')
        if not root_url:
            return content

        def replace_link(match):
            text = match.group(1)
            path = match.group(2)
            return f'[{text}]({root_url}{path})'

        return re.sub(r'\[([^\]]+)\]\((/[^)]+)\)', replace_link, content)
    
    def _clean_pattern_content(self, content: str) -> str:
        """
        Clean pattern markdown:
        - Remove "A Pattern for..." subtitle
        - Remove image reference
        - Remove "Explore in Your Context" section and everything after
        - Remove Jekyll syntax
        - Normalize relative links against the configured root URL
        """
        # Remove "A Pattern for..." line with markdown formatting
        # Pattern: *A Pattern for Adaptive Change Leadership by* **Michael Basil**
        content = re.sub(r'\n\*A\s+Pattern\s+for[^*]*\*\s*\*\*[^*]*\*\*\n\n', '\n', content)
        
        # Remove image line: ![text](/path/to/image.png)
        content = re.sub(r'!\[[^\]]*\]\([^)]*\.png\)\s*\n\n', '', content)
        
        # Remove "Explore in your context" section and everything after
        # (handles variations like "Your" vs "your" and "Context" vs "context").
        explore_pattern = r'\n##\s+Explore\s+in\s+your\s+context.*'
        content = re.sub(explore_pattern, '', content, flags=re.DOTALL | re.IGNORECASE)
        
        # Clean up Jekyll syntax
        content = self._filter_jekyll_syntax(content)

        # Normalize any relative links to absolute URLs
        content = self._normalize_relative_links(content)

        # Defensive fix: if a link line got concatenated with a following heading
        # (e.g. "](url)### Heading"), re-insert paragraph breaks.
        content = re.sub(r'\)(?=#{2,6}\s)', ')\n\n', content)

        # Replace unicode "pointing" emoji with a plain markdown bullet so it renders cleanly.
        # (This intentionally turns those lines into list items.)
        content = re.sub(r'(?m)^👉\s*', '- ', content)
        content = content.replace('👉', '-')
        
        # Remove extra blank lines (more than 2 in a row)
        content = re.sub(r'\n\n\n+', '\n\n', content)
        
        return content.strip()
    
    def _read_and_clean_pattern(self, pattern_file: str) -> Tuple[Dict[str, Any], str]:
        """Read a pattern markdown file, parse frontmatter, and clean the body."""
        full_path = self.root_dir / pattern_file
        
        if not full_path.exists():
            logger.error(f"Pattern file not found: {full_path}")
            sys.exit(1)
        
        try:
            with open(full_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            frontmatter, body = self._parse_frontmatter(content)
            body = self._clean_pattern_content(body)
            return frontmatter, body
        except Exception as e:
            logger.error(f"Error reading pattern file '{pattern_file}': {e}")
            sys.exit(1)

    def _get_patterns(self) -> List[Dict[str, str]]:
        """Return ordered patterns configuration.

        Preferred config shape:

        patterns:
          - file: moves/establish.md
            intro_image: images/full/move-establish.png
          - file: moves/balance.md
            intro_image: images/full/move-balance.png
          - file: moves/reconcile.md
            intro_image: images/full/move-reconcile.png

        Backwards-compatibility:
        - If patterns is missing, fall back to `pattern_file`.
        """

        raw_patterns = self.config.get('patterns')
        patterns: List[Dict[str, str]] = []

        if isinstance(raw_patterns, list) and raw_patterns:
            for item in raw_patterns:
                if isinstance(item, str):
                    patterns.append({'file': item})
                elif isinstance(item, dict):
                    # ensure we only carry string-ish values
                    pattern: Dict[str, str] = {}
                    for k, v in item.items():
                        if v is None:
                            continue
                        pattern[str(k)] = str(v)
                    patterns.append(pattern)
                else:
                    raise ValueError(f"Unsupported patterns item: {item}")

        if patterns:
            for p in patterns:
                if 'file' not in p:
                    raise ValueError(f"Each patterns item must include 'file'. Got: {p}")
            return patterns

        pattern_file = self.config.get('pattern_file')
        if pattern_file:
            intro_image = self.config.get('pattern_intro_image', '')
            return [{'file': str(pattern_file), 'intro_image': str(intro_image)}]

        # Final fallback
        return [{'file': 'moves/establish.md'}]

    def _slugify(self, text: str) -> str:
        """Create a stable anchor slug for LaTeX hypertargets."""
        text = (text or '').strip().lower()
        text = re.sub(r'[^a-z0-9]+', '-', text)
        text = re.sub(r'-{2,}', '-', text)
        return text.strip('-') or 'pattern'

    def _escape_latex_text(self, text: str) -> str:
        """Escape a small set of LaTeX special characters for TOC labels."""
        if not text:
            return ''

        # Keep this intentionally small; pattern titles in this repo are usually plain.
        replacements = {
            '\\': r'\textbackslash{}',
            '&': r'\&',
            '%': r'\%',
            '$': r'\$',
            '#': r'\#',
            '_': r'\_',
            '{': r'\{',
            '}': r'\}',
        }
        return ''.join(replacements.get(ch, ch) for ch in str(text))

    def _infer_pattern_title(self, pattern_file: str, frontmatter: Dict[str, Any], body: str) -> str:
        title = str(frontmatter.get('title') or '').strip()
        if title:
            return title

        # Fall back to the first markdown heading in the body.
        for line in body.splitlines():
            m = re.match(r'^#{1,6}\s+(.*)$', line.strip())
            if m:
                return m.group(1).strip()

        return pattern_file

    def _latex_hypertarget_block(self, anchor: str, *, prepend_page_break: bool) -> str:
        lines: List[str] = []
        if prepend_page_break:
            lines.append(r"\newpage")

        # Anchor for hyperlinks + label for page number lookups.
        lines.append(rf"\hypertarget{{{anchor}}}{{}}")
        lines.append(r"\phantomsection")
        lines.append(rf"\label{{{anchor}}}")
        return "```{=latex}\n" + "\n".join(lines) + "\n```"

    def _latex_full_width_image_page(
        self,
        image_path: str,
        *,
        anchor: str | None = None,
        prepend_page_break: bool,
    ) -> str:
        """A full-page image (centered), emitted as a Pandoc raw LaTeX block.

        If `anchor` is provided, we emit a \hypertarget so the short TOC can
        link directly to this intro image page.
        """
        if not image_path:
            return ''

        lines: List[str] = []
        if prepend_page_break:
            lines.append(r"\newpage")

        # Match the template's approach on other image-only pages.
        lines.append(r"\thispagestyle{empty}")
        if anchor:
            # Anchor for hyperlinks + label for page number lookups.
            lines.append(rf"\hypertarget{{{anchor}}}{{}}")
            lines.append(r"\phantomsection")
            lines.append(rf"\label{{{anchor}}}")

        lines.extend(
            [
                r"\vspace*{\fill}",
                r"\begin{center}",
                rf"\includegraphics[width=\textwidth]{{{image_path}}}",
                r"\end{center}",
                r"\vspace*{\fill}",
                r"\newpage",
            ]
        )

        return "```{=latex}\n" + "\n".join(lines) + "\n```"

    def _build_short_toc_latex(self, toc_items: List[Tuple[str, str]]) -> str:
        """Build a very short, flat TOC (patterns + conclusion).

        This is inserted on page 2 (image → abstract → TOC).
        """
        if not toc_items:
            return ''

        lines: List[str] = [
            r"\begingroup",
            r"\large",
            r"\setlength{\tabcolsep}{0pt}",
            r"\renewcommand{\arraystretch}{1.15}",
            r"\begin{center}",
            r"\begin{tabular}{@{}l@{\hspace{2.5em}}r@{}}",
        ]

        for label, anchor in toc_items:
            safe_label = self._escape_latex_text(label)
            lines.append(rf"\hyperlink{{{anchor}}}{{{safe_label}}} & \pageref{{{anchor}}}\\")

        lines.extend(
            [
                r"\end{tabular}",
                r"\end{center}",
                r"\endgroup",
            ]
        )
        return "\n".join(lines)

    def _build_markdown_for_pandoc(self, patterns: List[Dict[str, str]]) -> Tuple[str, List[Tuple[str, str]]]:
        """Build the combined patterns section for pandoc.

        Returns:
          (markdown_body, toc_items)

        toc_items is a list of (label, anchor) pairs linking to each pattern's
        intro-image page, plus the final conclusion page.
        """
        chunks: List[str] = []
        toc_items: List[Tuple[str, str]] = []
        used_anchors: set[str] = set()

        for idx, pattern in enumerate(patterns):
            pattern_file = pattern['file']
            intro_image = pattern.get('intro_image', '').strip()

            frontmatter, body = self._read_and_clean_pattern(pattern_file)
            title = self._infer_pattern_title(pattern_file, frontmatter, body)

            base_anchor = f"pattern-{self._slugify(title)}"
            anchor = base_anchor
            suffix = 2
            while anchor in used_anchors:
                anchor = f"{base_anchor}-{suffix}"
                suffix += 1
            used_anchors.add(anchor)

            logger.info(f"Loaded pattern: {title}")
            toc_items.append((title, anchor))

            if intro_image:
                chunks.append(
                    self._latex_full_width_image_page(
                        intro_image,
                        anchor=anchor,
                        prepend_page_break=(idx > 0),
                    )
                )
            else:
                # If there is no intro image page, still ensure the TOC link has
                # a stable target at the start of the pattern.
                chunks.append(self._latex_hypertarget_block(anchor, prepend_page_break=(idx > 0)))

            chunks.append(body)

        # Add the conclusion as the final entry.
        conclusion_title, conclusion_anchor = self._get_conclusion_meta()
        if conclusion_anchor in used_anchors:
            logger.warning(
                "Conclusion anchor '%s' conflicts with a pattern anchor; consider setting conclusion.anchor in config.",
                conclusion_anchor,
            )
        toc_items.append((conclusion_title, conclusion_anchor))

        markdown_body = "\n\n".join([c for c in chunks if c.strip()]).strip() + "\n"
        return markdown_body, toc_items
    
    def _markdown_to_latex(self, text: str) -> str:
        """Convert markdown formatting to LaTeX, using Pandoc for complex content."""
        text = text.strip()
        if not text:
            return ''
        
        # Check if content contains markdown lists - use Pandoc for proper rendering
        if re.search(r'^\s*([-+*]|\d+\.)\s+', text, re.MULTILINE):
            return self._pandoc_markdown_to_latex(text)
        
        # Simple formatting for non-list content
        paragraphs = []
        for para in re.split(r'\n\s*\n', text):
            if not para.strip():
                continue
            normalized = ' '.join(line.strip() for line in para.splitlines() if line.strip())
            paragraphs.append(normalized)

        text = '\n\n'.join(paragraphs)
        
        # Convert **bold** first, then normal italics.
        text = re.sub(r'\*\*([^*]+?)\*\*', r'\\textbf{\1}', text)
        text = re.sub(r'(?<!\*)\*([^*]+?)\*(?!\*)', r'\\textit{\1}', text)
        return text
    
    def _pandoc_markdown_to_latex(self, markdown_text: str) -> str:
        """Use Pandoc to convert markdown (with lists) to LaTeX inline content."""
        try:
            result = subprocess.run(
                ['pandoc', '-f', 'markdown', '-t', 'latex', '--no-highlight'],
                input=markdown_text.encode('utf-8'),
                capture_output=True,
                timeout=10
            )
            if result.returncode != 0:
                logger.warning(f"Pandoc conversion failed: {result.stderr.decode('utf-8')}")
                return markdown_text
            
            latex_output = result.stdout.decode('utf-8').strip()
            
            # In LaTeX minipages, use explicit line break before lists
            # This preserves the blank line from the markdown without adding extra spacing
            latex_output = re.sub(
                r'([^\n])\n\n+\\begin\{(itemize|enumerate)\}',
                r'\1\n\\\\\n\\begin{\2}',
                latex_output
            )
            
            return latex_output
        except Exception as e:
            logger.warning(f"Error using Pandoc for markdown conversion: {e}")
            return markdown_text
    
    def _call_pandoc(self, markdown_content: str, output_path: Path, *, short_toc: str = '') -> bool:
        """Call pandoc with custom template."""
        try:
            # Get template path
            template_path = self.config_path.parent / 'template.tex'
            
            if not template_path.exists():
                logger.error(f"LaTeX template not found: {template_path}")
                return False
            
            # Prepare pandoc arguments
            metadata = self.config.get('metadata', {})
            title = metadata.get('title', 'Document')
            author = metadata.get('author', 'Author')
            date = metadata.get('date', '')
            
            # Extract framing and conclusion config
            framing = self.config.get('framing', {})
            framing = framing if isinstance(framing, dict) else {}
            conclusion = self.config.get('conclusion', {})
            conclusion = conclusion if isinstance(conclusion, dict) else {}

            framing_abstract_label, framing_toc_label = self._get_framing_labels()
            conclusion_title, conclusion_anchor = self._get_conclusion_meta()
            
            # Convert markdown formatting to LaTeX in config values
            framing_abstract = self._markdown_to_latex(str(framing.get('abstract', '') or ''))
            conclusion_main = self._markdown_to_latex(str(conclusion.get('main_text', '') or ''))
            conclusion_details = self._markdown_to_latex(str(conclusion.get('details', '') or ''))
            
            args = [
                'pandoc',
                '--from', 'markdown',
                '--to', 'pdf',
                '--template', str(template_path),
                '--pdf-engine', self._get_pdf_engine(),
                '-V', 'title=' + str(title),
                '-V', 'author=' + str(author),
                '-V', 'date=' + str(date),
                '-V', 'framing_title=' + str(framing.get('title', 'Document')),
                '-V', 'framing_subtitle=' + str(framing.get('subtitle', '')),
                '-V', 'framing_abstract_label=' + framing_abstract_label,
                '-V', 'framing_toc_label=' + framing_toc_label,
                '-V', 'framing_abstract=' + framing_abstract,
                '-V', 'framing_image_1=' + (framing.get('images', [])[0] if len(framing.get('images', [])) > 0 else ''),
                '-V', 'framing_image_2=' + (framing.get('images', [])[1] if len(framing.get('images', [])) > 1 else ''),
                '-V', 'framing_image_3=' + (framing.get('images', [])[2] if len(framing.get('images', [])) > 2 else ''),
                '-V', 'short_toc=' + (short_toc or ''),
                '-V', 'conclusion_title=' + conclusion_title,
                '-V', 'conclusion_anchor=' + conclusion_anchor,
                '-V', 'conclusion_image=' + str(conclusion.get('image', '')),
                '-V', 'conclusion_main=' + conclusion_main,
                '-V', 'conclusion_details=' + conclusion_details,
                '-V', 'conclusion_cta=' + str(conclusion.get('cta_text', '')),
                '-V', 'conclusion_url=' + str(conclusion.get('cta_url', '')),
                '-V', 'conclusion_label=' + str(conclusion.get('cta_label', '')),
                '--output', str(output_path),
            ]
            
            logger.info(f"Calling pandoc with custom template")
            logger.info(f"Template: {template_path}")
            logger.info(f"Framing title: {framing.get('title', 'MISSING')}")
            logger.info(f"Abstract length: {len(framing_abstract)} chars")
            logger.info(f"Main text length: {len(conclusion_main)} chars")
            logger.info(f"Output file: {output_path}")
            
            # Call pandoc
            result = subprocess.run(
                args,
                input=markdown_content.encode('utf-8'),
                capture_output=True,
                cwd=str(self.root_dir),
            )
            
            if result.returncode != 0:
                logger.error(f"Pandoc error: {result.stderr.decode('utf-8')}")
                return False
            
            logger.info(f"PDF generated successfully: {output_path}")
            return True
            
        except FileNotFoundError:
            logger.error("Pandoc is not installed. Install with: brew install pandoc (macOS) or apt install pandoc (Linux)")
            return False
        except Exception as e:
            logger.error(f"Error calling pandoc: {e}")
            return False
    
    def build(self) -> bool:
        """Execute the PDF building pipeline."""
        logger.info("Starting Circle3 multi-pattern PDF build pipeline")

        # Step 1: Collect patterns
        patterns = self._get_patterns()
        logger.info(f"Patterns: {len(patterns)}")

        # Step 2: Prepare content
        logger.info("Step 2: Preparing content...")
        if not self._check_dependencies():
            logger.error("Build terminated due to missing external dependency.")
            return False

        try:
            markdown_content, toc_items = self._build_markdown_for_pandoc(patterns)
            short_toc = self._build_short_toc_latex(toc_items)
        except ValueError as e:
            logger.error("Configuration error: %s", e)
            return False

        # Optionally save intermediate markdown for inspection
        debug_md_path = self.config_path.parent / 'debug-intermediate.md'
        try:
            with open(debug_md_path, 'w', encoding='utf-8') as f:
                f.write(markdown_content)
            logger.info(f"Intermediate markdown saved for inspection: {debug_md_path}")
        except Exception as e:
            logger.warning(f"Could not save intermediate markdown: {e}")
        
        # Step 3: Generate PDF
        logger.info("Step 3: Generating PDF with custom template...")
        output_cfg = self.config.get('output', {})
        if not isinstance(output_cfg, dict):
            output_cfg = {}

        output_filename = str(output_cfg.get('filename') or 'circle-paper.pdf')
        output_dir = str(output_cfg.get('directory') or './')
        output_path = Path(output_dir) / output_filename
        
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        success = self._call_pandoc(markdown_content, output_path, short_toc=short_toc)
        
        if success:
            file_size = output_path.stat().st_size / (1024 * 1024)
            logger.info(f"\n✓ PDF build complete! File size: {file_size:.2f} MB")
            logger.info(f"✓ Location: {output_path.resolve()}")
        else:
            logger.error("\n✗ PDF build failed")
        
        return success


def main() -> None:
    """Main entry point."""
    script_dir = Path(__file__).parent
    workspace_root = script_dir.parent

    # Allow an explicit config path: python3 scripts/build-pdf.py scripts/config.yaml
    if len(sys.argv) > 1:
        config_path = Path(sys.argv[1])
        if not config_path.is_absolute():
            config_path = (Path.cwd() / config_path).resolve()
    else:
        config_path = script_dir / 'config.yaml'

    builder = CirclePDFBuilder(str(config_path), str(workspace_root))
    success = builder.build()

    raise SystemExit(0 if success else 1)


if __name__ == '__main__':
    main()
