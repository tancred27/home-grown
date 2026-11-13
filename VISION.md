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

1. **Search again when the first pass is weak:** when the judge finds nothing good, a few
   more searches before answering.
2. **A test on a large real multi-team organization**, with duplicates turned on.
3. **Real permissions:** sign-in through each company's own identity provider, team and
   access sync from GitHub, and a SCIM, Okta or Entra connector feeding the directory
   import. Until then the dashboard has no real sign-in.
4. **Models in your own cloud:** Amazon Bedrock, Google Vertex AI and Microsoft Foundry.
5. **Lower model cost:** cache results across questions, scoped by permission, and a
   cheaper duplicates judge.
6. **More of the admin view:** what people ask for that doesn't exist yet.
7. **More sources:** GitLab, Bitbucket, and more documentation systems.

Done so far: BM25 + vector search with rank fusion, a code reader that splits
repositories into reusable units, a judge that picks and orders up to three results with
a reason each, a clarifying question when the request is ambiguous, who to ask (recent
committers and their managers, from a directory import), duplicates across teams (opt-in),
an org map, an explanation for every answer, and traces and metrics (OpenTelemetry).

Ideas and pull requests toward any of these are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md).
