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

The source-only `compose_render.render_preflight` adds a narrow normalization
path: the caller independently pins descriptors and SHA-256 values for Bubblewrap
and a standalone Compose executable, an exact Compose version and project name.
It copies Compose and verified ordered layer/env bytes into sealed Linux memfds.
Bubblewrap is hash-pinned and executed by a root-owned, non-writable, no-symlink
path whose identity is checked before and after each call, so Linux pathname LSM
policy remains effective; the caller must be unprivileged. It launches Compose
inside a no-network, no-host-mount Bubblewrap namespace;
and renders layers 1–4 as original and 1–5 as candidate with explicit project
directory, project name and env file. It rechecks inputs and invokes the existing
image-only preflight. The returned receipt names the input-manifest and tool pins;
normalized data and protected environment values are not returned.

This deliberately supports only the limited fixture service/secret keys and
rejects Compose includes, extends, service env files, custom YAML tags/aliases,
external references and unrecognized keys. The input tree must remain protected
against same-UID mutation after this check and throughout any future action.
Caller-pinned tool descriptors, their trusted digests, Linux kernel/Bubblewrap,
the executable's host dynamic loader (for Bubblewrap), and the independent
layer-order/project-name/version policy and host root are trusted boundaries, not established
by this function. The standalone Compose must run in the minimal isolated root.
The narrow preflight only validates supported in-project bind/secret references;
it does not snapshot secret content or validate image/module identity or profile
freshness. Mixed ownership remains unsupported. A passing receipt is not
deployment authorization, evidence of the actual host configuration, or proof
that subsequent inputs remain unchanged.
