Review recent code changes and update documentation accordingly.

1. Get the diff of recent changes:
```bash
git diff HEAD~1
```

2. Also get the list of changed files:
```bash
git diff HEAD~1 --name-only
```

3. Pass the full diff to the docs-updater agent with this instruction:
   "Analyze this diff and update the project documentation. Follow your rules for when to edit existing files vs create new ones."

4. After the agent completes, summarize what was changed.
