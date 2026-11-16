# Production-readiness checklist · November 17, 2026

What AABridge still needs before a new user can pick it up and trust it.
Sorted hardest first. `[x]` is done on `claude/team-usability`; `[ ]` is open.
Each done item says how it was checked. Nothing here was run against two
physical Macs or a paid model.

## 1. Orchestration (hardest)

- [ ] **1.1 Reviews that never converge.** The leader could keep requesting
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
- [ ] **1.2 A follow-up question reran plan, build and review.** Asking “why
  didn’t you approve?” started a whole new round and replaced the task title.
  The leader now answers a question directly, the earlier result and delivery
  stay in place, and the title stays the original task.
- [ ] **1.3 Working blind.** The live panel sat at the top of the chat and
  scrolled out of view; nothing said who was working or for how long, and the
  log vanished when the task ended. Pinned bar above the composer with who,
  what, elapsed time and last update; the activity log stays after the task.
- [ ] **1.4 Branch work was hard to find.** The branch and pull request were
  shown only in the final card. A “Where the work is” strip now shows the
  branch, folder and pull request from the first build round onward, and says
  plainly when the work is still in the worker’s private copy.
- [ ] 1.5 Mirror the worker’s commits into the project branch while it builds
  (today the branch updates after each build round, not during one).
- [ ] 1.6 Post leader reviews as real GitHub reviews (approve / request
  changes) with inline comments, instead of plain pull-request comments.
- [ ] 1.7 Let the admin answer a worker’s question in place. Today a question
  ends the round and needs a follow-up.
- [ ] 1.8 One build round is capped by the 64 KB review diff. Large changes
  need chunked review rather than “review manually”.

## 2. Truthful state

- [ ] **2.1 Stale progress rows.** “Leader is planning…” stayed in the chat
  forever. These rows are no longer saved; the chat shows one live row while
  an agent works. Rows already saved are hidden when read; saved data is not
  modified or deleted.
- [ ] **2.2 “Done” when the leader did not approve.** A task finished by the
  admin, or one whose review still asked for changes, showed a green Done with
  every step ticked. It now reads “Finished, not approved”.
- [ ] **2.3 Silent message cut-off.** Only the last 40 messages were shown,
  with no hint. The window is larger and says how many earlier ones are hidden.
- [ ] 2.4 Page back through earlier messages (needs a `team_status` cursor in
  the request schema).
- [ ] 2.5 macOS notification when a task needs the admin or finishes.

## 3. First-run clarity

- [ ] **3.1 Machine lines in the chat.** `TASK_TITLE:`, `BRANCH_NAME:`,
  `NEXT:` and `VERDICT:` lines are shown as badges or hidden.
- [ ] **3.2 No explanation of how a team works.** The new-task screen opens
  with a five-step “How it works” strip.
- [ ] **3.3 Decision cards did not say why they appeared.** The card title
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
