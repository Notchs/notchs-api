#!/usr/bin/env python3
"""Claude Code PreToolUse hook for Bash (see .claude/settings.json and CLAUDE.md section 1).

Blocks, with a message Claude sees:
- committing, merging, rebasing, cherry-picking or reverting while on main/development
- pushing to main/development, whether named or implied by the current branch
- force pushes, --all/--mirror pushes, and deleting main/development
- merging pull requests and changing the repository (gh pr merge, gh repo edit/delete/...)

Exit 0 lets the command run; exit 2 blocks it and shows stderr to Claude.
"""

import json
import re
import subprocess
import sys

PROTECTED = {"main", "development"}
COMMITTING = {"commit", "merge", "rebase", "cherry-pick", "revert", "am"}
ALLOWED_FLAGS = {"--abort", "--continue", "--quit", "--skip"}
FORCE = re.compile(r"^(-f|--force|--force-with-lease|--force-if-includes)")
HOW = (
    "Work on your own branch (feature/<name>-module or fix/<name>) and open a PR into "
    "development; a teammate other than the author reviews it before merging."
)


def block(reason):
    sys.stderr.write(f"Blocked by Notchs team rules (CLAUDE.md section 1): {reason}. {HOW}\n")
    sys.exit(2)


def current_branch(directory):
    try:
        out = subprocess.run(
            ["git", "-C", directory, "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return out.stdout.strip()
    except Exception:
        return ""


def branch_name(ref, current):
    """refs/heads/main -> main; HEAD -> the current branch."""
    name = re.sub(r"^refs/heads/", "", ref)
    return current if name == "HEAD" else name


def check_git(words, cwd):
    # Skip global options such as `-C <dir>` and `-c key=value` before the subcommand.
    directory = cwd
    i = 1
    while i < len(words) and words[i].startswith("-"):
        if words[i] == "-C" and i + 1 < len(words):
            directory = words[i + 1]
        i += 2 if words[i] in ("-C", "-c") else 1
    if i >= len(words):
        return
    sub, args = words[i], words[i + 1 :]
    branch = current_branch(directory)

    if sub in COMMITTING and branch in PROTECTED:
        if any(arg in ALLOWED_FLAGS for arg in args):
            return
        block(f'"git {sub}" while on {branch}')

    if sub == "push":
        flags = [a for a in args if a.startswith("-")]
        positional = [a for a in args if not a.startswith("-")]
        if any(FORCE.match(f) for f in flags):
            block("force push")
        if any(f in ("--all", "--mirror") for f in flags):
            block(f'"git push {" ".join(flags)}"')
        deleting = any(f in ("-d", "--delete") for f in flags)

        refspecs = positional[1:]  # the first one is the remote
        if not refspecs:
            if branch in PROTECTED:
                block(f"pushing while on {branch}")
            return
        for spec in refspecs:
            if spec.startswith("+"):
                block("force push (+refspec)")
            target = branch_name(spec.split(":")[1] if ":" in spec else spec, branch)
            if target in PROTECTED:
                block(f"{'deleting' if deleting else 'pushing to'} {target}")


def check_gh(words):
    area = words[1] if len(words) > 1 else ""
    action = words[2] if len(words) > 2 else ""
    if area == "pr" and action == "merge":
        block("merging a pull request")
    if area == "repo" and action in ("edit", "delete", "rename", "archive", "unarchive"):
        block(f'"gh repo {action}"')


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        sys.exit(0)
    command = (payload.get("tool_input") or {}).get("command") or ""
    cwd = payload.get("cwd") or "."
    if not re.search(r"\b(git|gh)\b", command):
        sys.exit(0)

    # Check every part of chained commands: a && b; c | d
    for part in re.split(r"&&|\|\||;|\||\n", command):
        words = part.split()
        while words and re.match(r"^[A-Z_][A-Z0-9_]*=", words[0]):  # FOO=bar git ...
            words.pop(0)
        if words and words[0] == "git":
            check_git(words, cwd)
        if words and words[0] == "gh":
            check_gh(words)
    sys.exit(0)


if __name__ == "__main__":
    main()
