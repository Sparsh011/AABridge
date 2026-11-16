# Production-readiness checklist · November 17, 2026

What AABridge still needs before a new user can pick it up and trust it.
Sorted hardest first. `[x]` is done on `claude/team-usability`; `[ ]` is open.
Checked with the deterministic suite (244 tests, ten new ones for 1.1, 1.2,
1.5, 1.6, 1.7, 1.8, 2.1 and 2.4) and offscreen renders of the running, finished and new-task screens.
Nothing here was run against two physical Macs or a paid model, and the
installed app and running services were not replaced. The orchestration
changes take effect only after the bridge service on the admin Mac is updated
through Settings → Set up this Mac.

## 1. Orchestration (hardest)

- [x] **1.1 Reviews that never converge.** The leader could keep requesting
  changes for style points, raise new points every round, and repeat a point
  the worker had already disputed, until the fix limit ran out.
  - Reviews separate blocking findings from suggestions; only blocking ones
    can request changes.
  - A re-review judges the earlier points and may not add new non-blocking ones.
  - The worker answers each point as fixed or disputed.
  - New verdict `NEEDS_ADMIN` for a disagreement, a product choice, or a
    blocker outside the code.
  - Stalemate detection: the worker changed no code and the leader still wants
    changes, so the admin is asked at once instead of burning more rounds.
- [x] **1.2 A follow-up question reran plan, build and review.** Asking “why
  didn’t you approve?” started a whole new round and replaced the task title.
  The leader now answers a question directly, the earlier result and delivery
  stay in place, and the title stays the original task.
- [x] **1.3 Working blind.** The live panel sat at the top of the chat and
  scrolled out of view; nothing said who was working or for how long, and the
  log vanished when the task ended. Pinned bar above the composer with who,
  what, elapsed time and last update; the activity log stays after the task.
- [x] **1.4 Branch work was hard to find.** The branch and pull request were
  shown only in the final card. A “Where the work is” strip now shows the
  branch, folder and pull request from the first build round onward, and says
  plainly when the work is still in the worker’s private copy.
- [x] **1.5 Nothing in the project while a build ran.** While a worker on
  this Mac builds, AABridge refreshes a branch `aabridge-live/<id>` in your
  project every 20 seconds with a snapshot of its unfinished files, taken
  through a separate Git index so the worker’s checkout is untouched. It is
  removed when the build step finishes and the real branch is updated. Not
  available when the worker is on another Mac, or for “Keep it in AABridge”.
- [x] **1.6 Reviews were plain pull-request comments.** The leader’s review
  is posted through GitHub’s review API as an approval or a change request,
  with its `INLINE: file:line:` findings as inline comments. GitHub refuses an
  approval or change request from the pull request’s own author, and inline
  comments outside the diff; each refusal falls back one step, ending at the
  old plain comment. With one GitHub login for both agents you will therefore
  get a comment-type review. Checked with a stubbed `gh`, not against GitHub.
- [x] **1.7 A question from an agent ended the round.** The question now
  appears as a card; the answer resumes the same agent turn, so nothing is
  restarted. Checked against the test fixture only, not a real agent question.
- [x] **1.8 Large changes could not be reviewed.** A change whose Git bundle
  passed 60,000 bytes stopped with “review it manually”. The bundle is now
  sent to the leader in parts (up to about 15 MB) and verified by SHA-256
  before the review checkout is built. Both Macs need this build; an older
  leader gets an update instruction. Linked-project diffs are still capped.

## 2. Truthful state

- [x] **2.1 Stale progress rows.** “Leader is planning…” stayed in the chat
  forever. These rows are no longer saved; the chat shows one live row while
  an agent works. Rows already saved are hidden when read; saved data is not
  modified or deleted.
- [x] **2.2 “Done” when the leader did not approve.** A task finished by the
  admin, or one whose review still asked for changes, showed a green Done with
  every step ticked. It now reads “Finished, not approved”.
- [x] **2.3 Silent message cut-off.** Only the last 40 messages were shown,
  with no hint. The window is larger and says how many earlier ones are hidden.
- [x] **2.4 Earlier messages.** “Show earlier messages” pages back through
  the whole chat (`team_status` takes an optional `before` index).
- [x] **2.5 Notifications.** A macOS notification when a task needs you, is
  blocked, fails or finishes while AABridge is in the background. Compiled
  but not seen firing: it needs the installed app and a real status change.
  It only watches while the Team tab is the selected section.

## 3. First-run clarity

- [x] **3.1 Machine lines in the chat.** `TASK_TITLE:`, `BRANCH_NAME:`,
  `NEXT:` and `VERDICT:` lines are shown as badges or hidden.
- [x] **3.2 No explanation of how a team works.** The new-task screen opens
  with a five-step “How it works” strip.
- [x] **3.3 Decision cards did not say why they appeared.** The card title
  and buttons now match the cause: fix limit, disagreement, unclear verdict.
- [x] **3.4 “Follower” and “worker”.** Every message the admin reads says
  worker. Agent prompts and saved speaker labels keep “follower” so existing
  tasks and agent sessions stay valid.
- [x] **3.5 Empty states.** Projects explains what a project is and the two
  entries a team needs; Devices says a second Mac is optional and how pairing
  works.
- [x] **3.6 Start at login.** Settings has “Open AABridge when I log in”.
  Compiled only: registering needs the installed app, and macOS may refuse an
  app outside Applications; the toggle then says what to do.
- [x] **3.7 Finding Macs.** Devices lists other Macs on the network that have
  AABridge open (Bonjour) and whether each is paired. Discovery only shows who
  is there; trust still comes from exchanging connection cards. Compiled and
  rendered empty; not seen finding a second Mac. Needs an app built with
  `scripts/build_desktop.py`, which now declares the Bonjour service.

## 4. Shipping

- [x] **4.1 Signing.** `scripts/build_desktop.py` takes `--sign-identity`
  (hardened runtime, secure timestamp) and `--notary-profile` (notarize,
  staple, Gatekeeper check), also as `AABRIDGE_SIGN_IDENTITY` and
  `AABRIDGE_NOTARY_PROFILE` for `make build`. Not exercised: this Mac has an
  Apple Development certificate only. A Developer ID Application certificate
  from a paid Apple Developer account is still needed to ship to other people.
- [x] **4.2 A stale bridge service was invisible.** The service now reports
  its version. The toolbar turns orange with “Bridge service needs an update”
  when the installed service differs from the app, and a paired Mac on a
  different version is marked in the device list. Updating is still the
  Settings → Set up this Mac button; the release is now 0.3.0.dev6 so the
  running dev5 service is detected.
- [ ] 4.3 Swift UI tests. The desktop is checked by offscreen renders only.
- [ ] 4.4 A recorded two-Mac run with real models before each release.
- [ ] 4.5 Token and cost display per task and per agent.
- [ ] 4.6 VoiceOver labels and keyboard navigation for the team screens.
