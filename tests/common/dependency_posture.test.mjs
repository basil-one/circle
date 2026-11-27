import assert from 'node:assert/strict';
import test from 'node:test';
import { checkDependencyPosture } from '../../scripts/common/dependency_posture.mjs';

const options = {
  apiUrl: 'https://api.github.com',
  repository: 'basil-one/circle',
  token: 'test-token'
};

test('daily dependency posture passes only after readable empty alert response', async () => {
  let requestedUrl;
  const result = await checkDependencyPosture({
    ...options,
    fetchImpl: async (url) => {
      requestedUrl = url;
      return {
        ok: true,
        json: async () => []
      };
    }
  });
  assert.equal(result.status, 'passed');
  assert.match(result.markdown, /no open medium-or-higher Dependabot alerts/);
  assert.equal(requestedUrl.searchParams.has('page'), false);
  assert.equal(requestedUrl.searchParams.has('after'), false);
  assert.equal(requestedUrl.searchParams.get('state'), 'open');
  assert.equal(requestedUrl.searchParams.get('per_page'), '100');
});

test('a short single page reports its actual alert posture', async () => {
  const result = await checkDependencyPosture({
    ...options,
    fetchImpl: async () => ({
      ok: true,
      json: async () => [{
        number: 9,
        dependency: { package: { name: 'single-page-package' } },
        security_advisory: { severity: 'medium' }
      }]
    })
  });
  assert.equal(result.status, 'review');
  assert.equal(result.alerts.length, 1);
});

test('markdown alert cells escape backslashes and pipes', async () => {
  const result = await checkDependencyPosture({
    ...options,
    fetchImpl: async () => ({
      ok: true,
      json: async () => [{
        number: 12,
        dependency: { package: { name: 'pkg\\|injected' } },
        security_advisory: { severity: 'high' }
      }]
    })
  });
  assert.equal(result.status, 'review');
  assert.ok(result.markdown.includes('pkg\\\\\\|injected'));
});

test('follows next-link cursors even when a page is short', async () => {
  const requestedUrls = [];
  const responses = [
    {
      ok: true,
      json: async () => [{
        number: 10,
        dependency: { package: { name: 'first-page-package' } },
        security_advisory: { severity: 'high' }
      }],
      headers: {
        get: (name) => name === 'link'
          ? '<https://api.github.com/repos/basil-one/circle/dependabot/alerts?state=open&per_page=100&after=cursor-1>; rel="next"'
          : null
      }
    },
    {
      ok: true,
      json: async () => [{
        number: 11,
        dependency: { package: { name: 'second-page-package' } },
        security_advisory: { severity: 'low' }
      }]
    }
  ];
  const result = await checkDependencyPosture({
    ...options,
    fetchImpl: async (url) => {
      requestedUrls.push(url);
      return responses.shift();
    }
  });

  assert.equal(result.status, 'review');
  assert.equal(result.alerts.length, 2);
  assert.equal(requestedUrls.length, 2);
  assert.equal(requestedUrls[0].searchParams.has('after'), false);
  assert.equal(requestedUrls[1].searchParams.get('after'), 'cursor-1');
  assert.equal(requestedUrls.every((url) => !url.searchParams.has('page')), true);
});

test('medium-or-higher and unknown severity alerts require review', async () => {
  const result = await checkDependencyPosture({
    ...options,
    fetchImpl: async () => ({
      ok: true,
      json: async () => [
        {
          number: 7,
          dependency: {
            package: { name: 'example-package' },
            manifest_path: 'Gemfile.lock'
          },
          security_advisory: { severity: 'high' }
        }
      ]
    })
  });
  assert.equal(result.status, 'review');
  assert.match(result.markdown, /example-package/);
  assert.match(result.markdown, /alert 7/);
});

test('API permission failures are unknown and never green', async () => {
  const result = await checkDependencyPosture({
    ...options,
    fetchImpl: async () => ({ ok: false, status: 403 })
  });
  assert.equal(result.status, 'unknown');
  assert.match(result.markdown, /Unknown — GitHub Dependabot alerts API returned HTTP 403/);
});

test('API failures report sanitized GitHub diagnostics without exposing response data', async () => {
  let requestedUrl;
  let requestedOptions;
  const result = await checkDependencyPosture({
    ...options,
    fetchImpl: async (url, requestOptions) => {
      requestedUrl = url;
      requestedOptions = requestOptions;
      return {
        ok: false,
        status: 400,
        json: async () => ({
          message: `Invalid request: ${options.token}\nplease retry`,
          documentation_url: 'https://docs.github.com/rest/repos/repos#list-repository-dependabot-alerts?secret=value',
          sensitive_field: 'must not be logged'
        }),
        headers: {
          get: (name) => name === 'x-github-request-id' ? 'ABCD-1234' : null
        }
      };
    }
  });

  assert.equal(result.status, 'unknown');
  assert.match(result.detail, /HTTP 400/);
  assert.ok(result.detail.includes('Invalid request: \\[REDACTED\\] please retry'));
  assert.match(result.detail, /Documentation: https:\/\/docs\.github\.com\/rest\/repos\/repos/);
  assert.match(result.detail, /GitHub request ID: ABCD-1234/);
  assert.doesNotMatch(result.detail, /test-token|sensitive_field|must not be logged|secret=value/);
  assert.equal(requestedUrl.href, 'https://api.github.com/repos/basil-one/circle/dependabot/alerts?state=open&per_page=100');
  assert.equal(requestedOptions.headers.Accept, 'application/vnd.github+json');
  assert.equal(requestedOptions.headers['X-GitHub-Api-Version'], '2022-11-28');
  assert.equal(requestedOptions.headers.Authorization, ['Bearer', 'test-token'].join(' '));
});

test('malformed next links remain unknown', async () => {
  for (const link of [
    'rel="next"',
    '<https://api.github.com/repos/basil-one/circle/dependabot/alerts?state=open&per_page=100&after=cursor>; rel="next'
  ]) {
    const result = await checkDependencyPosture({
      ...options,
      fetchImpl: async () => ({
        ok: true,
        json: async () => [],
        headers: {
          get: (name) => name === 'link' ? link : null
        }
      })
    });
    assert.equal(result.status, 'unknown');
    assert.match(result.detail, /malformed next-page link/);
  }
});

test('invalid next cursors remain unknown', async () => {
  const result = await checkDependencyPosture({
    ...options,
    fetchImpl: async () => ({
      ok: true,
      json: async () => [],
      headers: {
        get: (name) => name === 'link'
          ? '<https://api.github.com/repos/basil-one/circle/dependabot/alerts?state=open&per_page=100>; rel="next"'
          : null
      }
    })
  });
  assert.equal(result.status, 'unknown');
  assert.match(result.detail, /invalid next-page cursor/);
});

test('repeated next cursors remain unknown instead of looping', async () => {
  let requests = 0;
  const result = await checkDependencyPosture({
    ...options,
    fetchImpl: async () => {
      requests += 1;
      return {
        ok: true,
        json: async () => [],
        headers: {
          get: (name) => name === 'link'
            ? '<https://api.github.com/repos/basil-one/circle/dependabot/alerts?state=open&per_page=100&after=repeated>; rel="next"'
            : null
        }
      };
    }
  });
  assert.equal(result.status, 'unknown');
  assert.match(result.detail, /repeated next-page cursor/);
  assert.equal(requests, 2);
});

test('cursor pagination retains the 100-request safety limit', async () => {
  let requests = 0;
  const result = await checkDependencyPosture({
    ...options,
    fetchImpl: async () => {
      requests += 1;
      return {
        ok: true,
        json: async () => [],
        headers: {
          get: (name) => name === 'link'
            ? `<https://api.github.com/repos/basil-one/circle/dependabot/alerts?state=open&per_page=100&after=cursor-${requests}>; rel="next"`
            : null
        }
      };
    }
  });
  assert.equal(result.status, 'unknown');
  assert.match(result.detail, /100-request safety limit/);
  assert.equal(requests, 100);
});
