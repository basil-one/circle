import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

const workflows = new URL('../../.github/workflows/', import.meta.url);

test('Release Publish runs only on a serialized manual main dispatch', async () => {
  const yaml = await readFile(new URL('release-publish.yml', workflows), 'utf8');
  assert.match(yaml, /^name: Release Publish$/m);
  assert.deepEqual(
    [...yaml.split('\npermissions:')[0].matchAll(/^  (\w+):$/gm)].map(([, trigger]) => trigger),
    ['workflow_dispatch']
  );
  assert.match(yaml, /permissions:\n  checks: read\n  contents: write\n  pull-requests: write\n\n/);
  assert.match(yaml, /group: paper-publication\n  cancel-in-progress: false/);
  assert.match(yaml, /jobs:\n  release-publish:\n    name: Release Publish\n    if: github.ref == 'refs\/heads\/main'/);
  assert.match(yaml, /fetch-depth: 0/);
  assert.match(yaml, /GH_TOKEN: \$\{\{ github.token \}\}/);
  assert.match(yaml, /python3 -m scripts\.orchestration\.release_publish --version "\$VERSION"/);
  const quality = await readFile(new URL('common-code-quality.yml', workflows), 'utf8');
  assert.match(quality, /unittest discover -s tests\/orchestration -p 'test_release_publish\.py'/);
});
