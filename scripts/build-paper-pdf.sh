#!/usr/bin/env bash
set -euo pipefail

sudo apt-get update
sudo apt-get install -y \
  pandoc \
  texlive-xetex \
  texlive-latex-recommended \
  texlive-latex-extra \
  texlive-fonts-recommended \
  fontconfig \
  fonts-noto-core \
  fonts-noto-ui-core \
  poppler-utils
fc-cache -f -v

rm -f assets/pdfs/circle3-paper.pdf
python3 scripts/build-pdf.py scripts/plop.paper.config.yaml
test -s assets/pdfs/circle3-paper.pdf
pdfinfo -isodates assets/pdfs/circle3-paper.pdf >/dev/null
