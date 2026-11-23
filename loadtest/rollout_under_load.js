// Scenario C (plan.md 7.3): rollout under load.
//
// Constant-arrival-rate traffic to /healthz, / and /api/v1/monitors while a new version is
// deployed (bump the image tag in pulse-gitops and let Argo CD roll it out). With readiness
// probes, maxUnavailable: 0 and graceful shutdown, the expected result is ZERO failed requests.
//
// Run:  k6 run -e BASE_URL=https://pulse-dev.example.com -e DURATION=15m loadtest/rollout_under_load.js
// Optional: -e RATE=30 (iterations/second; each iteration is one request)
import http from 'k6/http';
import { check } from 'k6';

const BASE_URL = (__ENV.BASE_URL || 'http://localhost:8000').replace(/\/+$/, '');
const DURATION = __ENV.DURATION || '10m';
const RATE = parseInt(__ENV.RATE || '30', 10);

const ENDPOINTS = [
  { path: '/healthz', name: 'GET /healthz' },
  { path: '/', name: 'GET /' },
  { path: '/api/v1/monitors', name: 'GET /api/v1/monitors' },
];

export const options = {
  scenarios: {
    rollout: {
      executor: 'constant-arrival-rate',
      rate: RATE,
      timeUnit: '1s',
      duration: DURATION,
      preAllocatedVUs: Math.max(10, RATE),
      maxVUs: Math.max(100, RATE * 5),
    },
  },
  thresholds: {
    // Any failed request during the rollout is a failed test.
    http_req_failed: ['rate==0'],
    // If k6 cannot keep up the arrival rate, the test is not measuring what we think.
    dropped_iterations: ['count==0'],
  },
};

export default function () {
  const ep = ENDPOINTS[__ITER % ENDPOINTS.length];
  const res = http.get(`${BASE_URL}${ep.path}`, { tags: { name: ep.name }, timeout: '10s' });
  check(res, { [`${ep.name} 200`]: (r) => r.status === 200 });
}
