# Release Publishing

This repository publishes GitHub Releases from version tags.

## Normal release

1. Update `pyproject.toml` so `[project].version` is the version you want to publish.
2. Commit the version change.
3. Create and push an annotated tag that matches the package version:

   ```powershell
   git tag -a v0.1.0 -m "v0.1.0"
   git push origin main
   git push origin v0.1.0
   ```

4. GitHub Actions runs `.github/workflows/release.yml`.

The workflow checks that the tag matches `pyproject.toml`, runs the unit tests, builds the wheel and source distribution, builds a Windows single-file GUI executable, creates `SHA256SUMS.txt`, and publishes a GitHub Release with those files attached.

## Manual rerun

If a tag already exists and you need to publish or retry its release, open GitHub Actions, run the `Release` workflow manually, and enter the existing tag name such as `v0.1.0`.

## Local Windows exe build

To build the same one-file GUI executable locally on Windows:

```powershell
python -m pip install -e ".[gui,exe]"
.\scripts\build_windows_exe.ps1
```

The executable is written to `dist/exe/moz-game-translator.exe`.

## Notes

- Tags must use the `vX.Y.Z` form and match the package version exactly.
- The workflow uses the repository `GITHUB_TOKEN`, so no extra secret is needed for GitHub Releases.
- Local provider files such as `configs/myproviders.json` are intentionally ignored and should not be included in releases.
