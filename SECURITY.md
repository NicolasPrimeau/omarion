# Security Policy

## Reporting a vulnerability

Please do not open a public issue for security vulnerabilities.

Email **nicolas.primeau@gmail.com** with a description of the issue, steps to reproduce, and any proof-of-concept. You'll receive a response within 72 hours.

## Scope

- Authentication bypass or privilege escalation on the REST API
- Memory or session data leakage across agents
- Remote code execution via any endpoint
- Secrets exposed in logs, responses, or error messages

## Out of scope

- Issues requiring physical access to the host
- Attacks against the SQLite file when the attacker already has filesystem access
