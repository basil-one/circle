#!/usr/bin/env python3
"""
Circle3 Single Pattern PDF Builder

Generates a focused, publication-ready PDF for a single pattern:
- Framing page with 3 images and abstract
- Pattern content (cleaned)
- Learn More conclusion page

Uses custom LaTeX template for professional formatting.
"""

import os
import sys
import re
import shutil
import subprocess
from pathlib import Path
from typing import Dict, Tuple
import logging

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def simple_yaml_load(content: str) -> dict:
    """Simple YAML parser for basic config - handles block scalars."""
    result = {}
    current_level = {-1: result}
    current_list_key = {-1: None}
    
    lines = content.split('\n')
    line_idx = 0
    
    while line_idx < len(lines):
        line = lines[line_idx]
        
        if not line.strip() or line.strip().startswith('#'):
            line_idx += 1
            continue
        
        indent = len(line) - len(line.lstrip())
        content_line = line.strip()
        
        # Clean up levels for this indent
        keys_to_remove = [k for k in current_level if k >= indent and k != -1]
        for k in keys_to_remove:
            del current_level[k]
            if k in current_list_key:
                del current_list_key[k]
        
        parent_indent = max([k for k in current_level if k < indent] + [-1])
        current_dict = current_level[parent_indent]
        
        # Handle list items
        if content_line.startswith('- '):
            list_key = current_list_key.get(parent_indent)
            if list_key and list_key in current_dict:
                if not isinstance(current_dict[list_key], list):
                    current_dict[list_key] = []
                item = content_line[2:].strip()
                if (item.startswith('"') and item.endswith('"')) or (item.startswith("'") and item.endswith("'")):
                    item = item[1:-1]
                current_dict[list_key].append(item)
            line_idx += 1
            continue
        
        # Handle key: value pairs
        if ':' in content_line:
            key, value = content_line.split(':', 1)
            key = key.strip()
            value = value.strip()
            
            # Check if this is a block scalar
            is_block_scalar = value in ('|', '|-', '|+', '>', '>-', '>+')
            
            if is_block_scalar:
                # Collect block scalar content
                block_lines = []
                block_indent = None
                line_idx += 1
                
                while line_idx < len(lines):
                    next_line = lines[line_idx]
                    next_indent = len(next_line) - len(next_line.lstrip())
                    
                    if next_line.strip() == '':
                        # Preserve blank lines inside the block scalar.
                        block_lines.append('')
                        line_idx += 1
                        continue
                    
                    if block_indent is None:
                        block_indent = next_indent
                    
                    if next_indent < block_indent:
                        # End of block
                        break
                    
                    # Add content with indent removed
                    if next_indent >= block_indent:
                        block_lines.append(next_line[block_indent:])
                    else:
                        block_lines.append(next_line)
                    
                    line_idx += 1
                
                # Remove trailing empty lines
                while block_lines and not block_lines[-1].strip():
                    block_lines.pop()
                
                current_dict[key] = '\n'.join(block_lines)
                # Note: line_idx is already at the next line, don't increment
                
            elif not value:
                # Look ahead to see if this is a list or nested dict
                is_list = False
                for future_idx in range(line_idx + 1, min(line_idx + 20, len(lines))):
                    future_line = lines[future_idx]
                    if not future_line.strip() or future_line.strip().startswith('#'):
                        continue
                    future_indent = len(future_line) - len(future_line.lstrip())
                    if future_indent > indent:
                        if future_line.strip().startswith('- '):
                            is_list = True
                        break
                
                if is_list:
                    current_dict[key] = []
                    current_list_key[indent] = key
                    current_level[indent] = current_dict
                else:
                    new_dict = {}
                    current_dict[key] = new_dict
                    current_level[indent] = new_dict
                
                line_idx += 1
                
            else:
                # Simple value
                if value.startswith('"') and value.endswith('"'):
                    current_dict[key] = value[1:-1]
                elif value.startswith("'") and value.endswith("'"):
                    current_dict[key] = value[1:-1]
                else:
                    current_dict[key] = value
                
                line_idx += 1
        else:
            line_idx += 1
    
    return result


class CirclePDFBuilder:
    """Build focused single-pattern PDF for Circle3."""
    
    def __init__(self, config_path: str, workspace_root: str = None):
        self.config_path = Path(config_path)
        self.root_dir = Path(workspace_root) if workspace_root else self.config_path.parent.parent
        
        logger.info(f"Workspace root: {self.root_dir}")
        
        self.config = self._load_config()
    
    def _load_config(self) -> dict:
        """Load configuration from YAML file."""
        try:
            with open(self.config_path, 'r') as f:
                content = f.read()
            config = simple_yaml_load(content)
            logger.info("Configuration loaded successfully")
            return config
        except Exception as e:
            logger.error(f"Error loading config: {e}")
            sys.exit(1)

    def _check_dependencies(self) -> bool:
        """Check required external system dependencies."""
        if shutil.which('pandoc') is None:
            logger.error("Pandoc is not installed. Install with: brew install pandoc (macOS) or apt install pandoc (Linux)")
            return False

        pdf_engine = 'xelatex'
        if shutil.which(pdf_engine) is None:
            logger.warning(f"PDF engine '{pdf_engine}' is not installed. Pandoc may still run, but PDF generation can fail.")

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
        """Remove Jekyll-specific syntax."""
        content = re.sub(r'\s*\{:\s*[^}]*\}\s*\n?', '', content)
        content = re.sub(r'\s*\{:\s*[^}]*\}', '', content)
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
        
        # Remove "Explore in Your Context" section and everything after (case-insensitive)
        explore_pattern = r'\n##\s+Explore\s+in\s+[Yy]our\s+Context.*'
        content = re.sub(explore_pattern, '', content, flags=re.DOTALL)
        
        # Clean up Jekyll syntax
        content = self._filter_jekyll_syntax(content)

        # Normalize any relative links to absolute URLs
        content = self._normalize_relative_links(content)
        
        # Remove extra blank lines (more than 2 in a row)
        content = re.sub(r'\n\n\n+', '\n\n', content)
        
        return content.strip()
    
    def _read_pattern_file(self) -> Tuple[Dict, str]:
        """Read and clean the pattern file."""
        pattern_file = self.config.get('pattern_file', 'moves/establish.md')
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
            logger.error(f"Error reading pattern file: {e}")
            sys.exit(1)
    
    def _build_markdown_for_pandoc(self, pattern_content: str) -> str:
        """
        Build the markdown content for pandoc.
        Pandoc will use the LaTeX template which handles the framing and learn more pages.
        """
        # Just return the pattern content - the template handles framing and conclusion
        return pattern_content
    
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
    
    def _call_pandoc(self, markdown_content: str, output_path: Path) -> bool:
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
            conclusion = self.config.get('conclusion', {})
            
            # Convert markdown formatting to LaTeX in config values
            framing_abstract = self._markdown_to_latex(framing.get('abstract', ''))
            conclusion_main = self._markdown_to_latex(conclusion.get('main_text', ''))
            conclusion_details = self._markdown_to_latex(conclusion.get('details', ''))
            
            args = [
                'pandoc',
                '--from', 'markdown',
                '--to', 'pdf',
                '--template', str(template_path),
                '--pdf-engine', 'xelatex',
                '-V', 'title=' + title,
                '-V', 'author=' + author,
                '-V', 'date=' + date,
                '-V', 'framing_title=' + framing.get('title', 'Document'),
                '-V', 'framing_subtitle=' + framing.get('subtitle', ''),
                '-V', 'framing_abstract=' + framing_abstract,
                '-V', 'framing_image_1=' + (framing.get('images', [])[0] if len(framing.get('images', [])) > 0 else ''),
                '-V', 'framing_image_2=' + (framing.get('images', [])[1] if len(framing.get('images', [])) > 1 else ''),
                '-V', 'framing_image_3=' + (framing.get('images', [])[2] if len(framing.get('images', [])) > 2 else ''),
                '-V', 'conclusion_image=' + conclusion.get('image', ''),
                '-V', 'conclusion_main=' + conclusion_main,
                '-V', 'conclusion_details=' + conclusion_details,
                '-V', 'conclusion_cta=' + conclusion.get('cta_text', ''),
                '-V', 'conclusion_url=' + conclusion.get('cta_url', ''),
                '-V', 'conclusion_label=' + conclusion.get('cta_label', ''),
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
                capture_output=True
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
        logger.info("Starting single-pattern PDF build pipeline")
        
        # Step 1: Read and clean pattern
        logger.info("Step 1: Reading pattern content...")
        frontmatter, pattern_content = self._read_pattern_file()
        logger.info(f"Pattern title: {frontmatter.get('title', 'Unknown')}")
        
        # Step 2: Build markdown for pandoc
        logger.info("Step 2: Preparing content...")
        if not self._check_dependencies():
            logger.error("Build terminated due to missing external dependency.")
            return False

        markdown_content = self._build_markdown_for_pandoc(pattern_content)
        
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
        output_filename = self.config['output']['filename']
        output_dir = self.config['output'].get('directory', './')
        output_path = Path(output_dir) / output_filename
        
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        success = self._call_pandoc(markdown_content, output_path)
        
        if success:
            file_size = output_path.stat().st_size / (1024 * 1024)
            logger.info(f"\n✓ PDF build complete! File size: {file_size:.2f} MB")
            logger.info(f"✓ Location: {output_path.resolve()}")
        else:
            logger.error("\n✗ PDF build failed")
        
        return success


def main():
    """Main entry point."""
    script_dir = Path(__file__).parent
    config_path = script_dir / 'config.yaml'
    workspace_root = script_dir.parent
    
    builder = CirclePDFBuilder(config_path, workspace_root)
    success = builder.build()
    
    sys.exit(0 if success else 1)


if __name__ == '__main__':
    main()
