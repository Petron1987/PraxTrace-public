# Release checklist

- Obtain explicit owner authorization for publication. An export, build, test pass or allowlist entry is not publication authorization.

- The project license is MIT, selected by the owner. Verify ownership and attribution of any future contributions. Keep upstream licenses separate.
- Build and test a wheel in a clean virtual environment, outside the source directory. Verify `import praxtrace` resolves to site-packages and no editable install or local link is used.
- Run synthetic unit tests, the demo and a host integration check. Record failures and coverage limitations.
- Review the exact Git index and history for private identifiers, credentials, business cases and absolute host paths. `.gitignore` alone is not a publication review.
- Review commit author identity, email, messages, tags, release assets and public build logs. Use an explicitly approved public identity; do not infer permission from local Git configuration.
- Keep credentials, personal data and real host evidence out of both product repositories. Public examples and test fixtures must be synthetic or explicitly cleared for redistribution. English translation and escaped strings are not anonymization.
- Public documentation, product messages, templates and default examples are English. Multilingual matching data and third-party notices are intentional exceptions.
- Chinese maintenance sources are versioned only in the private development repository. English documents describe the shipped scope; they are not a claim that every private method has been translated.
- Public releases use an explicitly reviewed file allowlist exported into a separate repository. Never push private development history to the public remote, even after deleting private files from the working tree.
- Do not publish `.local`, `.praxtrace`, virtual environments, build output, private records or downloaded upstream repositories.
- GitHub repository creation/push and PyPI publication are separate actions. No automatic publication is performed by this project.
