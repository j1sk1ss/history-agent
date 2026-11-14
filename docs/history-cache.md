# History response cache

Run `docker compose -f compose.redis.yaml up -d` for a local Redis with a 256 MiB
memory limit. The port binds to loopback. Cached data is disposable; persistence
is disabled. Existing Redis deployments can use `redis://` or `rediss://` URLs.

Configure the History server group in settings or use:

```sh
python chat.py --history-redis-url redis://127.0.0.1:6379/0 \
  --history-timeout 600 --history-cache-ttl 300 \
  --history-source-cache-ttl 86400
```

The same options are available to standalone MCP clients through
`HISTORY_REDIS_URL`, `HISTORY_TIMEOUT`, `HISTORY_CACHE_TTL` and
`HISTORY_SOURCE_CACHE_TTL`. A blank Redis URL selects the local fallback.
History configuration files use the same environment-style names with string
values. Saved chat configuration stores Redis credentials privately with mode 0600.

| Response | Default lifetime | Freshness behavior |
| --- | --- | --- |
| Source, DXR, file trees and traces at concrete snapshots | 86400 seconds | Shared across sessions; snapshot and address are part of the key |
| API/checker catalogs and guide HTML | 3600 seconds | Cached per history endpoint and credential identity |
| Warning lists, concrete warning reads and project/branch metadata | 300 seconds | Generation changes invalidate responses after attempted writes |
| HEAD reads, snapshot selection, live review history, login and writes | No caching | Always contact history directly |
| Review classification vocabulary | 3600 seconds | Catalog only; actual warning review state is always checked live |

Keys hash the endpoint, credentials, action, normalized arguments and read body.
Different users, servers, snapshots, subsets and pages remain separate. Redis
stores the raw server response; each model session applies its own short names.
Only successful responses are stored. Malformed cache values are discarded.

Concurrent identical reads share one in-flight request, including across harness
processes. A short Redis lease renews while the history server computes its
answer, so a 600-second request does not lose its lease. Only its owner may
publish a result or release the lease. A crashed worker's lease expires after
30 seconds. Cached hits and unrelated reads do not wait on a global HTTP lock.
Mutable in-flight results retain their original generation, so a write cannot
republish an old response into the new generation.

Redis connections and commands time out after 0.2 seconds with no SDK retries.
If Redis fails, a circuit breaker avoids it for 30 seconds and emits one warning.
The process uses a local cache bounded to 32 MiB / 256 entries and still combines
identical in-flight reads. No old cache value substitutes for a failed live
review check. After attempted writes, mutable cache generations are invalidated
even when the HTTP outcome is unknown; immutable evidence remains reusable.

The host verifies UD status and absence of a prior review comment immediately
before saving, independently of prompts and skills. Existing classifications
are preserved even if a model requests `overwrite=true` or resumes an old job.
The server API has no conditional-write parameter, so atomic coordination with
human edits between this check and the POST requires a server-side precondition.
Uncertain writes are verified on resume and never replayed automatically.

Redis primitives follow the official [SET documentation](https://redis.io/docs/latest/commands/set/)
and [redis-py production guidance](https://redis.io/docs/latest/develop/clients/redis-py/produsage/).
