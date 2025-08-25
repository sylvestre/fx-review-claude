# Patch Review CLI

A Python CLI tool that automatically checkouts repositories, applies patches from GitHub/Phabricator, and analyzes them using Claude Code.

## Features

- **Repository Management**: Automatically clones repositories if not present locally
- **Patch Application**: Downloads and applies patches from GitHub PRs/commits or Phabricator diffs
- **Code Review**: Uses the same Claude prompts as the Firefox extension for consistent analysis
- **Flexible Workflow**: Options to skip checkout or patch application for different use cases

## Installation

```bash
# Install dependencies
pip install -r requirements.txt

# Make the script executable
chmod +x patch_review_cli.py
```

## Usage

### Basic Usage

```bash
# Review a GitHub PR
./patch_review_cli.py https://github.com/owner/repo/pull/123

# Review a GitHub commit
./patch_review_cli.py https://github.com/owner/repo/commit/abcdef123

# Review a Phabricator diff
./patch_review_cli.py https://phabricator.services.mozilla.com/D123456
```

### Options

```bash
# Specify programming language context
./patch_review_cli.py -l Python https://github.com/owner/repo/pull/123

# Use custom base directory for repositories
./patch_review_cli.py -d ~/my-repos https://github.com/owner/repo/pull/123

# Add custom questions
./patch_review_cli.py -q "Are there any security concerns?" https://github.com/owner/repo/pull/123

# Only analyze patch without checking out repo
./patch_review_cli.py --no-checkout https://github.com/owner/repo/pull/123

# Checkout repo but don't apply patch (useful for large repos)
./patch_review_cli.py --no-apply https://github.com/owner/repo/pull/123
```

### Full Options

```
usage: patch_review_cli.py [-h] [-l LANGUAGE] [-d BASE_DIR] [-q QUESTIONS] 
                          [--no-checkout] [--no-apply] url

positional arguments:
  url                   GitHub PR/commit URL or Phabricator differential URL

optional arguments:
  -h, --help           show this help message and exit
  -l LANGUAGE          Programming language for the review context (default: Rust)
  -d BASE_DIR          Base directory for repositories (default: ~/repos)
  -q QUESTIONS         Additional questions to ask Claude about the patch
  --no-checkout        Don't checkout/clone repository, only analyze patch
  --no-apply          Don't apply patch to repository, only analyze the diff
```

## How It Works

1. **Extract Repository Info**: Parses the URL to determine repository owner/name
2. **Ensure Repository**: Clones the repository if not present, or updates existing repo
3. **Download Patch**: Fetches the patch/diff from GitHub or Phabricator
4. **Apply Patch**: Creates a new branch and applies the patch using `git apply`
5. **Analysis**: Runs Claude Code with the same prompts used in the Firefox extension
6. **Review Output**: Provides line-by-line feedback and overall assessment

## Repository Structure

The tool organizes repositories in a structured way:

```
~/repos/           (or custom base directory)
├── owner1/
│   ├── repo1/     (cloned GitHub repos)
│   └── repo2/
├── mozilla/
│   └── gecko-dev/ (for Phabricator Mozilla patches)
└── ...
```

## Examples

### Mozilla Firefox Development

```bash
# Review a Firefox patch from Phabricator
./patch_review_cli.py https://phabricator.services.mozilla.com/D260789

# This will:
# 1. Clone mozilla/gecko-dev if not present
# 2. Apply the Phabricator patch
# 3. Analyze with C++ context
```

### Open Source Projects

```bash
# Review a Rust project PR
./patch_review_cli.py -l Rust https://github.com/uutils/coreutils/pull/5678

# Review a Python project with custom questions
./patch_review_cli.py -l Python -q "Are there any performance implications?" \
    https://github.com/psf/requests/pull/1234
```

## Requirements

- Python 3.7+
- Git
- Claude Code CLI (`claude` command available in PATH)
- Internet connection for downloading patches

## Comparison with Firefox Extension

| Feature | Firefox Extension | CLI Tool |
|---------|------------------|----------|
| Interface | Browser button | Command line |
| Repository | Manual navigation | Auto clone/checkout |
| Patch Application | Manual | Automatic |
| Analysis Context | Same prompts | Same prompts |
| Platform | Firefox only | Any terminal |

The CLI tool provides the same analysis quality as the Firefox extension but with enhanced automation for repository management and patch application.