# Issue 83: synthetic deployment contract — NOT DEPLOYABLE

This fixture models the privately retained, byte-matched Issue 77 installer. It
does not import that installer, run a supervisor, invoke Docker, access a host,
or establish a live rollback. The historical profile bytes are unknown: future
use requires a fresh independently captured profile reference and digest.

The observed sequence is: validate publisher, source/module pins, old profile,
Control image and health, builder state, ordered Compose inputs and environment;
render original and candidate; check terminal task records and no pending handoff;
preserve originals; stop Control; lock and recheck task records and handoffs;
stop builder; replace selected modules; test sandbox; update profile; recheck env;
start builder; recreate Control with candidate; check health, security, mounts,
network and prior task-record hashes; test sandbox. The original script compares
the complete normalized candidate configuration with the original after changing
only the image. Its `/run` override is volatile and is not a safe retention model.

On a known failure after changes, the original flow stops running components,
rechecks tasks/handoffs, restores the saved module/profile bytes, starts builder,
restores the prior image and verifies health. Newly introduced modules are removed
only if their current hashes match the reviewed candidate. It never restores an
old task ledger. A timed-out external command has an uncertain outcome and is
not automatically retried or rolled back. Signals mark interruption; they do not
justify replay. An unsuccessful rollback must remain blocked for operator review.
Same-image handling below is a **future design guard**, not a historical claim.

The JSON fixture contains synthetic values only. It binds an ordered five-layer
set, a project directory, a separate protected environment reference and digest,
both images, module/profile baselines, allowed supervisor actions and explicit
ledger/uncertainty invariants. It is not an authorization or actual host manifest.
Its lexical checks are a test contract only: ownership, no-follow file opens,
durability, Compose rendering, path identity and actual secret access must be
implemented and independently validated by a future operator.

The source-only `deployment_preflight` accepts the same `fixture-1` shape. A
trusted caller supplies a pinned Linux directory descriptor, its expected UID,
the independently reviewed exact layer order and two already-normalized JSON
configurations. It opens inputs below that descriptor with `O_NOFOLLOW`, rejects
unsafe owner/mode/link/type, verifies layer/env/profile hashes, validates the
supported in-project bind and secret paths, and permits only the exact image
change across the supplied normalized configurations. It never runs Compose,
changes host state, reads ledger contents or grants deployment permission.

The caller must protect the pinned tree and descriptor from concurrent changes;
the validator cannot freeze the filesystem or verify the ancestry of a descriptor
it did not open. In particular, **normalization provenance is unverified**: the
function cannot prove that the supplied configurations were rendered from the
checked layers. Image IDs and module hashes are shape-checked, not verified
against Docker or module files; the `fresh_baseline` flag is self-attestation,
not independent evidence. External/named volumes, external secrets, configs,
includes, `env_file` and other unsupported references are denied. Only a single
UID and private 0600 file / non-group-writable directory layout is supported;
mixed-owner live installations require independently reviewed future work.
These limits prevent treating a passing result as a live/deployment preflight.
