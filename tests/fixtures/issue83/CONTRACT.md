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
