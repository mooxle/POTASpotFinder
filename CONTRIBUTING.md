# Contributing to POTA Finder

Thanks for your interest in contributing! This is a small personal tool —
contributions are welcome as long as they keep the project simple and respect
the fair-use spirit of the APIs it relies on.

## What's welcome

- Bug fixes
- Support for additional GeoJSON geometry types
- Improvements to the POTA score algorithm (with reasoning)
- Additional OSM amenity categories relevant to POTA activations
- Better elevation provider support (e.g. self-hosted endpoints)
- Documentation improvements

## What doesn't fit

- Features that would encourage bulk/automated querying of public APIs
- Dependencies beyond the Python standard library + `requests`
- Breaking changes to the `find_by_elevation` / `find_by_score` Python API signatures

## How to contribute

1. Fork the repo and create a feature branch
2. Make your changes — keep them focused and minimal
3. Test against a real GeoJSON park boundary
4. Open a pull request with a clear description of what changed and why

## API fair use

Any contribution must continue to respect the rate limits and intended use
described in `DISCLAIMER.md`. PRs that remove or weaken rate limiting,
caching, or attribution will not be merged.

## Code style

- No external dependencies beyond `requests`
- All user-facing output in English
- Comments in English
- New public functions need a docstring with Args / Returns

## 73

*de DA6MAX*
