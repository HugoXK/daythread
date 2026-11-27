import Foundation
import SwiftUI

struct DaythreadError: LocalizedError {
    let message: String
    var errorDescription: String? { message }
}

final class DaythreadClient {
    func run(_ arguments: [String]) async throws -> String {
        try await withCheckedThrowingContinuation { continuation in
            DispatchQueue.global().async {
                let process = Process()
                let environment = ProcessInfo.processInfo.environment
                let binary = environment["DAYTHREAD_BIN"] ?? "dt"
                if binary.contains("python") {
                    process.executableURL = URL(fileURLWithPath: binary)
                    process.arguments = ["-m", "daythread"] + arguments
                } else if binary.contains("/") {
                    process.executableURL = URL(fileURLWithPath: binary)
                    process.arguments = arguments
                } else {
                    process.executableURL = URL(fileURLWithPath: "/usr/bin/env")
                    process.arguments = [binary] + arguments
                }
                let out = Pipe()
                let err = Pipe()
                process.standardOutput = out
                process.standardError = err
                do {
                    try process.run()
                    process.waitUntilExit()
                    let outData = out.fileHandleForReading.readDataToEndOfFile()
                    let errData = err.fileHandleForReading.readDataToEndOfFile()
                    if process.terminationStatus == 0 {
                        continuation.resume(returning: String(data: outData, encoding: .utf8) ?? "")
                    } else {
                        let message = String(data: errData, encoding: .utf8) ?? "daythread failed"
                        continuation.resume(
                            throwing: DaythreadError(
                                message: message.trimmingCharacters(in: .whitespacesAndNewlines)
                            )
                        )
                    }
                } catch {
                    continuation.resume(throwing: error)
                }
            }
        }
    }
}

struct RunRecord: Identifiable, Hashable {
    let id: String
    let state: String
    let tool: String
    let intent: String
}

@MainActor
final class AppModel: ObservableObject {
    @Published var today = "Loading…"
    @Published var runs: [RunRecord] = []
    @Published var facts: [String] = []
    @Published var query = ""
    @Published var status = ""

    private let client = DaythreadClient()

    func refresh() async {
        do {
            async let brief = client.run(["brief"])
            async let runJSON = client.run(["--json", "runs", "--limit", "20"])
            today = try await brief
            runs = parseRuns(try await runJSON)
            status = "Updated"
        } catch {
            status = error.localizedDescription
        }
    }

    func approve(_ id: String) async { await action("approve", id) }
    func reject(_ id: String) async { await action("reject", id) }
    func undo(_ id: String) async { await action("undo", id) }

    private func action(_ name: String, _ id: String) async {
        do {
            _ = try await client.run([name, id])
            await refresh()
        } catch {
            status = error.localizedDescription
        }
    }

    func search() async {
        guard !query.trimmingCharacters(in: .whitespaces).isEmpty else { return }
        do {
            let text = try await client.run(["fact", "search", query])
            facts = text.split(separator: "\n").map(String.init)
        } catch {
            status = error.localizedDescription
        }
    }

    private func parseRuns(_ text: String) -> [RunRecord] {
        guard let data = text.data(using: .utf8),
              let array = try? JSONSerialization.jsonObject(with: data) as? [[String: Any]] else {
            return []
        }
        return array.compactMap { item in
            guard let id = item["id"] as? String,
                  let state = item["state"] as? String,
                  let tool = item["tool"] as? String else { return nil }
            return RunRecord(
                id: id,
                state: state,
                tool: tool,
                intent: item["intent"] as? String ?? ""
            )
        }
    }
}

struct ContentView: View {
    @StateObject private var model = AppModel()
    @State private var section: Int? = 0

    var body: some View {
        NavigationSplitView {
            List(selection: $section) {
                Label("Today", systemImage: "sun.max").tag(0)
                Label("Runs", systemImage: "list.bullet.rectangle").tag(1)
                Label("Memory", systemImage: "brain").tag(2)
            }
            .navigationTitle("Daythread")
        } detail: {
            VStack(spacing: 0) {
                switch section ?? 0 {
                case 1: runsView
                case 2: memoryView
                default: todayView
                }
                Divider()
                HStack {
                    Text(model.status)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    Spacer()
                    Button("Refresh") { Task { await model.refresh() } }
                }
                .padding(8)
            }
        }
        .frame(minWidth: 900, minHeight: 620)
        .task { await model.refresh() }
    }

    private var todayView: some View {
        ScrollView {
            Text(model.today)
                .font(.system(.body, design: .monospaced))
                .textSelection(.enabled)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(20)
        }
    }

    private var runsView: some View {
        List(model.runs) { run in
            HStack(alignment: .top, spacing: 12) {
                VStack(alignment: .leading, spacing: 4) {
                    Text("\(run.tool) · \(run.state)").font(.headline)
                    Text(run.intent).font(.caption).foregroundStyle(.secondary)
                    Text(run.id).font(.caption2).foregroundStyle(.tertiary)
                }
                Spacer()
                HStack {
                    if run.state == "pending_approval" {
                        Button("Approve") {
                            Task { await model.approve(run.id) }
                        }
                        .buttonStyle(.borderedProminent)
                        Button("Reject", role: .destructive) {
                            Task { await model.reject(run.id) }
                        }
                    }
                    if run.state == "completed" {
                        Button("Undo") {
                            Task { await model.undo(run.id) }
                        }
                    }
                }
            }
            .padding(.vertical, 4)
        }
    }

    private var memoryView: some View {
        VStack(spacing: 0) {
            HStack {
                TextField("Search facts", text: $model.query)
                    .textFieldStyle(.roundedBorder)
                    .onSubmit { Task { await model.search() } }
                Button("Search") { Task { await model.search() } }
            }
            .padding()
            List(model.facts, id: \.self) { fact in
                Text(fact)
                    .font(.system(.body, design: .monospaced))
                    .textSelection(.enabled)
            }
        }
    }
}

@main
struct DaythreadApp: App {
    var body: some Scene {
        WindowGroup { ContentView() }
    }
}
