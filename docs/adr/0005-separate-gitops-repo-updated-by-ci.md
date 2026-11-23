# 0005: Separate config repo (pulse-gitops) updated by CI with a GitHub App token

- Status: Proposed (draft — rewrite in your own words before marking Accepted)
- Date: 2026-11-22

## Context

Argo CD deploys whatever is in git. After CI pushes image `sha-xxxxxxx` to ECR, something has to
change the desired state so Argo CD deploys it. Goals:

- CI must never hold cluster credentials or reach the EKS API (the endpoint is restricted to the
  owner's IP).
- Every deployment should be an auditable, revertible git commit.
- The credential CI uses to write the config must be scoped as narrowly as possible.

## Options considered

Where the config lives:

1. **Separate repo `pulse-gitops`** (chart, per-env values, Argo CD apps)
   - Pros: deploy history is separate from code history; CI commits do not trigger app CI loops;
     access can differ (who can deploy vs who can change code); the classic GitOps layout.
   - Cons: two repos to keep in sync; chart changes and app changes land separately.
2. **Same repo, config in a folder**
   - Pros: one repo, atomic changes.
   - Cons: CI committing tag bumps to the repo it builds from creates noise and needs care to avoid
     triggering itself; every deploy is mixed into the code history.

Credential for CI to push to `pulse-gitops`:

1. **GitHub App installed only on `pulse-gitops`**, token minted per run with
   `actions/create-github-app-token`
   - Pros: installation tokens are short-lived (they expire after one hour); scoped to the
     installed repository and to the permissions granted to the app (`contents: write` only);
     commits are attributed to the app bot; revocable by uninstalling the app.
   - Cons: more setup (create app, install it, store app ID and private key); the app private key
     is a long-lived secret in `pulse`, though it can only mint tokens with the app's limited scope.
2. **SSH deploy key with write access**, stored as `GITOPS_DEPLOY_KEY`
   - Pros: simple; scoped to one repo.
   - Cons: never expires unless rotated manually; full write to every branch of that repo; not tied
     to an identity in the audit log beyond "deploy key".
3. **Fine-grained personal access token**
   - Pros: simple; can be limited to one repo and `contents: write`.
   - Cons: tied to a personal account; has to be rotated on expiry; acts as the owner.

## Decision

Use a separate `pulse-gitops` repository. CI writes to it with a GitHub App that is installed only on
`pulse-gitops` with `contents: write`. The `promote-dev` job mints a token with
`actions/create-github-app-token`, sets `.image.tag` in `envs/dev/values.yaml` with `yq`, and commits
`chore(dev): deploy sha-xxxxxxx`. Prod is promoted by the manual `promote-prod.yml` workflow behind
the `prod` environment approval.

## Consequences

- Easier: CI has no cluster access at all; the deploy log is `git log` of `pulse-gitops`; rollback is
  `git revert`; prod promotion is a reviewed, approved commit.
- Harder: two repos; the app private key must be stored as a secret and rotated if exposed;
  concurrent CI runs can race when pushing (rebase-and-retry, or a concurrency group on
  `promote-dev`).
- Tokens minted by a GitHub App, unlike the default `GITHUB_TOKEN`, do trigger workflows in the
  target repo, so `chart-ci.yml` in `pulse-gitops` runs on CI commits. That is useful, but keep it
  read-only.
- Branch protection on `pulse-gitops` `main` must allow the app to push, or CI must open PRs instead.
- At larger scale: tools like Argo CD Image Updater or Kargo can manage promotion; PR-based promotion
  with required checks is a natural next step.
