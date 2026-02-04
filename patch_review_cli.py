#!/usr/bin/env python3
"""
Patch review CLI tool that checkouts repos, applies patches, and analyzes them using Claude Code.
"""

import argparse
import hashlib
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse
import requests
from typing import Optional, Tuple

REVIEW_QUESTIONS = """* Are there any potential improvements to this patch?
* Is there any code duplication that could be reduced?
* Are there any potential performance improvements?
* Are there any potential bugs or edge cases not handled?"""


def run_command(cmd, cwd=None, capture=True):
    """Run a shell command and optionally capture output."""
    try:
        if capture:
            result = subprocess.run(
                cmd, shell=True, cwd=cwd, capture_output=True, text=True
            )
            if result.returncode != 0:
                print(f"Command failed: {cmd}")
                print(f"Error: {result.stderr}")
                return None
            return result.stdout.strip()
        else:
            result = subprocess.run(cmd, shell=True, cwd=cwd)
            return result.returncode == 0
    except Exception as e:
        print(f"Error running command '{cmd}': {e}")
        return None


def print_completion_message(url: str):
    """Print analysis completion message with the reviewed patch URL."""
    print("\n" + "=" * 80)
    print("Analysis complete")
    print(f"\nReviewed patch: {url}")
    print("=" * 80)


def get_review_filename(repo_path: Optional[str], url: str) -> str:
    """Generate a consistent filename for storing review results."""
    # Extract project name and identifier from URL
    github_match = re.match(r'https://github\.com/([^/]+)/([^/]+)/(pull|commit)/(.+?)(?:\?|$|#)', url)
    phab_match = re.search(r'/D(\d+)', url)

    # Store reviews in current working directory
    reviews_dir = Path.cwd() / "reviews"
    reviews_dir.mkdir(parents=True, exist_ok=True)

    if github_match:
        owner = github_match.group(1)
        repo = github_match.group(2)
        pr_type = github_match.group(3)  # 'pull' or 'commit'
        pr_id = github_match.group(4)

        # Sanitize repo name (remove .git suffix if present)
        repo = repo.replace('.git', '')

        if pr_type == 'pull':
            identifier = f"{owner}-{repo}-pr-{pr_id}"
        else:  # commit
            identifier = f"{owner}-{repo}-commit-{pr_id[:8]}"
    elif phab_match:
        identifier = f"mozilla-firefox-phab-D{phab_match.group(1)}"
    else:
        # Fallback: hash the URL
        url_hash = hashlib.md5(url.encode()).hexdigest()[:8]
        identifier = f"review-{url_hash}"

    return str(reviews_dir / f"{identifier}-latest.txt")


def load_previous_review(repo_path: Optional[str], url: str) -> Optional[str]:
    """Load previous review results if they exist."""
    review_file = get_review_filename(repo_path, url)

    if os.path.exists(review_file):
        try:
            with open(review_file, 'r') as f:
                content = f.read()

            # Get file modification time
            mtime = os.path.getmtime(review_file)
            review_date = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S")

            print(f"\nFound previous review from {review_date}")
            print(f"Review file: {review_file}\n")

            return content
        except Exception as e:
            print(f"Warning: Failed to load previous review: {e}")

    return None


def save_review_output(repo_path: Optional[str], url: str, output: str) -> None:
    """Save review output to a file."""
    review_file = get_review_filename(repo_path, url)

    try:
        # Add timestamp header
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        header = f"Review generated: {timestamp}\nPatch URL: {url}\n\n" + "=" * 80 + "\n\n"

        with open(review_file, 'w') as f:
            f.write(header + output)

        print(f"\nReview saved to: {review_file}")
    except Exception as e:
        print(f"\nWarning: Failed to save review output: {e}")


def get_repo_info_from_url(url: str) -> Optional[Tuple[str, str, str]]:
    """Extract repository information from GitHub/Phabricator URL."""
    parsed_url = urlparse(url)

    if "github.com" in parsed_url.netloc:
        # GitHub URL: https://github.com/owner/repo/pull/123 or commit/hash
        path_parts = parsed_url.path.strip("/").split("/")
        if len(path_parts) >= 2:
            owner, repo = path_parts[0], path_parts[1]
            return f"https://github.com/{owner}/{repo}.git", owner, repo
    elif "phabricator" in parsed_url.netloc and "mozilla" in parsed_url.netloc:
        # Mozilla Phabricator - assume Firefox repo
        return (
            "https://github.com/mozilla-firefox/firefox/",
            "mozilla-firefox",
            "firefox",
        )

    return None


def ensure_repository(
    repo_url: str, owner: str, repo: str, base_dir: str = "~/repos"
) -> Optional[str]:
    """Ensure repository is cloned locally and return the path."""
    base_path = Path(base_dir).expanduser()
    repo_path = base_path / owner / repo

    if repo_path.exists() and (repo_path / ".git").exists():
        print(f"Repository already exists at: {repo_path}")
        # Update the repo
        print("Updating repository...")
        if run_command("git fetch origin", cwd=repo_path, capture=False):
            print("Repository updated successfully")
        else:
            print("Warning: Failed to update repository")
        return str(repo_path)

    # Clone the repository
    print(f"Cloning repository {repo_url} to {repo_path}")
    repo_path.parent.mkdir(parents=True, exist_ok=True)

    if run_command(f"git clone {repo_url} {repo_path}", capture=False):
        print(f"Repository cloned successfully to: {repo_path}")
        return str(repo_path)
    else:
        print("Error: Failed to clone repository")
        return None


def download_github_patch(url: str) -> str:
    """Download a patch from GitHub PR or commit URL."""
    # Parse GitHub URL patterns
    pr_match = re.match(r"https://github\.com/([^/]+)/([^/]+)/pull/(\d+)", url)
    commit_match = re.match(
        r"https://github\.com/([^/]+)/([^/]+)/commit/([a-f0-9]+)", url
    )

    if pr_match:
        owner, repo, pr_num = pr_match.groups()
        patch_url = f"https://github.com/{owner}/{repo}/pull/{pr_num}.diff"
    elif commit_match:
        owner, repo, commit = commit_match.groups()
        patch_url = f"https://github.com/{owner}/{repo}/commit/{commit}.diff"
    else:
        raise ValueError("Invalid GitHub URL. Expected PR or commit URL.")

    response = requests.get(patch_url)
    response.raise_for_status()
    return response.text


def download_phabricator_patch(url: str) -> str:
    """Download a patch from Phabricator differential URL."""
    # Parse Phabricator URL pattern
    match = re.match(r"(https://[^/]+)/D(\d+)", url)
    if not match:
        raise ValueError(
            "Invalid Phabricator URL. Expected format: https://domain/D123456"
        )

    base_url, diff_id = match.groups()

    # Try to download raw diff
    patch_url = f"{base_url}/D{diff_id}?download=true"
    response = requests.get(patch_url)
    response.raise_for_status()
    return response.text


def fetch_github_pr_comments(url: str) -> str:
    """Fetch all comments from a GitHub PR (review comments + issue comments)."""
    pr_match = re.match(r"https://github\.com/([^/]+)/([^/]+)/pull/(\d+)", url)
    if not pr_match:
        return ""

    owner, repo, pr_num = pr_match.groups()
    all_comments = []

    # Check for GitHub token for authentication (avoid rate limits)
    headers = {}
    github_token = os.environ.get("GITHUB_TOKEN")
    if github_token:
        headers["Authorization"] = f"token {github_token}"

    try:
        # Fetch review comments (inline code comments)
        review_comments_url = (
            f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_num}/comments"
        )
        response = requests.get(review_comments_url, headers=headers)
        if response.status_code == 200:
            review_comments = response.json()
            for comment in review_comments:
                user = comment.get("user", {}).get("login", "Unknown")
                body = comment.get("body", "")
                path = comment.get("path", "N/A")
                line = comment.get("line", "N/A")
                all_comments.append(
                    f"Review comment by {user} on {path}:{line}\n{body}"
                )

        # Fetch general PR comments
        issue_comments_url = (
            f"https://api.github.com/repos/{owner}/{repo}/issues/{pr_num}/comments"
        )
        response = requests.get(issue_comments_url, headers=headers)
        if response.status_code == 200:
            issue_comments = response.json()
            for comment in issue_comments:
                user = comment.get("user", {}).get("login", "Unknown")
                body = comment.get("body", "")
                all_comments.append(f"General comment by {user}\n{body}")

        # Fetch PR reviews (approve/request changes/comment)
        reviews_url = (
            f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_num}/reviews"
        )
        response = requests.get(reviews_url, headers=headers)
        if response.status_code == 200:
            reviews = response.json()
            for review in reviews:
                user = review.get("user", {}).get("login", "Unknown")
                state = review.get("state", "COMMENTED")
                body = review.get("body", "")
                if body:  # Only include reviews with text
                    all_comments.append(f"Review by {user} ({state})\n{body}")
    except Exception as e:
        print(f"Warning: Failed to fetch some GitHub comments: {e}")

    if all_comments:
        return (
            "\n\n"
            + "=" * 80
            + "\nEXISTING COMMENTS/REVIEWS:\n"
            + "=" * 80
            + "\n\n"
            + "\n\n---\n\n".join(all_comments)
            + "\n\n"
            + "=" * 80
            + "\n"
        )
    return ""


def fetch_github_commit_comments(url: str) -> str:
    """Fetch comments from a GitHub commit."""
    commit_match = re.match(
        r"https://github\.com/([^/]+)/([^/]+)/commit/([a-f0-9]+)", url
    )
    if not commit_match:
        return ""

    owner, repo, commit_sha = commit_match.groups()
    all_comments = []

    # Check for GitHub token for authentication (avoid rate limits)
    headers = {}
    github_token = os.environ.get("GITHUB_TOKEN")
    if github_token:
        headers["Authorization"] = f"token {github_token}"

    try:
        comments_url = (
            f"https://api.github.com/repos/{owner}/{repo}/commits/{commit_sha}/comments"
        )
        response = requests.get(comments_url, headers=headers)
        if response.status_code == 200:
            comments = response.json()
            for comment in comments:
                user = comment.get("user", {}).get("login", "Unknown")
                body = comment.get("body", "")
                path = comment.get("path", "N/A")
                line = comment.get("line", "N/A")
                all_comments.append(f"Comment by {user} on {path}:{line}\n{body}")
    except Exception as e:
        print(f"Warning: Failed to fetch GitHub commit comments: {e}")

    if all_comments:
        return (
            "\n\n"
            + "=" * 80
            + "\nEXISTING COMMENTS:\n"
            + "=" * 80
            + "\n\n"
            + "\n\n---\n\n".join(all_comments)
            + "\n\n"
            + "=" * 80
            + "\n"
        )
    return ""


def fetch_phabricator_comments(url: str) -> str:
    """Fetch comments from a Phabricator differential."""
    match = re.match(r"(https://[^/]+)/D(\d+)", url)
    if not match:
        return ""

    base_url, diff_id = match.groups()

    # Phabricator's public API requires authentication, so we'll try to scrape
    # or use the Conduit API if available. For now, return empty string.
    # Users can implement this with their Phabricator credentials if needed.
    print(
        "Note: Phabricator comment fetching requires API authentication (not yet implemented)"
    )
    return ""


def apply_patch(patch_content: str, repo_path: str, create_branch: bool = True) -> bool:
    """Apply patch to the repository."""
    if create_branch:
        # Clean up any uncommitted changes first
        print("Cleaning up repository state...")

        # Check if there are uncommitted changes
        status_output = run_command("git status --porcelain", cwd=repo_path)
        if status_output:
            print("Found uncommitted changes, stashing them...")
            if not run_command(
                "git stash push -u -m 'Automated stash before patch review'",
                cwd=repo_path,
                capture=False,
            ):
                print("Failed to stash changes, trying hard reset...")
                run_command("git reset --hard HEAD", cwd=repo_path, capture=False)
                run_command("git clean -fd", cwd=repo_path, capture=False)

        # Create a new branch for the patch
        branch_name = f"patch-review-{os.getpid()}"
        print(f"Creating branch: {branch_name}")

        # Ensure we're on main/master and it's up to date
        main_branch = run_command(
            "git symbolic-ref refs/remotes/origin/HEAD", cwd=repo_path
        )
        if main_branch:
            main_branch = main_branch.split("/")[-1]
        else:
            # Try to detect main branch
            branches = run_command("git branch -r", cwd=repo_path)
            if branches and "origin/main" in branches:
                main_branch = "main"
            elif branches and "origin/master" in branches:
                main_branch = "master"
            else:
                main_branch = "main"  # default fallback

        print(f"Switching to {main_branch} branch...")
        run_command(f"git checkout {main_branch}", cwd=repo_path, capture=False)
        print(f"Updating {main_branch} branch...")
        run_command(f"git pull origin {main_branch}", cwd=repo_path, capture=False)

        if not run_command(
            f"git checkout -b {branch_name}", cwd=repo_path, capture=False
        ):
            print("Error: Failed to create branch")
            return False

    # Save patch to temporary file
    with tempfile.NamedTemporaryFile(mode="w", suffix=".patch", delete=False) as f:
        f.write(patch_content)
        patch_file = f.name

    try:
        # Try multiple approaches to apply the patch
        print("Applying patch...")

        # Method 1: Try git apply with 3-way merge
        if run_command(f"git apply --3way {patch_file}", cwd=repo_path, capture=False):
            print("Patch applied successfully with 3-way merge")
            return True

        # Method 2: Try git apply without 3-way merge
        print("3-way merge failed, trying standard git apply...")
        if run_command(f"git apply {patch_file}", cwd=repo_path, capture=False):
            print("Patch applied successfully")
            return True

        # Method 3: Try git apply with whitespace fixes
        print("Standard apply failed, trying with whitespace fixes...")
        if run_command(
            f"git apply --whitespace=fix {patch_file}", cwd=repo_path, capture=False
        ):
            print("Patch applied successfully with whitespace fixes")
            return True

        # Method 4: Show what conflicts exist
        print("All apply methods failed. Checking for conflicts...")
        conflict_output = run_command(f"git apply --check {patch_file}", cwd=repo_path)
        if conflict_output:
            print(f"Conflict details: {conflict_output}")

        # Try to get partial application info
        print("Attempting to show what would be applied...")
        run_command(f"git apply --stat {patch_file}", cwd=repo_path, capture=False)

        print("Error: Failed to apply patch cleanly")
        return False
    finally:
        os.unlink(patch_file)


def run_interactive_followup(repo_path: str, url: str) -> None:
    """Run interactive follow-up session for asking additional questions."""
    print("\n" + "=" * 80)
    print("INTERACTIVE FOLLOW-UP MODE")
    print("=" * 80)
    print("You can now ask follow-up questions about the patch.")
    print("Type your question and press Enter. Type 'exit' or 'quit' to finish.")
    print("=" * 80 + "\n")

    while True:
        try:
            # Prompt for user input
            user_input = input("\nYour question (or 'exit' to quit): ").strip()

            if not user_input:
                continue

            # Check for exit commands
            if user_input.lower() in ["exit", "quit", "q", "done"]:
                print("\nExiting interactive mode...")
                print_completion_message(url)
                break

            # Run Claude with the follow-up question
            print("\n" + "=" * 80)
            print("CLAUDE RESPONSE:")
            print("=" * 80 + "\n")

            result = subprocess.run(
                ["claude", "--print"],
                input=user_input,
                text=True,
                cwd=repo_path,
                timeout=300,
            )

            if result.returncode != 0:
                print(f"\nWarning: Claude returned with code {result.returncode}")

        except KeyboardInterrupt:
            print("\n\nInterrupted by user. Exiting...")
            print_completion_message(url)
            break
        except subprocess.TimeoutExpired:
            print("\nError: Claude timed out after 5 minutes")
            print("You can try asking a simpler question or exit.")
        except Exception as e:
            print(f"\nError running Claude: {e}")
            print("You can try again or type 'exit' to quit.")


def analyze_with_claude(
    repo_path: str,
    language: str,
    url: str,
    custom_questions: Optional[str] = None,
    patch_content: Optional[str] = None,
    existing_comments: Optional[str] = None,
) -> None:
    """Run Claude Code to analyze the repository changes."""
    # Load previous review if it exists
    previous_review = load_previous_review(repo_path, url)

    # Build the base prompt with common instructions
    base_prompt = (
        f"I am a {language} developer, I need to review this patch from: {url}\n\n"
    )

    # Add patch content or git diff instruction
    if patch_content:
        # We have the patch content directly - use it
        base_prompt += f"""Here is the patch content:
```patch
{patch_content}
```

"""
    else:
        # No patch content provided - ask Claude to load changes from git
        diff_output = run_command("git diff HEAD", cwd=repo_path)
        if not diff_output:
            print("No changes found to analyze")
            return
        base_prompt += "Load the current changes with 'git diff' and analyze them.\n\n"

    # Add previous review if available
    if previous_review:
        base_prompt += "\n" + "=" * 80 + "\n"
        base_prompt += "PREVIOUS REVIEW:\n"
        base_prompt += "=" * 80 + "\n\n"
        base_prompt += previous_review
        base_prompt += "\n\n" + "=" * 80 + "\n"
        base_prompt += "Please compare the current patch with the previous review above.\n"
        base_prompt += "Note any improvements made, remaining issues, and new concerns.\n"
        base_prompt += "=" * 80 + "\n\n"

    # Add existing comments/reviews if available
    if existing_comments:
        base_prompt += existing_comments
        base_prompt += "\nPlease consider the above existing comments/reviews when providing your analysis.\n\n"

    # Add common review instructions
    base_prompt += """Analyze the patch overall and answer these questions:
* What does this patch do? Provide a brief summary.
* Propose specific improvements to this patch. Be concrete and actionable - provide exact code snippets showing how to implement the improvements.
* Identify and suggest how to reduce any code duplication. Show the exact refactored code.
* Propose specific performance improvements if applicable. Include concrete code examples.
* Identify potential bugs or edge cases not handled, and suggest how to fix them. Provide the actual code fix.
* Propose refactoring opportunities that would improve code quality, readability, or maintainability. Show before/after code examples with the concrete changes.

IMPORTANT: For every issue or improvement you identify, provide concrete code examples showing exactly how to fix it. Don't just describe what should be done - show the actual code.

Note: Focus your analysis on the implementation code. Keep test analysis brief - only mention critical issues in test code.

At the end of the output, provide LINE-BY-LINE FEEDBACK for ISSUES ONLY (no positive feedback) in this format:
filename:line_number severity "comment"

Severity levels: "PEDANTIC", "LOW", "MEDIUM", "HIGH"

Only include lines that have problems, potential bugs, improvements needed, pedantic, deduplication or other issues.
For example:
src/main.rs:45 LOW "Consider using unwrap_or_else() instead of unwrap() to handle potential errors"
lib/parser.rs:123 HIGH "This variable name 'x' is not descriptive"

If there are no issues with specific lines, just write "No line-specific issues found."

"""

    if custom_questions:
        base_prompt += f"\n\nAdditional questions:\n{custom_questions}"

    base_prompt += """

At the end, please provide a SIMPLIFIED SUMMARY section with:
--- COPY-PASTE SUMMARY START ---
[A concise review summary that can be posted as a comment, including:
- Key findings (improvements needed, bugs, performance issues)
- Overall assessment (LGTM with minor suggestions / Needs changes / etc.)
]
--- COPY-PASTE SUMMARY END ---"""

    # Write prompt to temporary file to avoid shell escaping issues
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".txt", delete=False
    ) as prompt_file:
        prompt_file.write(base_prompt)
        prompt_file_path = prompt_file.name

    try:
        # Try to run claude command in the repository directory
        print(f"Analyzing patch with Claude Code ({language} context)...")
        print(f"Working directory: {repo_path}")

        # Read the prompt content directly
        with open(prompt_file_path, "r") as f:
            prompt_content = f.read()

        success = False
        captured_output = []

        # Try direct invocation while capturing output for storage
        try:
            print("Running: claude --print with prompt via stdin")
            print(f"Prompt length: {len(prompt_content)} characters")
            print("\n" + "=" * 80)
            print("CLAUDE ANALYSIS OUTPUT:")
            print("=" * 80 + "\n")

            # Use Popen to capture output while displaying it
            process = subprocess.Popen(
                ["claude", "--print"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                cwd=repo_path,
            )

            # Write input and close stdin
            process.stdin.write(prompt_content)
            process.stdin.close()

            # Read and display output line by line while capturing
            for line in process.stdout:
                print(line, end='')
                captured_output.append(line)

            process.wait(timeout=300)
            success = process.returncode == 0

            if not success:
                print(f"\nClaude failed with return code {process.returncode}")
        except subprocess.TimeoutExpired:
            print("\nClaude timed out after 5 minutes")
            if process:
                process.kill()
        except FileNotFoundError:
            print(
                "\nError: 'claude' command not found. Please ensure Claude Code CLI is installed."
            )
        except Exception as e:
            print(f"\nError running Claude: {e}")

        if not success:
            print("\nClaude invocation failed.")
            print(
                f'Please manually run: cd {repo_path} && claude --print "$(cat {prompt_file_path})"'
            )
            return

        print_completion_message(url)

        # Save the review output
        if success and captured_output:
            review_text = ''.join(captured_output)
            save_review_output(repo_path, url, review_text)

        # Enter interactive follow-up mode
        run_interactive_followup(repo_path, url)

    finally:
        # Always preserve the prompt file for follow-up questions
        persistent_prompt_path = os.path.join(
            repo_path, f"claude-review-prompt-{os.getpid()}.txt"
        )
        try:
            # Copy the file instead of renaming to handle cross-device links
            import shutil

            shutil.copy2(prompt_file_path, persistent_prompt_path)
            os.unlink(prompt_file_path)  # Clean up the temp file
            print(f"\nPrompt saved to: {persistent_prompt_path}")
        except Exception as e:
            print(f"\nWarning: Could not save prompt to repo directory: {e}")
            print(f"Prompt remains at: {prompt_file_path}")
            print("You can copy it manually if needed.")


def main():
    parser = argparse.ArgumentParser(
        description="Download patches, checkout repos, apply patches, and analyze with Claude Code"
    )
    parser.add_argument(
        "url", help="GitHub PR/commit URL or Phabricator differential URL"
    )
    parser.add_argument(
        "-l",
        "--language",
        default="Rust",
        help="Programming language for the review context (default: Rust)",
    )
    parser.add_argument(
        "-d",
        "--base-dir",
        default="~/repos",
        help="Base directory for repositories (default: ~/repos)",
    )
    parser.add_argument(
        "-q", "--questions", help="Additional questions to ask Claude about the patch"
    )
    parser.add_argument(
        "--no-checkout",
        action="store_true",
        help="Don't checkout/clone repository, only analyze patch",
    )
    parser.add_argument(
        "--no-apply",
        action="store_true",
        help="Don't apply patch to repository, only analyze the diff",
    )

    args = parser.parse_args()

    # Get repository information from URL
    repo_info = get_repo_info_from_url(args.url)
    if not repo_info:
        print(
            f"Error: Could not extract repository information from URL: {args.url}",
            file=sys.stderr,
        )
        sys.exit(1)

    repo_url, owner, repo = repo_info
    print(f"Repository: {owner}/{repo}")

    # Download the patch
    try:
        parsed_url = urlparse(args.url)
        if "github.com" in parsed_url.netloc:
            print(f"Downloading patch from GitHub: {args.url}")
            patch_content = download_github_patch(args.url)
        elif parsed_url.path.startswith("/D"):
            print(f"Downloading patch from Phabricator: {args.url}")
            patch_content = download_phabricator_patch(args.url)
        else:
            print(
                f"Error: Unsupported URL format. Expected GitHub or Phabricator URL.",
                file=sys.stderr,
            )
            sys.exit(1)
    except Exception as e:
        print(f"Error downloading patch: {e}", file=sys.stderr)
        sys.exit(1)

    # Fetch existing comments/reviews
    print("Fetching existing comments and reviews...")
    existing_comments = ""
    try:
        parsed_url = urlparse(args.url)
        if "github.com" in parsed_url.netloc:
            if "/pull/" in args.url:
                existing_comments = fetch_github_pr_comments(args.url)
            elif "/commit/" in args.url:
                existing_comments = fetch_github_commit_comments(args.url)
        elif parsed_url.path.startswith("/D"):
            existing_comments = fetch_phabricator_comments(args.url)
    except Exception as e:
        print(f"Warning: Failed to fetch comments: {e}")

    if existing_comments:
        print("Successfully fetched existing comments/reviews")
    else:
        print("No existing comments found or unable to fetch")

    if args.no_checkout:
        # Just analyze the patch content directly
        print("Analyzing patch without repository checkout...")
        with tempfile.NamedTemporaryFile(mode="w", suffix=".patch", delete=False) as f:
            f.write(patch_content)
            patch_file = f.name

        # Load previous review if it exists
        previous_review = load_previous_review(None, args.url)

        base_prompt = f"""I am a {args.language} developer, I need to review this patch.

Here is the patch content:
```patch
{patch_content}
```

"""

        # Add previous review if available
        if previous_review:
            base_prompt += "\n" + "=" * 80 + "\n"
            base_prompt += "PREVIOUS REVIEW:\n"
            base_prompt += "=" * 80 + "\n\n"
            base_prompt += previous_review
            base_prompt += "\n\n" + "=" * 80 + "\n"
            base_prompt += "Please compare the current patch with the previous review above.\n"
            base_prompt += "Note any improvements made, remaining issues, and new concerns.\n"
            base_prompt += "=" * 80 + "\n\n"

        # Add existing comments if available
        if existing_comments:
            base_prompt += existing_comments
            base_prompt += "\nPlease consider the above existing comments/reviews when providing your analysis.\n\n"

        base_prompt += """First, provide LINE-BY-LINE FEEDBACK for ISSUES ONLY (no positive feedback) in this format:
filename:line_number severity "comment"

Severity levels: "PEDANTIC", "LOW", "MEDIUM", "HIGH"

Only include lines that have problems, potential bugs, improvements needed, or other issues.

If there are no issues with specific lines, just write "No line-specific issues found."

Then analyze the patch overall and answer these questions:
* What does this patch do? Provide a brief summary.
* Propose specific improvements to this patch. Be concrete and actionable - provide exact code snippets showing how to implement the improvements.
* Identify and suggest how to reduce any code duplication. Show the exact refactored code.
* Propose specific performance improvements if applicable. Include concrete code examples.
* Identify potential bugs or edge cases not handled, and suggest how to fix them. Provide the actual code fix.
* Propose refactoring opportunities that would improve code quality, readability, or maintainability. Show before/after code examples with the concrete changes.

IMPORTANT: For every issue or improvement you identify, provide concrete code examples showing exactly how to fix it. Don't just describe what should be done - show the actual code.

Note: Focus your analysis on the implementation code. Keep test analysis brief - only mention critical issues in test code."""

        if args.questions:
            base_prompt += f"\n\nAdditional questions:\n{args.questions}"

        # Write prompt to file and use file-based approach
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False
        ) as prompt_temp_file:
            prompt_temp_file.write(base_prompt)
            prompt_temp_file_path = prompt_temp_file.name

        try:
            # Pass prompt via stdin to avoid argument length limits
            print("\n" + "=" * 80)
            print("CLAUDE ANALYSIS OUTPUT:")
            print("=" * 80 + "\n")

            captured_output = []

            # Use Popen to capture output while displaying it
            process = subprocess.Popen(
                ["claude", "--print"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )

            # Write input and close stdin
            process.stdin.write(base_prompt)
            process.stdin.close()

            # Read and display output line by line while capturing
            for line in process.stdout:
                print(line, end='')
                captured_output.append(line)

            process.wait(timeout=300)

            if process.returncode == 0:
                print_completion_message(args.url)

                # Save the review output
                if captured_output:
                    review_text = ''.join(captured_output)
                    save_review_output(None, args.url, review_text)

                # Enter interactive follow-up mode (without repo context)
                print("\n" + "=" * 80)
                print("INTERACTIVE FOLLOW-UP MODE (Limited Context)")
                print("=" * 80)
                print("You can ask follow-up questions, but without repository checkout,")
                print("Claude will only have the patch content for context.")
                print("Type your question and press Enter. Type 'exit' or 'quit' to finish.")
                print("=" * 80 + "\n")

                while True:
                    try:
                        user_input = input("\nYour question (or 'exit' to quit): ").strip()

                        if not user_input:
                            continue

                        if user_input.lower() in ["exit", "quit", "q", "done"]:
                            print("\nExiting interactive mode...")
                            print_completion_message(args.url)
                            break

                        print("\n" + "=" * 80)
                        print("CLAUDE RESPONSE:")
                        print("=" * 80 + "\n")

                        result = subprocess.run(
                            ["claude", "--print"],
                            input=user_input,
                            text=True,
                            timeout=300,
                        )

                        if result.returncode != 0:
                            print(f"\nWarning: Claude returned with code {result.returncode}")

                    except KeyboardInterrupt:
                        print("\n\nInterrupted by user. Exiting...")
                        print_completion_message(args.url)
                        break
                    except subprocess.TimeoutExpired:
                        print("\nError: Claude timed out after 5 minutes")
                    except Exception as e:
                        print(f"\nError running Claude: {e}")
            else:
                print(f"\nError: Claude failed with return code {process.returncode}")
                print(f"Prompt saved to: {prompt_temp_file_path}")
                print(f"Please manually run: claude --print < {prompt_temp_file_path}")
        except subprocess.TimeoutExpired:
            print("\nError: Claude timed out after 5 minutes")
            if 'process' in locals():
                process.kill()
        except FileNotFoundError:
            print("\nError: 'claude' command not found. Please ensure Claude Code CLI is installed.")
        except Exception as e:
            print(f"Error running Claude Code: {e}", file=sys.stderr)
            print(f"Please manually run: claude --print < {prompt_temp_file_path}")

        os.unlink(patch_file)
        return

    # Ensure repository is available
    repo_path = ensure_repository(repo_url, owner, repo, args.base_dir)
    if not repo_path:
        print("Error: Failed to ensure repository is available", file=sys.stderr)
        sys.exit(1)

    patch_applied = True
    if not args.no_apply:
        # Apply the patch
        patch_applied = apply_patch(patch_content, repo_path)
        if not patch_applied:
            print(
                "Warning: Failed to apply patch, but continuing with analysis using original patch content..."
            )

    # Analyze with Claude - pass original patch content if application failed
    if patch_applied and not args.no_apply:
        analyze_with_claude(
            repo_path, args.language, args.url, args.questions, None, existing_comments
        )
    else:
        # Use original patch content for analysis
        analyze_with_claude(
            repo_path,
            args.language,
            args.url,
            args.questions,
            patch_content,
            existing_comments,
        )


if __name__ == "__main__":
    main()
