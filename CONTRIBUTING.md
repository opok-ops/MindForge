# Contributing to MindForge

First off, thank you for considering contributing to MindForge! 🎉

## Getting Started

1. Fork the repository
2. Clone your fork: `git clone https://github.com/YOUR_USERNAME/MindForge.git`
3. Create a feature branch: `git checkout -b feature/amazing-feature`
4. Install in development mode: `pip install -e ".[dev]"`
5. Make your changes
6. Run tests: `pytest tests/ -v`
7. Commit: `git commit -m "feat: add amazing feature"`
8. Push: `git push origin feature/amazing-feature`
9. Open a Pull Request

## Pull Request Guidelines

- **One PR per feature/bugfix** — keep changes focused
- **Add tests** — for new features, include test coverage
- **Update docs** — if you change behavior, update README/docs
- **Follow existing code style** — match the patterns in the codebase
- **Write clear commit messages** — use conventional commits format

## Commit Message Format

```
<type>(<scope>): <subject>

<description>
```

Types: `feat`, `fix`, `docs`, `style`, `refactor`, `perf`, `test`, `chore`, `security`

Examples:
- `feat(search): add cross-encoder reranking`
- `fix(storage): fix FTS5 index cleanup on soft delete`
- `security(encryption): remove HMAC-XOR fallback path`

## Reporting Bugs

Before submitting a bug report:
1. Check the [existing issues](https://github.com/opok-ops/MindForge/issues)
2. Check the [Changelog](CHANGELOG.md) for known issues

When filing a bug, include:
- MindForge version (`pip show MindForge`)
- Python version and OS
- Steps to reproduce
- Expected vs actual behavior
- Error messages / stack traces

## Feature Requests

We welcome feature requests! Please open an issue with:
- Clear description of the feature
- Why it would be useful
- Potential implementation ideas (if you have them)

## Code of Conduct

Be respectful, inclusive, and constructive. We're all here to build something great together.

## Questions?

Open a [discussion](https://github.com/opok-ops/MindForge/discussions) — we're happy to help!

---

Thank you for contributing! 🙏
