// orbit-calendar FROM TO: print the macOS Calendar events between two epoch seconds as JSON (read-only).
// Orbit compiles this on first use (swiftc) into <state dir>/bin/orbit-calendar.
import EventKit
import Foundation

func out(_ obj: Any) {
    let data = try! JSONSerialization.data(withJSONObject: obj, options: [])
    FileHandle.standardOutput.write(data)
    FileHandle.standardOutput.write("\n".data(using: .utf8)!)
}

let args = CommandLine.arguments
guard args.count >= 3, let from = Double(args[1]), let to = Double(args[2]) else {
    out(["error": "usage: orbit-calendar FROM TO (epoch seconds)"])
    exit(2)
}

let store = EKEventStore()
var granted = false
var why = ""
let status = EKEventStore.authorizationStatus(for: .event)
if status == .fullAccess {
    granted = true
} else if status == .denied || status == .restricted || status == .writeOnly {
    why = "denied"
} else {
    let sem = DispatchSemaphore(value: 0)
    store.requestFullAccessToEvents { ok, err in
        granted = ok
        if let err = err { why = err.localizedDescription }
        sem.signal()
    }
    if sem.wait(timeout: .now() + 120) == .timedOut { why = "no answer to the Calendar access prompt" }
}
if !granted {
    out(["error": why == "denied" || why.isEmpty
        ? "Orbit may not read your calendars. Allow it in System Settings → Privacy & Security → Calendars."
        : why, "denied": true])
    exit(0)
}

let pred = store.predicateForEvents(withStart: Date(timeIntervalSince1970: from),
                                    end: Date(timeIntervalSince1970: to), calendars: nil)
let events = store.events(matching: pred).sorted { $0.startDate < $1.startDate }
var list: [[String: Any]] = []
func statusName(_ s: EKParticipantStatus) -> String {
    switch s {
    case .accepted: return "accepted"
    case .declined: return "declined"
    case .tentative: return "tentative"
    case .pending: return "pending"
    default: return ""
    }
}
func who(_ p: EKParticipant) -> String { p.name ?? p.url.absoluteString.replacingOccurrences(of: "mailto:", with: "") }
for e in events {
    var d: [String: Any] = [
        "id": e.eventIdentifier ?? "",
        "title": e.title ?? "",
        "start": e.startDate.timeIntervalSince1970,
        "end": e.endDate.timeIntervalSince1970,
        "all_day": e.isAllDay,
        "calendar": e.calendar?.title ?? "",
        "location": e.location ?? "",
        "url": e.url?.absoluteString ?? "",
        "notes": String((e.notes ?? "").prefix(4000)),
    ]
    if let a = e.attendees {
        d["attendees"] = a.count
        // who comes (not you), every guest with their answer, and yours
        d["people"] = Array(a.filter { !$0.isCurrentUser }.map(who).prefix(40))
        d["guests"] = Array(a.prefix(60).map { ["name": who($0), "status": statusName($0.participantStatus), "me": $0.isCurrentUser] as [String: Any] })
        if let me = a.first(where: { $0.isCurrentUser }) { d["response"] = statusName(me.participantStatus) }
    }
    d["recurring"] = e.hasRecurrenceRules
    d["canceled"] = e.status == .canceled
    d["free"] = e.availability == .free
    if let o = e.organizer {
        d["organizer"] = who(o)
        d["mine"] = o.isCurrentUser
    } else {
        d["mine"] = true  // no organizer: an event of your own (a block, a reminder)
    }
    if let c = e.calendar?.cgColor, let comps = c.components, comps.count >= 3 {
        d["color"] = String(format: "#%02x%02x%02x", Int(comps[0] * 255), Int(comps[1] * 255), Int(comps[2] * 255))
    }
    list.append(d)
}
out(["events": list])
