# Desktop Entry and Launcher Integration

Read this file for any change that installs a `.desktop` entry, changes an application id,
or ships a bundled browser engine: a new package, a version bump, or a QA-only correction.
Every rule here is verified against the running application, never against the installed
file alone.

Query its registered evidence route with
`python3 scripts/source_manager.py list --capability desktop-entry-review`.

## Icon and window matching

- A window falls back to a default icon when its Wayland `app_id` or X11 `WM_CLASS` matches
  no installed desktop file's basename.
- Measure that value on a running instance instead of deriving it from the binary name,
  the desktop file, or the upstream application id. KWin reports it through
  `workspace.windowList()`.
- Resolve a mismatch by renaming the desktop file to the measured id or by adding
  `StartupWMClass` with that value.
- Toolkits derive the id differently. GTK3 sends `g_get_prgname()` rather than the
  `GApplication` id, so `org.example.App.desktop` still reports `app`.

## Bundled Chromium and ozone switches

- `--ozone-platform-hint=auto` and `--ozone-platform=wayland` are not interchangeable, and
  either one can be the failing switch. Launch the application both ways and check which
  platform the build actually took before changing it.
- A bundled Chromium silently ignores a switch its version predates. Confirm that each
  switch exists in the shipped binary.
- `wayland` is a desktop-profile default, so a `wayland?`-guarded switch also reaches X11
  users. Prefer a switch that resolves the platform at runtime over one that forces it.

## Verification

- Validate installed desktop entries with the tool the current Portage path uses. Fix the
  entry at its cause; use a path-scoped QA exception only for a verified false positive.
- Record an id that cannot be measured, an unavailable graphical session, and an untested
  architecture as unverified in the delivery report, with the behavior that remains
  unproven.
