# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a Python CLI tool that automates patch review workflows. It downloads patches from GitHub (PRs/commits) or Phabricator diffs, manages repository checkouts, applies patches, and analyzes them using Claude Code with structured prompts designed for code review.

## Key Commands

### Development Setup
```bash
pip install -r requirements.txt
chmod +x patch_review_cli.py
```

### Running the Tool
```bash
# Basic usage - review a patch
./patch_review_cli.py <url>

# With language context
./patch_review_cli.py -l Python <url>

# With custom questions
./patch_review_cli.py -q "Are there security concerns?" <url>

# Analyze without checkout (faster for quick reviews)
./patch_review_cli.py --no-checkout <url>

# Checkout repo but don't apply patch
./patch_review_cli.py --no-apply <url>
```

### URL Formats Supported
- GitHub PR: `https://github.com/owner/repo/pull/123`
- GitHub commit: `https://github.com/owner/repo/commit/abc123`
- Phabricator: `https://phabricator.services.mozilla.com/D123456`

## Architecture

### Core Workflow (main function)
The tool follows a linear workflow with multiple fallback strategies:

1. **URL Parsing** (`get_repo_info_from_url`): Extract owner/repo from GitHub URLs or default to mozilla-firefox/firefox for Phabricator
2. **Patch Download** (`download_github_patch` / `download_phabricator_patch`): Fetch raw diff via `.diff` endpoints (cleaner format without commit history)
3. **Comment Fetching** (`fetch_github_pr_comments` / `fetch_github_commit_comments` / `fetch_phabricator_comments`): Fetch existing reviews and comments from the PR/commit
4. **Repository Management** (`ensure_repository`): Clone if needed, or update existing repo
5. **Patch Application** (`apply_patch`): Create branch and apply using multiple git strategies
6. **Claude Analysis** (`analyze_with_claude`): Invoke Claude Code with structured review prompt including existing comments

### Comment Fetching (fetch_* functions)
The tool automatically fetches existing comments and reviews to provide Claude with full context:

**GitHub PRs** (`fetch_github_pr_comments`):
- Review comments (inline code comments) via `/repos/{owner}/{repo}/pulls/{pr_num}/comments`
- General PR comments via `/repos/{owner}/{repo}/issues/{pr_num}/comments`
- PR reviews (APPROVED/CHANGES_REQUESTED/COMMENTED) via `/repos/{owner}/{repo}/pulls/{pr_num}/reviews`

**GitHub Commits** (`fetch_github_commit_comments`):
- Commit comments via `/repos/{owner}/{repo}/commits/{sha}/comments`

**Phabricator** (`fetch_phabricator_comments`):
- Not yet implemented - requires Conduit API authentication
- Currently returns empty string with a note to user

Comments are formatted with clear separators and include:
- Username of commenter
- Comment location (file:line for inline comments)
- Comment body/review state
- Clear visual separation between comments

Claude is instructed to consider these existing comments when providing analysis, allowing it to build upon or address previous reviewer feedback.

### Patch Application Strategy (apply_patch function)
The tool tries multiple git apply methods in sequence to handle various patch formats:
1. `git apply --3way` (preferred - handles conflicts better)
2. `git apply` (standard application)
3. `git apply --whitespace=fix` (for patches with whitespace issues)
4. If all fail, show conflict details with `git apply --check` and `git apply --stat`

Before applying, the function:
- Stashes or resets uncommitted changes
- Detects main branch (main vs master) via `git symbolic-ref refs/remotes/origin/HEAD`
- Creates a timestamped review branch: `patch-review-{pid}`

### Claude Integration (analyze_with_claude function)
Builds structured prompts that mirror the Firefox extension's review format:
- Includes patch content directly (if patch application failed) or instructs Claude to use `git diff`
- Inserts existing comments/reviews from the PR/commit (if available)
- Instructs Claude to consider existing feedback when analyzing
- Standard questions: summary, improvements, duplication, performance, bugs/edge cases
- Requests LINE-BY-LINE FEEDBACK format: `filename:line severity "comment"`
- Severity levels: PEDANTIC, LOW, MEDIUM, HIGH
- Generates COPY-PASTE SUMMARY section for posting as review comments

The function invokes Claude via stdin to avoid shell argument length limits:
```python
subprocess.run(['claude', '--print'], input=prompt_content, text=True, cwd=repo_path)
```

### Repository Organization
Repos are organized by owner/name under `~/repos` (configurable with `-d`):
```
~/repos/
├── owner1/
│   └── repo1/
├── mozilla-firefox/
│   └── firefox/
```

## Code Review Prompt Format

The tool uses a specific prompt structure that you should maintain when modifying `analyze_with_claude`:

1. Developer context: "I am a {language} developer"
2. Patch source: URL reference
3. Patch content: Either embedded or via `git diff` instruction
4. Existing comments/reviews: Formatted with separators and attribution (if available)
5. Instruction to consider existing feedback
6. Standard review questions (improvements, duplication, performance, bugs)
7. LINE-BY-LINE FEEDBACK format specification
8. COPY-PASTE SUMMARY section for posting

This format ensures consistency with the Firefox extension and provides structured output that can be copy-pasted into review tools.

## Special Considerations

### GitHub API Rate Limiting
The tool makes unauthenticated API requests to fetch comments/reviews. GitHub's unauthenticated API has a rate limit of 60 requests/hour per IP. For higher limits (5000 requests/hour), set the `GITHUB_TOKEN` environment variable with a personal access token. The comment fetching functions gracefully handle failures and continue with analysis even if comments cannot be fetched.

### Phabricator URLs
Mozilla Phabricator URLs map to `mozilla-firefox/firefox` repo, not the full `mozilla/gecko-dev`. This may need adjustment based on actual Mozilla workflow.

### Prompt File Preservation
The tool saves the review prompt to `{repo_path}/claude-review-prompt-{pid}.txt` for follow-up questions. This allows iterative review without regenerating the context.

### Timeout Handling
Claude invocation has a 5-minute timeout (300 seconds) to handle large patches. Adjust in line 284 if needed.

### No-Checkout Mode
When `--no-checkout` is used, the tool analyzes the raw patch without repository context. This is faster but Claude won't have access to the full codebase for deeper analysis.
