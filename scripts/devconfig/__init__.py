"""Optional local settings for developer tools, read from one declared table.

Values come, highest first, from command-line flags, the environment, a git-ignored
``.devtools.env`` at the main checkout, a user-level file of the same format, and the
declared defaults. The product never reads these settings.
"""
