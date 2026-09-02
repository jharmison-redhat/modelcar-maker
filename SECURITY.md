# Security policy

modelcar-maker is designed for trusted local and operator-supplied model, registry, configuration, and Helm values.
It applies focused correctness validation at boundaries; it is not a hardened service for hostile multi-tenant input.

To report a vulnerability, open a report through this project's GitHub repository:
<https://github.com/jharmison-redhat/modelcar-maker>. Include affected version, impact, and reproduction details. Do not
place credentials or access tokens in the report.

Out of scope for the current deferred hardening work are broad adversarial-input defenses, dependency lockfiles,
automated dependency updates, SBOMs, provenance, and signing.
