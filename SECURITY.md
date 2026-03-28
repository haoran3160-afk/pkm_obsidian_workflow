# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 2.x.x   | ✅ Actively supported |
| 1.x.x   | ❌ No longer supported |

## Reporting a Vulnerability

We take security seriously. If you discover a security vulnerability, **please do not open a public GitHub issue**.

Instead, please report it via one of the following:

1. **GitHub Private Vulnerability Reporting** (preferred):  
   Navigate to the [Security tab](../../security) → *Report a vulnerability*.

2. **Email**: Contact the maintainer directly via email listed in the repository profile.

### What to Include

Please provide as much detail as possible:
- Description of the vulnerability
- Steps to reproduce
- Potential impact assessment
- Suggested fix (if you have one)

### Response Timeline

- We will acknowledge your report within **48 hours**.
- We aim to provide a fix or mitigation within **14 days** for critical issues.

## Scope

This project runs **entirely locally** on the user's machine. It does not host any public endpoints or process user data in the cloud.

Common areas to consider:
- **Path Traversal**: The `write_to_obsidian_disk` function constructs file paths from external RSS content.
- **SSRF**: `fetcher.py` performs HTTP requests to configurable URLs. Malicious URLs in `pkm_config.json` could be used if an attacker has write access to the config file.
- **Template Injection**: Jinja2 templates are loaded from the local filesystem only; external template injection is not a threat model.

## Disclosure Policy

We follow **Coordinated Vulnerability Disclosure**. We ask that you give us a reasonable amount of time to address the vulnerability before any public disclosure.
