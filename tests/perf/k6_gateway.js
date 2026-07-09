import http from 'k6/http';
import { check, sleep } from 'k6';

export const options = {
  thresholds: {
    http_req_failed: ['rate<0.01'],
    'http_req_duration{name:ai_chat_acceptance}': ['p(95)<1000'],
    'http_req_duration{name:agent_job_acceptance}': ['p(95)<5000'],
  },
  vus: 5,
  duration: '1m',
};

const gateway = (__ENV.GATEWAY_URL || 'http://127.0.0.1:9090').replace(/\/$/, '');

export default function () {
  const health = http.get(`${gateway}/health`);
  check(health, { 'health ok': (res) => res.status === 200 && res.json('status') === 'ok' });
  sleep(1);
}