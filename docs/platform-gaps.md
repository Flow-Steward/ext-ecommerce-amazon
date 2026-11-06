# Platform gaps found while building the logistics methods

Two gaps in Flow Steward Core affect personal data in this extension's results,
and one builder behaviour was observed during the UI audit. None blocks the methods,
and none is worked around in the extension:
the fix belongs in Core, and each is bounded. They were found by reading Core at
`claude/amazon-spapi-extension` (6eaec9c..d09bb7e); they are reported here, not
fixed.

## 1. Step results are stored without name or address redaction

**Observed in code.**
- PII Guard (`core/infrastructure/security/scanners/pii.py`) detects e-mail,
  phone numbers, SSN, card numbers, IP addresses and labelled person names. It has
  no postal-address detector, and a JSON pair such as `"name": "Jane Doe"` does
  not match its `name:` label pattern.
- The step message is scanned before it is written to `platform_agent_messages`
  (`message_bus.py`), but the run's LangGraph checkpoint keeps the original,
  unscanned result (`nodes_step_dispatch.py`, `checkpoint_*` tables).
- An extension cannot mark an operation or output as personal data:
  `extension_contract_v2` has no such field, and a custom schema keyword is
  refused at install.

**Impact here.** `get_order_buyer_info`, `fba_get_inbound_plan`, `fba_get_shipment`
and `fba_list_inbound_plans` return names, addresses, phone numbers and e-mail
addresses; settlement and order reports hold them in files. Step inputs with a
ship-from address or a contact (`get_shipping_rates`, `fba_create_inbound_plan`,
transportation contacts) are stored with the run as well. Phone and e-mail are pseudonymised
in step messages; names and addresses are stored and shown as returned. Everything
is kept until the job is removed by retention (`FS_RETENTION_MESSAGES_DAYS` and
`FS_RETENTION_TERMINAL_JOB_CHECKPOINTS_DAYS`, 30 days by default, adjustable per
account). The Operation matrix marks these methods as personal data.

**Bounded fix (Core).** A per-output personal-data marker in the operation
manifest, honoured when the result is persisted (checkpoint and message) and
when it is put into model context. Needs an ADR; no extension change is needed
beyond setting the marker.

## 2. File previews copy the first 256 characters of every stored file

**Observed in code.** When an extension output file is finalized, Core stores
`preview_text` = the first 256 bytes decoded as text
(`artifact_plane_values.py`, `artifact_dataset_plane_v2.py`), and that metadata
travels in `produced_artifacts` into the step message and the checkpoint.

**Impact here.** A ZPL shipping label is plain text; its first 256 characters can
contain the buyer's name and address. PDF and PNG labels start with binary
headers and are not affected in practice.

**Bounded fix (Core).** Skip `preview_text` for outputs whose declared upload type
is `application/octet-stream` (the type this extension uses for documents), or
let an artifact policy opt out of previews.

## 3. Observation: the builder keeps mappings when the action changes

**Observed on the stand (independent UI audit, 2026-11-06).** After choosing
`get_shipping_rates`, mapping `ship_from` and `amazon_order_id`, and then switching
the same step to `fba_get_item_eligibility_preview`, the builder kept the old
parameter mappings although the new action has no such inputs.

**Impact here.** None at run time for correctly built workflows: the extension
refuses unknown inputs (`invalid_payload`, nothing sent to Amazon). It is a
builder usability issue: a stale mapping shows up as an error only when the step
runs. Not investigated further and not part of this task; reported for Core.

