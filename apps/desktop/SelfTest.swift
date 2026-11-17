import Foundation
import SwiftUI

/// Checks the display rules the Team screens depend on, and that this app can
/// decode what the bridge service really returns. Run by `scripts/verify_ui.py`
/// as `AABridge --self-test <fixture.json>`; needs no window, service or model.
func runSelfTest(fixture path: String?) -> Bool {
    var failures: [String] = []
    var count = 0
    func check(_ ok: Bool, _ name: String) { count += 1; if !ok { failures.append(name) } }
    func room(_ json: String) -> TeamRoom? { try? JSONDecoder().decode(TeamRoom.self, from: Data(json.utf8)) }
    let member = #"{"target":"local","project":"app","agent":"codex"}"#
    func base(_ extra: String) -> String { #"{"id":"team-1","objective":"Why?","status":"completed","stage":"review","leader":\#(member),"follower":\#(member)\#(extra)}"# }

    // Status wording: "Done" only for approved work.
    let approved = room(base(#","approval":"auto","verdict":"approved","title":"Fix weights""#))
    check(approved.map { TeamStatus(room: $0).text } == "Done", "approved task reads Done")
    check(approved?.displayTitle == "Fix weights", "title is the first request, not the follow-up")
    let rejected = room(base(#","approval":"auto","verdict":"changes""#))
    check(rejected?.unapproved == true && rejected.map { TeamStatus(room: $0).text } == "Finished, not approved", "unapproved task is not Done")
    check(rejected.map { TeamProgress(room: $0).spoken.contains("review not approved") } == true, "progress says review not approved")
    let answered = room(base(#","approval":"auto","answered":true"#))
    check(answered?.onlyAnswered == true && answered.map { TeamStatus(room: $0).text } == "Answered", "answer-only task reads Answered")
    let answeredApproved = room(base(#","approval":"auto","answered":true,"verdict":"approved""#))
    check(answeredApproved?.unapproved == false && answeredApproved?.onlyAnswered == false, "answer after approval keeps Done")
    let legacy = room(base(""))
    check(legacy?.unapproved == false && legacy?.displayTitle == "Why?", "task from an older service still decodes and reads Done")
    check(TeamStatus(status: "awaiting_admin", stage: "plan").text == "Needs you" && TeamStatus(status: "running", stage: "work").text == "Building", "running and waiting wording")

    // Marker lines never reach the reader.
    let shown = TeamMessageRow.readable("TASK_TITLE: Fix it\nReal text\n  BRANCH_NAME: fix-it\nNEXT: BUILD\n**VERDICT: APPROVED**")
    check(shown == "Real text", "marker lines are hidden, got: \(shown)")
    check(TeamMessageRow.readable("The verdict: fine.\nINLINE: a.py:3: check") == "The verdict: fine.\nINLINE: a.py:3: check", "ordinary lines stay")
    check(TeamMessageRow.linked("See https://github.com/o/r/pull/7 and file:///etc/hosts").runs.filter { $0.link != nil }.count == 1, "only https links are clickable")

    // Small formatters.
    check(elapsedText(42) == "42s" && elapsedText(185) == "3m 05s" && elapsedText(4320) == "1h 12m" && elapsedText(-5) == "0s", "elapsed time text")
    check(TeamUsage.short(842) == "842" && TeamUsage.short(12_340) == "12.3k" && TeamUsage.short(1_200_000) == "1.2M", "token count text")
    check(AgentModelSettings.validModel("gpt-6.1-sol") && !AgentModelSettings.validModel("opus --dangerous"), "model IDs reject options")

    // Stale-service detection.
    func report(_ app: String?, _ service: String?) -> DeviceReport {
        DeviceReport(service: true, listening: true, service_error: nil, devices: [], app_release: app, service_release: service)
    }
    check(report("0.3.0.dev6", "0.3.0.dev5").serviceOutdated && report("0.3.0.dev6", "older").serviceOutdated, "older service is flagged")
    check(!report("0.3.0.dev6", "0.3.0.dev6").serviceOutdated && !report("source", "0.3.0.dev5").serviceOutdated && !report(nil, nil).serviceOutdated, "same or unknown version is not flagged")

    // Contract: everything the real service produced must decode, with the fields the screens read.
    if let path = path {
        struct Fixture: Decodable { let rooms: [String: TeamRoom]; let list: [TeamRoom]; let devices: DeviceReport; let activity: LiveActivityPage }
        do {
            let fixture = try JSONDecoder().decode(Fixture.self, from: Data(contentsOf: URL(fileURLWithPath: path)))
            check(fixture.rooms.count >= 5 && !fixture.list.isEmpty, "fixture has every room state")
            check(fixture.rooms["question"]?.gate?.kind == "question" && fixture.rooms["question"]?.gate?.reason.isEmpty == false, "question gate decodes")
            check(fixture.rooms["dispute"]?.gate?.cause == "dispute", "dispute gate cause decodes")
            check(fixture.rooms["unapproved"]?.unapproved == true && fixture.rooms["unapproved"]?.result != nil, "unapproved room keeps its result")
            check(fixture.rooms["answered"]?.answered == true && fixture.rooms["answered"]?.verdict == "approved", "answered room keeps its verdict")
            check(fixture.rooms["building"]?.preview?.branch?.hasPrefix("aabridge-live/") == true && (fixture.rooms["building"]?.usage?["leader"]?.total ?? 0) > 0, "live branch and token totals decode")
            check(fixture.rooms["building"]?.live?.started_at != nil, "live state carries its start time")
            check(fixture.rooms["delivered"]?.publication?.pull_request != nil && fixture.rooms["delivered"]?.publication?.branch != nil, "delivery decodes")
            check((fixture.rooms["long"]?.earlier_messages ?? 0) > 0, "hidden message count decodes")
            check(fixture.list.allSatisfy { !$0.displayTitle.isEmpty }, "task list rows have titles")
            check(fixture.rooms["mismatch"]?.error_code == "baseline_mismatch" && fixture.rooms["mismatch"]?.busy_role == "leader", "commit mismatch and busy role decode")
            check(fixture.rooms["dispute"]?.busy_role == nil && fixture.rooms["building"]?.busy_role == "follower", "idle agents are reported at a decision and during a build")
            check(fixture.devices.app_release != nil && fixture.activity.entries.first?.role == "leader", "device report and activity page decode")
        } catch { check(false, "service output did not decode: \(error)") }
    }
    for failure in failures { print("FAILED: " + failure) }
    print("\(count - failures.count) of \(count) desktop checks passed" + (path == nil ? " (no service fixture given)" : ""))
    return failures.isEmpty
}
