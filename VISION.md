# Vision

Engineers rebuild things their own company already has, mostly because they can't see
other teams' code. Coding agents make this worse: they write a new rate limiter in
seconds, and they can read even less of the org than the engineer can.

HomeGrown should be the thing every engineer and every coding agent checks first:
*has someone here already built this?* The answer should be honest, show its evidence,
respect permissions, and stay current without anyone maintaining a catalog by hand.

## Principles

- **Permissions are the product.** Telling someone that a thing exists, and who to ask,
  is useful even when they can't read the code. The database enforces this.
- **Evidence over description.** Tests, usage and history say more than a README does.
- **Say "I don't know".** A wrong "use this" costs more than a missed match.
- **No hand-maintained catalogs.** It keeps itself current from the systems teams
  already use.

## Next

Roughly in order:

1. **Admin dashboard:** the org map is done. Still to come: where things are
   duplicated, and what people ask for that doesn't exist yet.
2. **Recall at scale:** better handling of look-alike components across teams.
3. **Lower model cost:** cache results across questions, scoped by permission.
4. **Real permissions:** sign-in through an identity provider, and team and access
   sync from GitHub and directories.
5. **More sources:** GitLab, Bitbucket, and more documentation systems.

Done so far: traces and metrics (OpenTelemetry), and a separate "related, check these"
group for `extend` verdicts.

Ideas and pull requests toward any of these are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md).
