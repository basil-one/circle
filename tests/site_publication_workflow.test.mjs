import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

const workflow = await readFile(
  new URL('../.github/workflows/site-publish.yml', import.meta.url),
  'utf8'
);

test('Site: Publish only builds and deploys the main branch', () => {
  assert.match(workflow, /^name: "Site: Publish"$/m);
  assert.match(workflow, /^on:\n  push:\n    branches: \[main\]\n  workflow_dispatch:$/m);
  assert.doesNotMatch(workflow, /^  pull_request:/m);
  assert.match(workflow, /^    if: github\.ref == 'refs\/heads\/main'$/m);
  assert.match(
    workflow,
    /^    if: github\.ref == 'refs\/heads\/main' && needs\.build\.outputs\.source_sha == github\.sha$/m
  );
});

test('Site: Publish builds the production site with the frozen Site Quality toolchain', () => {
  assert.match(workflow, /^permissions:\n  contents: read$/m);
  assert.match(workflow, /uses: actions\/checkout@v5\n        with:\n          ref: \$\{\{ github\.sha \}\}\n          persist-credentials: false/);
  assert.match(workflow, /uses: ruby\/setup-ruby@v1\n        with:\n          ruby-version: '3\.3'\n          bundler-cache: true\n        env:\n          BUNDLE_FROZEN: 'true'/);
  assert.match(workflow, /run: bundle exec jekyll build --trace\n        env:\n          JEKYLL_ENV: production/);
  assert.match(workflow, /test -s _site\/CNAME/);
  assert.match(
    workflow,
    /source_sha="\$\(git rev-parse HEAD\)"\n          test "\$source_sha" = "\$GITHUB_SHA"\n          echo "sha=\$source_sha" >> "\$GITHUB_OUTPUT"\n          echo "Built source SHA: \$source_sha" >> "\$GITHUB_STEP_SUMMARY"/
  );
});

test('Site: Publish deploys only its generated Pages artifact with scoped permissions', () => {
  assert.match(
    workflow,
    /uses: actions\/upload-pages-artifact@v5\n        with:\n          name: github-pages\n          path: _site/
  );
  assert.match(workflow, /needs: build\n    permissions:\n      contents: read\n      pages: write\n      id-token: write/);
  assert.match(workflow, /environment:\n      name: github-pages/);
  assert.match(workflow, /uses: actions\/deploy-pages@v5\n        with:\n          artifact_name: github-pages/);
  assert.match(workflow, /group: github-pages\n  cancel-in-progress: false/);
  assert.match(workflow, /gh api "repos\/\$GITHUB_REPOSITORY\/commits\/main" --jq \.sha/);
});
