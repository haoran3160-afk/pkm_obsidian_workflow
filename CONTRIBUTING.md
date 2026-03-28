# Contributing to Obsidian PKM Workflow

We love your input! We want to make contributing to this project as easy and transparent as possible.

## How to Contribute

1. **Fork the repo** and create your branch from `main`.
2. **Install dependencies**: `pip install -r requirements.txt`.
3. **Make your changes**. If you add a new feed parser, ensure it integrates seamlessly with `fetcher.py`.
4. **Test your code**. Run `pytest` for unit tests and `python main.py --test` for a workflow smoke test.
5. **Issue that pull request!**

## Code Structure Rules

This project follows a strict **ETL** separation of concerns:
- **Extract** (`fetcher.py`): No formatting or disk I/O should occur here. All network requests must use `@retry` from the `tenacity` library.
- **Transform** (`formatter.py`): Pure functions only. Take a dict/string, output Markdown data. No network requests or file writes.
- **Load** (`writer.py`): The only module allowed to talk to the Obsidian API or Vault File System.

## Reporting Bugs

We use GitHub issues to track public bugs. Report a bug by opening a new issue; please include the log output (`fetch.log`) and the steps required to reproduce the issue.

## Pull Request Process

1. Update the `README.md` with details of changes to the CLI or new environmental variables if applicable.
2. Provide a clear description of the problem you are solving in your PR.
3. The PR will be merged once you have the sign-off of at least one maintainer.

## License

By contributing, you agree that your contributions will be licensed under its MIT License.
