# Cloud session setup

A fresh Claude Code Web / cloud session needs only a clone of this repository.

```bash
scripts/validate_cloud.sh              # CLOUD-SAFE: python + web jobs, mirrors CI
scripts/validate_cloud.sh --postgres   # also PG integration tests on a throwaway local cluster
scripts/validate_cloud.sh --help       # all options
```

The script provisions the CI toolchain if it is missing (uv 0.12.15 into
`~/.cache/nexora-cloud`, Node 24 via nvm), unshallows the clone for the web
baseline tests, and scrubs `NEXORA_*`, `MT5_*`, `BROKER_*` and `PG*` from its
own environment. It never starts PROD, connects to MT5 or a broker, or uses a
production DSN, journal or checkpoint.

A full run takes about 6 minutes (pytest is ~4.5 of them).
