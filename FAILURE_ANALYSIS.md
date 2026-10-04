# Failure analysis foundation (Sprint 1)

QA Studio now associates optional, deterministic diagnostics with failures in
its live Selenium inspection walk and normalized performance results. The
existing runner, thresholds, continuation rules, metrics, retries, selectors,
native artifacts and reporting layout remain authoritative and unchanged.

## Architecture and scope

`failure_analysis/models.py` contains frozen dataclasses and stable string enums,
following `tracker/models.py` and `perf/models.py`. No new execution-result model
competes with `PerfResult` or the existing inspection result dictionary.

```
existing failure processing
  -> integration adapter (existing facts and artifact references)
  -> FailureCollector -> FailureAnalysisContext
  -> AnalyzerRegistry -> FailureAnalyzer.supports / analyze
  -> FailureAnalysis(context, structured results, schema_version=1)
  -> optional field in existing result / report / history
```

The package uses only the standard library and the existing local diagnostic
logger. There is no AI provider, network call, self-healing, selector modification,
retry policy, dynamic discovery, or new endpoint in this subsystem.

The registry owns explicit analyzer registration in `failure_analysis/__init__.py`.
To add an analyzer, implement `FailureAnalyzer` from `ports.py` and register an
instance once during application composition. `register()` rejects duplicate
identifiers. Registration order is deterministic; readers take a locked snapshot.
Multiple supported findings can coexist. Unsupported, broken or malformed plugins
cannot suppress valid findings from other plugins. No finding produces `UNKNOWN`.

`TimeoutAnalyzer` is the only initial analyzer. It recognizes exact, qualified
runtime exception identities (Python, Selenium, Playwright, and selected Java
timeout classes), including JMeter's explicit non-HTTP exception-code field.
It does not classify a message merely because it says "timeout", an HTTP 504,
an unresolved locator, or a breached latency threshold. Confidence 1.0 refers to
the observed category, not a root cause; probable cause is left unset.

## Data contract

- `FailureAnalysisContext`: execution/run identifiers, optional case/step identity,
  test name, failed command, typed locator, exception type/message, optional safe
  stack frames, UTC timestamp, page URL, selected runtime attributes, attempt,
  duration in milliseconds, and normalized evidence.
- `FailureEvidence`: stable per-context evidence identifier, type, source,
  timestamp, text value or artifact reference, and typed scalar attributes.
- `AnalysisResult`: category, optional probable cause/confidence, severity,
  explanation, supporting evidence identifiers, recommendations, analyzer ID/version.
- Categories: locator, timeout, assertion, navigation, network, backend,
  authentication, environment, data, unknown.

Absent facts remain absent. Inspection run IDs and case execution IDs are UUIDs.
The inspection attempt is one inspection traversal; existing internal action
fallbacks are not represented as new test attempts. Source step numbers are joined
when a compiled intent spans multiple original steps.

JMeter analysis describes the existing **failure group**, not an invented test
case or virtual-user execution. Contexts retain a caller-provided scenario ID;
otherwise case identity and attempt are absent. Each group references its first
failed CSV row, records the original timestamp/elapsed time when available,
response code, group count, and message provenance. Its execution ID is scoped
to the analysis run and group. The existing top-30 group limit is unchanged.
Malformed/missing CSV timestamps fall back to collection time. Threshold-only
and third-party aggregate failures receive run-level UNKNOWN diagnostics.

## Integration and storage

1. `engine.explore_and_map`: after `_todo` records an unresolved inspection step,
   aggregate diagnostics. Navigation, browser-dialog, popup, cart-wait and terminal
   input/frame/upload/keyboard/hover handlers pass the caught exception when available.
   Attempted locators stay in diagnostics without being restored as verified locators.
   Other unresolved steps use
   UNKNOWN instead of inventing an exception. No further action, screenshot,
   console collection, or DOM harvest is triggered by diagnostics.
2. `engine.write_inspection_screens`: write the optional `failure_analysis` field
   into the existing local `inspection-screens.json`. DOM evidence points into
   `screen_captures`, explicitly identified as the last available prior capture,
   not a newly captured failure state. Existing schema version 4 and execution
   plans stay intact; historical readers ignore the extra optional field.
3. `perf.targets.jmeter.parse_jtl`: preserve native `.jtl`, existing groups and
   all metrics; associate diagnostics using original row metadata.
4. `perf.service.run`: after existing threshold evaluation, cover threshold-only
   or third-party aggregate failures. Target exceptions/cancellation still propagate
   exactly as before; this sprint does not fabricate results for crashed processes.
5. `performance._run_worker`: serialize optional diagnostics into the existing
   capped, per-user `perf_history` dictionaries, persisted by `store.save` in the
   existing local credential vault. No cloud schema or database migration.

`FailureAnalysis.to_dict/from_dict` and `serialize_analyses/read_analyses` provide
JSON interoperability. Missing optional history fields are accepted. Additive
fields are ignored on read; unknown taxonomy values become UNKNOWN. Unsupported
future schema versions and malformed records are isolated, without hiding other
valid records. A serialization error omits the problematic diagnostic record,
never the original inspection report or performance history.

There is no automated-test-result HTTP endpoint in the current app. `run_worker.py`
and the Report screen concern title/step generation, so they were intentionally
left untouched. Generated Selenium/TestNG, Playwright and Cypress suites run
outside QA Studio; their native reporters are not replaced or automatically
ingested in Sprint 1. The shared models/registry are ready for future adapters.
CLI callers receive typed diagnostics through `PerfResult`; no new CLI output
file or sidecar persistence policy is introduced.

## Privacy and error isolation

The existing report query sanitizer is shared through `diagnostic_safety.py` with
the same report behavior. Failure evidence additionally strips URL userinfo,
all query parameters and fragments, masks known typed/login values, credential
assignments, auth headers, cookies, bearer/basic credentials, JWTs and signing
metadata. Metadata accepts scalar values only and is bounded. Stack traces retain
frames, not source lines or local variables. Binary/data-URI content is excluded;
screenshots, DOM, raw logs and request/response content require references.
Diagnostic text is limited to 4 KiB; oversized text is omitted before truncation
could expose a secret prefix. Normalization deduplicates evidence and limits
incoming evidence to 64 items and attributes to 32 per item.

No code fetches credentials, request bodies, cookies or headers for diagnostics.
The referenced **existing native artifacts** are not rewritten or audited by this
sprint. Their access, redaction and retention remain the responsibility of their
existing owners. Arbitrary natural-language text cannot be guaranteed secret-free
by regex; adapters should prefer allowlisted metadata and supply known sensitive
values rather than ingesting raw payloads. Future raw-log ingestion requires its
own reviewed redaction policy.

Collection, analyzer, adapter, serialization and logging exceptions are isolated.
The existing rotating `diag_log` sink receives structured `failure_analysis` events
with sanitized run/execution/analyzer/category identifiers; raw exception messages
are never sent to this logger. No diagnostic step determines test pass/fail.

## Verification and follow-up

Offline suite (includes pytest free-function tests, not just unittest discovery):

```
python -m pytest tests perf/tests -q
python -m tracker.contract
python -m tracker.security
python verify_ops.py
```

`tests/test_failure_analysis.py` covers typed models, normalization, privacy,
registry/contract behavior, exact timeout matching, fallback, plugin faults,
observability and schema compatibility. `tests/test_failure_analysis_integration.py`
runs the real browser-inspection method with a deterministic WebDriver boundary,
real CSV parsing, service threshold logic, inspection JSON and existing history
persistence. External browsers/load generators/UI rendering are not required.

Android staging and the manual source-sync package allowlist include the new
package. No APK/executable release or installed-app sync is performed by this
change. Pending release notes exist in all seven languages; VERSION is unchanged.

Deferred: generated-suite result ingestion; per-sample/per-case identity mapping
for load tests; rich failure presentation; legacy artifact privacy/retention audit;
independently reviewed analyzers for additional failure categories. Inspect existing
second-resolution artifact/history IDs, lossy aggregation, and process-exit handling
before extending persistence. None of these adjacent mechanisms is refactored here.
