Review code changes and update documentation accordingly.

Requires an argument: a number of commits or a git range.
Examples:
  /update-docs 3             — last 3 commits (shorthand for HEAD~3)
  /update-docs 10            — last 10 commits
  /update-docs main..HEAD    — all commits ahead of main
  /update-docs abc123..HEAD  — from specific commit to HEAD

If no argument provided — ask the user to specify a range before proceeding.

1. Resolve the range:
   - If $ARGUMENTS is a plain number (e.g. "3") → use `HEAD~3`
   - Otherwise → use $ARGUMENTS as-is

2. Get the diff:
```bash
git diff <resolved range>
```

3. Get the list of changed files:
```bash
git diff --name-only <resolved range>
```

3. If the diff is empty — report "No changes found in this range, documentation is up to date."

4. Pass the full diff and file list to the technical-writer agent:
   "Analyze this diff and update the project documentation. Follow your rules for when to edit existing files vs create new ones."

5. After the agent completes, summarize:
   - Which docs were updated and what changed
   - Which docs were created and why
   - What was left unchanged
