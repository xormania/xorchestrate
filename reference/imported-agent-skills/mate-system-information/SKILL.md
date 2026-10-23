---
name: mate-system-information
description: Inspect installed Composer package versions when diagnosing a version-dependent API or capability.
---

Use `framework_inspect(kind="packages", package="symfony/console")` for one installed
package, or a family filter such as `package="symfony/ai-*"`. Report the installed
version and the version required by the failing API. Dependency constraints describe
allowed versions; they do not prove what is installed. Path packages may report a
development revision rather than a release. Avoid loading the complete lock file.

For PHP version, OS or loaded extensions, use `mate-php-environment-check` instead.
Package inspection is read-only; dependency changes remain part of the assigned work.
