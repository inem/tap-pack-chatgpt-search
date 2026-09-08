# Provenance review

Reviewed 2026-09-08 for the initial `0.1.0` artifact.

The legacy private checkout was used as behavioral evidence for the observed
request fields, endpoint path, pagination values, credential file names, error
classes and no-retry behavior. The files in this repository were written anew
for the public pack contract and standard-library runtime. No user credentials,
conversation contents, captured traffic, private fixtures, generated titles or
organizer code are included.

The anonymized fixture identifiers, titles, snippets and response bodies in the
tests are new synthetic data. A live verification, when performed, must discard
stdout and must not commit its response or auth material. This review records
source provenance; it does not claim that ChatGPT's private web endpoint is a
stable or supported API.
