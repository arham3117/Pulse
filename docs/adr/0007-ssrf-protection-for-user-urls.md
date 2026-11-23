# 0007: SSRF protection on user-supplied URLs

- Status: Proposed (draft — rewrite in your own words before marking Accepted)
- Date: 2026-11-22

## Context

Pulse's core feature is "fetch any URL a user gives us". Workers run inside the VPC, so without
protection a monitor could target:

- the EC2 instance metadata service `169.254.169.254` (node IAM credentials);
- the EKS Pod Identity agent at `169.254.170.23` (IPv6 `fd00:ec2::23`);
- the Kubernetes API and in-cluster services (`*.svc.cluster.local`, service CIDR);
- RDS and anything else in `10.20.0.0/16`;
- localhost services in the worker pod.

This is server-side request forgery (SSRF). Even though Pulse does not show response bodies, a
monitor still leaks information: status codes, response times, error types (`connection` vs
`timeout`) and whether a keyword matched. That is enough for internal port scanning and one-bit
content probing.

## Options considered

1. **Validate the URL once at creation**
   - Pros: simple; immediate 400 for obviously bad URLs.
   - Cons: DNS can change after creation (DNS rebinding); redirects can point anywhere.
2. **Validate at creation and at check time, follow redirects manually and validate every hop**
   - Pros: catches DNS changes between creation and check; blocks redirect-based bypasses.
   - Cons: still a gap between our DNS lookup and the HTTP client's connect (see below).
3. **Pin the resolved IP**: resolve once, validate, then connect to that exact IP while sending the
   original `Host` header and TLS SNI
   - Pros: closes the time-of-check/time-of-use gap.
   - Cons: needs a custom httpx transport or resolver; TLS verification must still use the hostname;
     more code to get right.
4. **Egress proxy** (for example Stripe's Smokescreen) that enforces the IP policy at connect time
   - Pros: policy enforced at the only point that matters, for all HTTP traffic; central logging.
   - Cons: another component to run and scale; adds latency.
5. **Network controls**: Kubernetes NetworkPolicy egress rules denying private ranges from worker
   pods (allowing only DNS, RDS and the chaos target), plus IMDSv2 hop limit 1
   - Pros: independent of application code.
   - Cons: requires network policy enforcement in the VPC CNI; does not cover everything (for example
     public IPs that are sensitive to us).

## Decision

Implement option 2 in `core/url_safety.py`, with network-level defence in depth:

- Allow only `http` and `https`.
- Resolve the hostname and collect **all** A and AAAA records. Reject if **any** address is loopback,
  private (10/8, 172.16/12, 192.168/16), link-local (169.254/16, which includes IMDS and the Pod
  Identity agent), carrier-grade NAT (100.64/10), multicast, reserved or unspecified, IPv6 loopback,
  unique-local (fc00::/7) or IPv6 link-local (fe80::/10). IPv4-mapped IPv6 addresses
  (`::ffff:a.b.c.d`) must be unwrapped and checked as IPv4.
- Validation runs on resolved addresses, so alternative IP spellings (decimal `2852039166`, octal,
  short forms) are caught after resolution rather than by string matching.
- Validate **at creation** (API returns 400) **and at check time** (worker records
  `error_type=blocked`).
- httpx does not follow redirects automatically. The worker follows up to 3 hops manually and
  validates each `Location` target with the same function.
- Hostnames listed in `PULSE_ALLOW_PRIVATE_HOSTS` (only `pulse-target`) are exempt, by exact match.
- Defence in depth: node launch template requires IMDSv2 with hop limit 1, so a pod cannot complete
  the IMDSv2 token exchange even if a request got through. Workers also only send `GET`/`HEAD`,
  while IMDSv2 needs a `PUT` to obtain a session token.

### Residual risk

There is a time-of-check/time-of-use gap: the validator resolves DNS, then httpx resolves the name
again when it connects. An attacker who controls a domain's DNS can answer the first lookup with a
public IP and the second with `169.254.169.254` or `10.x` (DNS rebinding with a very low TTL). The
check-time validation narrows the window but does not close it.

Mitigations, in order of preference for a follow-up:

1. Pin the validated IP: connect to the address that was checked and pass the hostname for `Host`
   and SNI, so no second lookup happens.
2. Route worker egress through an egress proxy that applies the same IP policy at connect time.
3. NetworkPolicy egress deny for private ranges from `pulse-worker` pods.

Other residual risks: response timing and error types still reveal some information about blocked
vs unreachable hosts; the allow-list is a configuration risk if someone adds a broad name to it.

## Consequences

- Easier: the common attacks (metadata, localhost, private IPs, redirects to them) return 400 or
  `blocked`; table-driven unit tests cover every range.
- Harder: every check pays for a DNS lookup; monitors for hosts with any private record are rejected
  even if they also have public records (intended); the TOCTOU gap is documented, not closed.
- Evidence for this control is collected in [security.md](../security.md).
- At larger scale: run checks from an isolated network (separate VPC or account with no route to
  internal systems), which removes most of the blast radius regardless of application validation.
