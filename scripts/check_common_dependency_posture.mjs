const severityRank = { low: 0, medium: 1, high: 2, critical: 3 };

function escapeMarkdown(value) {
  return String(value ?? 'unknown').replace(/[|`<>]/g, '\\$&');
}

function sanitizeDiagnostic(value, token) {
  if (typeof value !== 'string') return '';
  return value
    .replaceAll(token, '[REDACTED]')
    .replace(/[\u0000-\u001f\u007f-\u009f]/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
    .slice(0, 500)
    .replace(/[\\|`<>[\]{}*_!#]/g, '\\$&');
}

async function getApiErrorDetails(response, token) {
  let body = {};
  try {
    body = await response.json();
  } catch {
    body = {};
  }
  const message = sanitizeDiagnostic(body?.message, token);
  const rawDocumentationUrl = body?.documentation_url;
  let documentationUrl = '';
  if (typeof rawDocumentationUrl === 'string') {
    try {
      const url = new URL(rawDocumentationUrl);
      if (
        url.protocol === 'https:' &&
        ['docs.github.com', 'github.com'].includes(url.hostname)
      ) {
        documentationUrl = `${url.origin}${url.pathname}`;
      }
    } catch {
      documentationUrl = '';
    }
  }
  const requestId = response.headers?.get?.('x-github-request-id') ?? '';
  const safeRequestId = /^[a-zA-Z0-9-]{1,100}$/.test(requestId)
    ? requestId
    : '';
  return { message, documentationUrl, requestId: safeRequestId };
}

function splitLinkHeader(header) {
  const links = [];
  let start = 0;
  let inTarget = false;
  let inQuote = false;
  for (let index = 0; index < header.length; index += 1) {
    const character = header[index];
    if (character === '"' && !inTarget) inQuote = !inQuote;
    if (character === '<' && !inQuote) inTarget = true;
    if (character === '>' && !inQuote) inTarget = false;
    if (character === ',' && !inTarget && !inQuote) {
      links.push(header.slice(start, index).trim());
      start = index + 1;
    }
  }
  links.push(header.slice(start).trim());
  return links;
}

function getNextCursor(response, apiUrl, repository, seenCursors) {
  const header = response.headers?.get?.('link');
  if (!header) return { cursor: null };

  const expectedPath = new URL(
    `/repos/${repository}/dependabot/alerts`,
    apiUrl
  ).pathname;
  let nextCursor = null;
  for (const link of splitLinkHeader(header)) {
    const relation = link.match(/;\s*rel\s*=\s*(?:"([^"]*)"|([^;\s]+))/i);
    const relationValue = relation?.[1] ?? relation?.[2] ?? '';
    const isNext = relationValue.split(/\s+/).includes('next');
    const hasNextMarker = /\brel\s*=\s*"?next(?:[";\s]|$)/i.test(link);
    const beginsWithTarget = link.match(/^<([^<>]*)>(.*)$/);
    if (!isNext) {
      if (hasNextMarker) {
        return { error: 'Dependabot alerts API returned a malformed next-page link' };
      }
      continue;
    }
    if (nextCursor !== null || !beginsWithTarget) {
      return { error: 'Dependabot alerts API returned a malformed next-page link' };
    }
    let url;
    try {
      url = new URL(beginsWithTarget[1], apiUrl);
    } catch {
      return { error: 'Dependabot alerts API returned a malformed next-page link' };
    }
    const query = url.searchParams;
    const afterValues = query.getAll('after');
    if (
      url.origin !== new URL(apiUrl).origin ||
      url.pathname !== expectedPath ||
      url.username ||
      url.password ||
      url.hash ||
      query.getAll('state').length !== 1 ||
      query.get('state') !== 'open' ||
      query.getAll('per_page').length !== 1 ||
      query.get('per_page') !== '100' ||
      afterValues.length !== 1 ||
      !afterValues[0] ||
      afterValues[0].length > 2048 ||
      query.has('page') ||
      query.has('before') ||
      [...query.keys()].some((key) => !['state', 'per_page', 'after'].includes(key))
    ) {
      return { error: 'Dependabot alerts API returned an invalid next-page cursor' };
    }
    nextCursor = afterValues[0];
  }
  if (nextCursor !== null && seenCursors.has(nextCursor)) {
    return { error: 'Dependabot alerts API returned a repeated next-page cursor' };
  }
  return { cursor: nextCursor };
}

async function checkDependencyPosture({
  apiUrl,
  repository,
  token,
  fetchImpl = fetch
}) {
  const lines = ['# Dependency Security', '', '## Outcome', ''];
  if (!token || !/^[^/]+\/[^/]+$/.test(repository ?? '')) {
    lines.push('**Unknown — GitHub API credentials or repository are unavailable.**');
    return {
      status: 'unknown',
      alerts: [],
      detail: 'GitHub API credentials or repository are unavailable',
      markdown: lines.join('\n')
    };
  }

  const alerts = [];
  const seenCursors = new Set();
  let cursor = null;
  try {
    for (let requestCount = 1; requestCount <= 100; requestCount += 1) {
      const url = new URL(
        `/repos/${repository}/dependabot/alerts`,
        apiUrl
      );
      const query = new URLSearchParams({
        state: 'open',
        per_page: '100'
      });
      if (cursor !== null) {
        query.set('after', cursor);
        seenCursors.add(cursor);
      }
      url.search = query;
      const response = await fetchImpl(url, {
        headers: {
          Accept: 'application/vnd.github+json',
          Authorization: ['Bearer', token].join(' '),
          'X-GitHub-Api-Version': '2022-11-28'
        },
        signal: AbortSignal.timeout(15_000)
      });
      if (!response.ok) {
        const errorDetails = await getApiErrorDetails(response, token);
        const detail = `GitHub Dependabot alerts API returned HTTP ${response.status}${errorDetails.message ? `: ${errorDetails.message}` : ''}`;
        lines.push(`**Unknown — ${detail}.**`);
        if (errorDetails.documentationUrl) {
          lines.push(`- Documentation: ${errorDetails.documentationUrl}`);
        }
        if (errorDetails.requestId) {
          lines.push(`- GitHub request ID: ${errorDetails.requestId}.`);
        }
        return {
          status: 'unknown',
          alerts: [],
          detail: [
            detail,
            errorDetails.documentationUrl
              ? `Documentation: ${errorDetails.documentationUrl}`
              : '',
            errorDetails.requestId
              ? `GitHub request ID: ${errorDetails.requestId}`
              : ''
          ].filter(Boolean).join('\n'),
          markdown: lines.join('\n')
        };
      }
      const pageAlerts = await response.json();
      if (!Array.isArray(pageAlerts)) {
        lines.push('**Unknown — GitHub returned an unreadable Dependabot alert response.**');
        return {
          status: 'unknown',
          alerts: [],
          detail: 'GitHub returned an unreadable Dependabot alert response',
          markdown: lines.join('\n')
        };
      }
      alerts.push(...pageAlerts);
      const next = getNextCursor(response, apiUrl, repository, seenCursors);
      if (next.error) {
        lines.push(`**Unknown — ${next.error}.**`);
        return {
          status: 'unknown',
          alerts: [],
          detail: next.error,
          markdown: lines.join('\n')
        };
      }
      if (next.cursor === null) break;
      if (requestCount === 100) {
        lines.push('**Unknown — alert pagination exceeded the 100-request safety limit.**');
        return {
          status: 'unknown',
          alerts: [],
          detail: 'Alert pagination exceeded the 100-request safety limit',
          markdown: lines.join('\n')
        };
      }
      cursor = next.cursor;
    }
  } catch (error) {
    const reason = error?.name === 'TimeoutError' ? 'timed out' : 'could not be reached';
    lines.push(`**Unknown — GitHub Dependabot alerts API ${reason}.**`);
    return {
      status: 'unknown',
      alerts: [],
      detail: `GitHub Dependabot alerts API ${reason}`,
      markdown: lines.join('\n')
    };
  }

  const reviewAlerts = alerts.filter((alert) => {
    const severity = alert.security_advisory?.severity;
    return severityRank[severity] === undefined || severityRank[severity] >= 1;
  });
  const lowAlerts = alerts.filter(
    (alert) => alert.security_advisory?.severity === 'low'
  );
  const status = reviewAlerts.length ? 'review' : 'passed';
  const detail = status === 'passed'
    ? 'No open medium-or-higher Dependabot alerts'
    : `${reviewAlerts.length} open alert(s) are medium-or-higher or have unknown severity`;
  lines.push(
    status === 'passed'
      ? '**Posture checked — no open medium-or-higher Dependabot alerts.**'
      : `**Review needed — ${reviewAlerts.length} open alert(s) are medium-or-higher or have unknown severity.**`,
    '',
    `- Open alerts checked across enabled dependency ecosystems: ${alerts.length}.`,
    `- Open low-severity alerts: ${lowAlerts.length}.`
  );
  for (const alert of reviewAlerts.slice(0, 20)) {
    const packageName = escapeMarkdown(alert.dependency?.package?.name);
    const severity = escapeMarkdown(alert.security_advisory?.severity);
    const manifest = escapeMarkdown(alert.dependency?.manifest_path);
    const link = Number.isInteger(alert.number)
      ? ` [alert ${alert.number}](${`https://github.com/${repository}/security/dependabot/${alert.number}`})`
      : '';
    lines.push(`- ${severity}: \`${packageName}\` in \`${manifest}\`${link}.`);
  }
  if (reviewAlerts.length > 20) {
    lines.push(`- ${reviewAlerts.length - 20} additional alerts omitted; review the repository's Dependabot alerts.`);
  }
  lines.push(
    '',
    'This daily repository posture uses open Dependabot alerts across enabled ecosystems; the PR check separately reviews only the proposed dependency diff.'
  );
  return {
    status,
    alerts,
    reviewAlerts: reviewAlerts.length,
    lowAlerts: lowAlerts.length,
    detail,
    markdown: lines.join('\n')
  };
}

async function main() {
  const result = await checkDependencyPosture({
    apiUrl: process.env.GITHUB_API_URL ?? 'https://api.github.com',
    repository: process.env.GITHUB_REPOSITORY,
    token: process.env.GITHUB_TOKEN
  });
  process.stdout.write(`${result.markdown}\n`);
  if (process.env.GITHUB_STEP_SUMMARY) {
    const { appendFileSync } = await import('node:fs');
    appendFileSync(process.env.GITHUB_STEP_SUMMARY, `${result.markdown}\n`);
  }
  if (process.env.DEPENDENCY_POSTURE_FILE) {
    const { writeFileSync } = await import('node:fs');
    writeFileSync(
      process.env.DEPENDENCY_POSTURE_FILE,
      `${JSON.stringify({
        status: result.status,
        detail: result.detail,
        openAlerts: result.alerts.length,
        reviewAlerts: result.reviewAlerts ?? 0,
        lowAlerts: result.lowAlerts ?? 0
      }, null, 2)}\n`
    );
  }
  if (result.status !== 'passed') process.exitCode = 1;
}

if (import.meta.url === `file://${process.argv[1]}`) {
  await main();
}

export { checkDependencyPosture };
