# Fork attribution and contribution evidence

Reviewed **2026-10-28**. This document describes **RichardRacette/odysseus**, a fork of **[odysseus-dev/odysseus](https://github.com/odysseus-dev/odysseus)**. It is not a claim that the fork owner created the Odysseus platform.

## Attribution at a glance

| Layer | Credit and claim boundary |
| --- | --- |
| Odysseus application, identity, core features and inherited code | Upstream Odysseus contributors and the credited third parties. Retain their notices, history and license. |
| Operating and evaluating a local deployment | Operator experience, documented separately; not authorship of the upstream platform and not a guarantee for a new install. |
| Fork-local documentation and coordination metadata | Inspectable additions in the baseline comparison below. They do not establish new runtime functionality. |
| Unmerged branches, local experiments and machine-only configuration | Excluded from claims about the default branch unless separately identified, reviewed and evidenced. |
| AI-assisted work | Describe direction, integration, evaluation and AI-assisted implementation honestly. A generated change or commit count does not establish individual authorship of inherited code. |

## Reproducible baseline

The default branch before this documentation pass was `dev` at [`9b46982031ebe7aad46a6d672210a1078290629a`](https://github.com/RichardRacette/odysseus/tree/9b46982031ebe7aad46a6d672210a1078290629a).

The upstream `dev` reference observed during comparison was `a646b591107edb738f2056ed1ff7cce4f302943f`. The common ancestor was `c9dd68d890a7c0ee0df9a0e351ce22aafd6c7c0f`. GitHub reported the fork **2 commits ahead and 547 behind** at that observation. These are historical branch-comparison counts, not product-quality metrics or a statement about current upstream status.

The [fork-local diff from that common ancestor](https://github.com/RichardRacette/odysseus/compare/c9dd68d890a7c0ee0df9a0e351ce22aafd6c7c0f...9b46982031ebe7aad46a6d672210a1078290629a) lists only:

- `HANDOFF.md`: an operator/fork handoff with dated observations and limitations.
- `README.md`: a link to that handoff.
- `.richieos/project.json` and `RICHIEOS.md`: coordination metadata.

**No fork-local application-code change appears in this default-branch comparison.** That bounded finding does not inspect every branch, every local machine, or the origin of every upstream line. It also does not say that documentation and operating work have no value.

This portfolio pass changes only the README and this attribution document. It does not modify coordination metadata, merge experimental branches, change application behavior, synchronize upstream, or certify deployment safety.

## Historical operating evidence

The [September 5 handoff](https://github.com/RichardRacette/odysseus/blob/9b46982031ebe7aad46a6d672210a1078290629a/HANDOFF.md) distinguishes tracked behavior from deployment observations. Preserve that distinction. A historical report of local model integration, authenticated access, startup continuity or evaluation is not a fresh check of today's environment.

The handoff also describes an unmerged document-RAG-default experiment and a narrow four-test result. Neither is a full-suite result, default-branch feature, upstream contribution or current production-readiness claim.

Before crediting a future code contribution, add the exact branch/commit, smallest relevant diff, implementation scope, executed checks, remaining limitations and upstream PR status. Do not call a fork patch an accepted upstream contribution without evidence of upstream acceptance.

## Suggested portfolio wording

> Documented operation and evaluation of an upstream self-hosted AI workspace, maintained a public fork with dated operating evidence and coordination metadata, and separated inherited platform features from local experimentation.

Do not use "built Odysseus," copy upstream star counts as personal traction, present upstream screenshots as a custom deployment, or claim that local experiments are shipped upstream features.

## Upstream, branding and licensing

The existing repository identifies its license as **AGPL-3.0-or-later**. [LICENSE](LICENSE) and [ACKNOWLEDGMENTS.md](ACKNOWLEDGMENTS.md) remain unchanged. This documentation pass does not relicense code or grant rights to third-party branding/assets. Preserve upstream attribution when sharing demonstrations.

The logo, interface image, landing page, project feature list and popularity links inherited by the README describe **upstream Odysseus**, not original products or audience owned by this fork.

## Maintenance boundary

The fork is not an automatically synchronized distribution. Review upstream changes, security notices and compatibility in an isolated branch before using or updating it. The observed divergence is a reason to review, not permission for a blind merge, dependency upgrade or replacement of an existing deployment.

Do not include machine addresses, credentials, private configuration, operational state or personal content in a public evidence update. Use fictional fixtures and only explicitly approved screenshots.
