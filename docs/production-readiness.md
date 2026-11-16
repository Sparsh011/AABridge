# Production-readiness checklist · November 17, 2026

What AABridge still needs before a new user can pick it up and trust it.
Sorted hardest first. `[x]` is done on `claude/team-usability`; `[ ]` is open.
Checked with the deterministic suite (240 tests, six new ones for 1.1, 1.2,
1.7, 2.1 and 2.4) and offscreen renders of the running, finished and new-task screens.
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
- [ ] 1.5 Mirror the worker’s commits into the project branch while it builds
  (today the branch updates after each build round, not during one).
- [ ] 1.6 Post leader reviews as real GitHub reviews (approve / request
  changes) with inline comments, instead of plain pull-request comments.
  Blocked while both agents push and review through one GitHub login: GitHub
  refuses an approve or request-changes review on your own pull request. It
  needs a second account or a GitHub App for the reviewer.
- [x] **1.7 A question from an agent ended the round.** The question now
  appears as a card; the answer resumes the same agent turn, so nothing is
  restarted. Checked against the test fixture only, not a real agent question.
- [ ] 1.8 One build round is capped by the 64 KB review diff. Large changes
  need chunked review rather than “review manually”.

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
- [ ] 3.4 “Follower” and “worker” are both used. The UI says worker; prompts
  and saved speakers still say follower.
- [ ] 3.5 Empty states for Projects and Devices that walk through setup.
- [ ] 3.6 Start AABridge at login so paired Macs reconnect after a reboot.
- [ ] 3.7 Find paired Macs by Bonjour instead of a pasted address.

## 4. Shipping

- [ ] 4.1 Developer ID signing and notarization; today the app is ad-hoc signed.
- [ ] 4.2 In-app update of the bridge service. Merging source does not update
  an installed service, which has caused stale-behaviour reports.
- [ ] 4.3 Swift UI tests. The desktop is checked by offscreen renders only.
- [ ] 4.4 A recorded two-Mac run with real models before each release.
- [ ] 4.5 Token and cost display per task and per agent.
- [ ] 4.6 VoiceOver labels and keyboard navigation for the team screens.
