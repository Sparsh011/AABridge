# Test audit · October 31, 2026

The first cleanup removed 14 obvious duplicate/trivial test methods from the
258-test suite. That was a narrow pass, not evidence that the remaining 244
were all necessary. The second pass reviewed the suite's assertions and used
per-test execution tracing to find overlapping production paths. It removed
19 more duplicate runs, leaving 225 test methods across the same 22 test files.
No test files are excluded from the runner; no application behavior changed.

## Second-pass removals and retained checks

| Removed standalone runs | Retained behavior checks |
| --- | --- |
| Store submission/reopen smoke test | `HistoryTests.test_question_answer_and_result_survive_revision_restart_and_retries` reopens the database and verifies the durable task conversation. |
| Store duplicate-submission smoke test | The real TLS question/answer/result transfer test replays the same submission key and checks that it returns the original task. |
| Store cross-peer status rejection and token smoke tests | Real TLS rejects another paired peer with the same project; real local IPC rejects unenrolled credentials and exercises valid authentication throughout. |
| Store event-cursor smoke test | The full local service flow now checks ordered, unique event cursors and an empty resumed page. |
| Store result-capture happy path | Real local-service and TLS result flows verify accepted edits, captured Git results and the untouched source checkout. |
| Ordinary task-pagination happy path | Shared-history pagination exercises multiple full pages, a partial final page and mixed local/peer ownership. |
| Mocked team follow-up | The real IPC/TLS team flow checks that a follow-up reuses the implementation workspace and preserves earlier work. |
| Mocked per-role model forwarding | The real team activity flow checks both requested model labels; CLI tests retain model/effort and session argument checks. |
| Standalone automatic review/fix happy path | The existing PR lifecycle test now checks the full stage sequence, transmitted findings, revised review SHA/files, branch placement and explicit merge request. |
| External-app lookup and hidden-extension helpers | Project creation selects an external app without its Finder-hidden `.app` suffix and verifies the pinned executable/hash. |
| Standalone Claude symlink lookup | The independent-agent runtime test now also checks the exact resolved Claude executable. |
| Unsupported `runtime` RPC name | Generic unknown-operation rejection already covers this same protocol whitelist; no runtime-mutation RPC exists. |
| Separate dangling-file and leaf-directory symlink cases | File-symlink rejection and a symlink ancestor with a missing leaf retain the no-follow diagnostic checks. Actual store journal and private-state link checks remain. |
| Report constant/boolean smoke test | CLI readiness checks cover successful/error reports; diagnostic collection checks that it does not enable workers or create state. |
| Standalone question-with-edits validator call | The Claude wire malformed-result test passes that same invalid proposal through the actual adapter validation path. |
| Standalone default CLI argument inspection | The existing native CLI fixture checks received arguments for absent model/effort overrides, for both agents. |

The useful distinct assertions moved into existing flows. These removals avoid
extra fixture repositories, databases and complete team cycles; they do not
just rename test cases or hide them behind a smaller default test selection.

## Why overlapping rejection tests remain

Tests for authorization/revocation, idempotency conflicts, stale revisions,
TLS certificate pinning, sandbox restrictions, filesystem preservation and
shutdown behavior retain different failure inputs or boundary checks. Many
execute the same rejection lines. That does not make their assertions redundant:
for example, a trusted peer with the wrong project and an unpaired certificate
must both be denied for different reasons.

## Verification and limits

A temporary tracer recorded production line execution in the test runner and
its threads before and after the second pass:

| Suite | Test methods | Distinct production lines executed |
| --- | ---: | ---: |
| Before second pass | 244 | 3,390 |
| After second pass | 225 | 3,390 |

Both instrumented runs passed; no previously executed production line was lost.
The ordinary required command, `.venv/bin/python scripts/check.py`, also passed
all 225 tests.
The tracer is not a repository dependency and does not instrument subprocesses.
This comparison supports the manual assertion review; it does not prove complete
branch coverage, mutation resistance or a mathematically minimal suite. There
were no paid model calls or physical two-Mac model runs.
