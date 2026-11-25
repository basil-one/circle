import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import test from 'node:test';
import { classifyChanges } from '../scripts/classify_site_performance_skip.mjs';

function collectionDocument(collection, slug, body = 'Editorial prose.') {
  const route = `/${collection}/${slug}/`;
  return `---
layout: default
title: ${slug}
description: An editorial description.
permalink: ${route}
css_id: ${slug}
---

${body}
`;
}

function commit(cwd, message) {
  execFileSync('git', ['add', '-A'], { cwd });
  execFileSync('git', ['-c', 'user.name=Classifier test', '-c', 'user.email=test@example.com', 'commit', '--allow-empty', '-m', message], {
    cwd,
    stdio: 'ignore'
  });
  return execFileSync('git', ['rev-parse', 'HEAD'], { cwd, encoding: 'utf8' }).trim();
}

function writeFiles(cwd, files) {
  for (const [path, content] of Object.entries(files)) {
    const destination = join(cwd, path);
    mkdirSync(dirname(destination), { recursive: true });
    writeFileSync(destination, content);
  }
}

function scenario({ base = {}, head = {}, intermediate = [], remove = [], rename, eventName = 'pull_request' }) {
  const cwd = mkdtempSync(join(tmpdir(), 'circle-performance-skip-'));
  try {
    execFileSync('git', ['init', '--quiet', '-b', 'main'], { cwd });
    writeFiles(cwd, base);
    const baseSha = commit(cwd, 'base');
    for (const [index, files] of intermediate.entries()) {
      writeFiles(cwd, files);
      commit(cwd, `intermediate ${index + 1}`);
    }
    for (const path of remove) rmSync(join(cwd, path), { force: true });
    if (rename) execFileSync('git', ['mv', rename.from, rename.to], { cwd });
    writeFiles(cwd, head);
    const headSha = commit(cwd, 'head');
    return classifyChanges({ baseSha, headSha, eventName, cwd });
  } finally {
    rmSync(cwd, { recursive: true, force: true });
  }
}

test('skips plain prose edits to existing Circle move and lens pages', () => {
  const result = scenario({
    base: {
      '_moves/establish.md': collectionDocument('moves', 'establish'),
      '_lenses/sense.md': collectionDocument('lenses', 'sense')
    },
    head: {
      '_moves/establish.md': collectionDocument('moves', 'establish', 'Revised editorial prose.'),
      '_lenses/sense.md': collectionDocument('lenses', 'sense', 'Updated lens prose.')
    }
  });
  assert.equal(result.skip, true, result.reason);
  assert.deepEqual(result.paths, ['_lenses/sense.md', '_moves/establish.md']);
});

test('skips title and description metadata edits to existing collection pages', () => {
  const original = collectionDocument('moves', 'establish');
  const updated = original
    .replace('title: establish', 'title: Establish the Circle')
    .replace('description: An editorial description.', 'description: A clearer editorial description.');
  const result = scenario({
    base: { '_moves/establish.md': original },
    head: { '_moves/establish.md': updated }
  });
  assert.equal(result.skip, true, result.reason);
});

test('classifies the complete text-only change range across multiple PR commits', () => {
  const result = scenario({
    base: { '_lenses/sense.md': collectionDocument('lenses', 'sense') },
    intermediate: [{
      '_lenses/sense.md': collectionDocument('lenses', 'sense', 'First prose draft.')
    }],
    head: {
      '_lenses/sense.md': collectionDocument('lenses', 'sense', 'Final prose text.')
    }
  });
  assert.equal(result.skip, true, result.reason);
});

test('runs the full baseline for added pages or mixed collection and site changes', () => {
  assert.equal(scenario({
    head: { '_moves/new-move.md': collectionDocument('moves', 'new-move') }
  }).skip, false);

  for (const path of ['assets/css/site.css', 'scripts/site.js', '_layouts/default.html', '_config.yml']) {
    const result = scenario({
      base: { '_moves/establish.md': collectionDocument('moves', 'establish') },
      head: {
        '_moves/establish.md': collectionDocument('moves', 'establish', 'Updated prose.'),
        [path]: 'site change\n'
      }
    });
    assert.equal(result.skip, false, `${path} must fail open`);
  }
});

test('runs the full baseline for Markdown structure, links, media, HTML, and Liquid changes', () => {
  const unsafeBodies = [
    '# A new heading',
    '- A new list item',
    '**emphasized text**',
    '[move link](/moves/integrate/)',
    '![image](/images/example.png)',
    '[diagram]: https://images.example.test/example.svg',
    '[download](/assets/recording.mp3)',
    '<iframe src="https://example.test/embed"></iframe>',
    '{% include callout.html %}',
    '{{ site.title }}'
  ];
  for (const body of unsafeBodies) {
    const result = scenario({
      base: { '_moves/establish.md': collectionDocument('moves', 'establish') },
      head: { '_moves/establish.md': collectionDocument('moves', 'establish', body) }
    });
    assert.equal(result.skip, false, `${body} must fail open`);
  }
});

test('runs the full baseline for behavior-affecting or unknown front matter edits', () => {
  for (const [before, after] of [
    ['layout: default', 'layout: page'],
    ['permalink: /moves/establish/', 'permalink: /moves/changed/'],
    ['css_id: establish', 'css_id: changed'],
    ['title: establish', 'custom_field: changed']
  ]) {
    const result = scenario({
      base: { '_moves/establish.md': collectionDocument('moves', 'establish') },
      head: {
        '_moves/establish.md': collectionDocument('moves', 'establish').replace(before, after)
      }
    });
    assert.equal(result.skip, false, `${after} must fail open`);
  }
});

test('runs the full baseline for deleted and renamed pages or changed media files', () => {
  const existing = collectionDocument('moves', 'establish');
  assert.equal(scenario({
    base: { '_moves/establish.md': existing },
    remove: ['_moves/establish.md']
  }).skip, false);
  assert.equal(scenario({
    base: { '_moves/establish.md': existing },
    rename: { from: '_moves/establish.md', to: '_moves/renamed.md' }
  }).skip, false);
  assert.equal(scenario({
    base: { '_moves/establish.md': existing },
    head: {
      '_moves/establish.md': collectionDocument('moves', 'establish', 'Updated prose.'),
      'images/photo.png': 'image bytes'
    }
  }).skip, false);
});

test('runs the full baseline when the complete PR diff cannot be determined', () => {
  const result = classifyChanges({
    baseSha: 'a'.repeat(40),
    headSha: 'b'.repeat(40),
    eventName: 'pull_request',
    cwd: tmpdir()
  });
  assert.equal(result.skip, false);
  assert.match(result.reason, /could not determine the complete pull request diff/);
});

test('scheduled and manual runs always perform the full baseline', () => {
  for (const eventName of ['schedule', 'workflow_dispatch']) {
    const result = scenario({
      base: { '_moves/establish.md': collectionDocument('moves', 'establish') },
      head: { '_moves/establish.md': collectionDocument('moves', 'establish', 'Changed prose.') },
      eventName
    });
    assert.equal(result.skip, false);
    assert.match(result.reason, /always perform the full baseline/);
  }
});
