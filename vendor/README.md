# vendor

Files bundled into the exe, fetched by `build.py` instead of being kept in the repo:

- `UE4SS_v3.0.1-1176-g1ba38db4.zip`: [UE4SS](https://github.com/UE4SS-RE/RE-UE4SS) mod loader (MIT licence,
  included in the zip). Downloaded from its GitHub release and checked against a fixed SHA-256.

Optional: put a copy of the community item list as `items.json` here to bundle it, so the library build
doesn't need the internet. Only do that if you have permission to redistribute it.
