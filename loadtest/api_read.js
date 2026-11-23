// Scenario A (plan.md 7.3): API read load.
//
// 50 virtual users for 10 minutes hitting the status page (`/`) and per-monitor stats
// (`/api/v1/monitors/{id}/stats`). Monitor ids are fetched once in setup().
//
// Run:  k6 run -e BASE_URL=https://pulse-dev.example.com loadtest/api_read.js
// Optional: -e VUS=50 -e DURATION=10m -e MAX_MONITORS=200
//
// Only load-test endpoints you own (Pulse itself and pulse-target).
import http from 'k6/http';
import { check, fail, sleep } from 'k6';

const BASE_URL = (__ENV.BASE_URL || 'http://localhost:8000').replace(/\/+$/, '');
const VUS = parseInt(__ENV.VUS || '50', 10);
const DURATION = __ENV.DURATION || '10m';
const MAX_MONITORS = parseInt(__ENV.MAX_MONITORS || '200', 10);

export const options = {
  scenarios: {
    api_read: {
      executor: 'constant-vus',
      vus: VUS,
      duration: DURATION,
    },
  },
  thresholds: {
    http_req_duration: ['p(95)<300'],
    http_req_failed: ['rate<0.005'],
    checks: ['rate>0.995'],
  },
};

function extractMonitors(body) {
  // The API returns a JSON list; tolerate a paginated {"items": [...]} shape too.
  if (Array.isArray(body)) return body;
  if (body && Array.isArray(body.items)) return body.items;
  return [];
}

export function setup() {
  const res = http.get(`${BASE_URL}/api/v1/monitors`, { tags: { name: 'GET /api/v1/monitors' } });
  if (res.status !== 200) {
    fail(`setup: GET /api/v1/monitors returned ${res.status}`);
  }
  const ids = extractMonitors(res.json())
    .map((m) => m.id)
    .filter((id) => id !== undefined && id !== null)
    .slice(0, MAX_MONITORS);
  if (ids.length === 0) {
    fail('setup: no monitors found; create some first (make seed or loadtest/seed_monitors.py)');
  }
  console.log(`setup: using ${ids.length} monitor ids against ${BASE_URL}`);
  return { ids };
}

export default function (data) {
  const page = http.get(`${BASE_URL}/`, { tags: { name: 'GET /' } });
  check(page, { 'status page 200': (r) => r.status === 200 });

  const id = data.ids[Math.floor(Math.random() * data.ids.length)];
  // Tag with the route template so k6 groups all ids under one metric series.
  const stats = http.get(`${BASE_URL}/api/v1/monitors/${id}/stats`, {
    tags: { name: 'GET /api/v1/monitors/{id}/stats' },
  });
  check(stats, {
    'stats 200': (r) => r.status === 200,
    'stats is JSON': (r) => {
      try {
        return typeof r.json() === 'object';
      } catch (e) {
        return false;
      }
    },
  });

  // Think time: a human refreshing a dashboard, not a tight loop.
  sleep(0.5 + Math.random());
}
