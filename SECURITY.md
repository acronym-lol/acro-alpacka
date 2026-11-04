# Security

ALPACKA opens other people's project folders and templates and runs the commands they define, so security reports are very welcome.

## Reporting a problem

Please report security problems **privately** through GitHub: go to the repository's **Security** tab and choose **Report a vulnerability** ([private vulnerability reporting](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability)). Please don't open a public issue for it.

Include what you found, how to reproduce it (a minimal `alpacka.yml`, `template.yml` or steps), and what an attacker could do with it. We'll acknowledge the report, keep you posted while we fix it, and credit you in the release notes if you'd like.

## What's in scope

- Anything that makes ALPACKA run a command, write a file, or kill a process the user didn't approve — for example through a project's `alpacka.yml` / `alpacka.local.yml`, a template, a TouchDesigner version string, or the update check.
- Values from `alpacka.local.yml` (often secrets) ending up in `alpacka.yml` or anywhere else they could be committed.
- The packaged app, installer and release workflow.

## What isn't

A project's own code runs when you launch it — opening a `.toe` runs its Python, and `npm` / `pip` run a package's scripts. ALPACKA shows you what a project's settings will run and asks before running them, but it can't make an untrusted project safe to open. Only open projects you trust.

## Supported versions

Fixes go into the latest release. Please update before reporting, if you can.
