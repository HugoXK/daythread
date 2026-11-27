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
    @Published var status = "Connecting"

    private let client = DaythreadClient()

    func refresh() async {
        do {
            async let brief = client.run(["brief"])
            async let runJSON = client.run(["--json", "runs", "--limit", "30"])
            today = try await brief
            runs = parseRuns(try await runJSON)
            status = "Online"
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
            return RunRecord(id: id, state: state, tool: tool, intent: item["intent"] as? String ?? "")
        }
    }
}

enum Palette {
    static let background = LinearGradient(
        colors: [Color(red: 0.027, green: 0.039, blue: 0.067), Color(red: 0.043, green: 0.063, blue: 0.106)],
        startPoint: .top,
        endPoint: .bottom
    )
    static let warm = Color(red: 1.0, green: 0.722, blue: 0.420)
    static let warm2 = Color(red: 1.0, green: 0.557, blue: 0.447)
    static let cool = Color(red: 0.478, green: 0.635, blue: 1.0)
    static let cool2 = Color(red: 0.698, green: 0.549, blue: 1.0)
    static let good = Color(red: 0.357, green: 0.839, blue: 0.635)
    static let warn = Color(red: 1.0, green: 0.769, blue: 0.420)
    static let bad = Color(red: 1.0, green: 0.478, blue: 0.565)
    static let surface = Color.white.opacity(0.055)
    static let border = Color.white.opacity(0.09)
}

struct BrandMark: View {
    var body: some View {
        GeometryReader { geo in
            let w = geo.size.width
            let h = geo.size.height
            ZStack {
                RoundedRectangle(cornerRadius: w * 0.235, style: .continuous)
                    .fill(
                        LinearGradient(
                            colors: [Color(red: 0.039, green: 0.063, blue: 0.125), Color(red: 0.11, green: 0.165, blue: 0.28)],
                            startPoint: .topLeading,
                            endPoint: .bottomTrailing
                        )
                    )
                Path { path in
                    path.move(to: CGPoint(x: w * 0.20, y: h * 0.66))
                    path.addLine(to: CGPoint(x: w * 0.80, y: h * 0.66))
                }
                .stroke(Color.white.opacity(0.14), style: StrokeStyle(lineWidth: 1, lineCap: .round))
                Path { path in
                    path.move(to: CGPoint(x: w * 0.235, y: h * 0.66))
                    path.addQuadCurve(
                        to: CGPoint(x: w * 0.765, y: h * 0.66),
                        control: CGPoint(x: w * 0.5, y: h * 0.17)
                    )
                }
                .stroke(
                    LinearGradient(colors: [Palette.warm, Palette.cool], startPoint: .leading, endPoint: .trailing),
                    style: StrokeStyle(lineWidth: w * 0.05, lineCap: .round)
                )
                Circle()
                    .fill(LinearGradient(colors: [Color(red: 1.0, green: 0.949, blue: 0.84), Palette.warm], startPoint: .top, endPoint: .bottom))
                    .frame(width: w * 0.13, height: w * 0.13)
                    .position(x: w * 0.5, y: h * 0.39)
                Circle().fill(Palette.warm).frame(width: w * 0.035).position(x: w * 0.235, y: h * 0.66)
                Circle().fill(Palette.cool).frame(width: w * 0.035).position(x: w * 0.765, y: h * 0.66)
            }
        }
    }
}

struct Card<Content: View>: View {
    let title: String
    @ViewBuilder var content: () -> Content

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(title.uppercased())
                .font(.system(size: 10, weight: .semibold))
                .tracking(1.6)
                .foregroundStyle(.secondary)
            content()
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(16)
        .background(
            RoundedRectangle(cornerRadius: 18, style: .continuous)
                .fill(Palette.surface)
        )
        .overlay(
            RoundedRectangle(cornerRadius: 18, style: .continuous)
                .stroke(Palette.border, lineWidth: 1)
        )
    }
}

struct StateBadge: View {
    let state: String

    private var color: Color {
        switch state {
        case "completed": return Palette.good
        case "pending_approval": return Palette.warn
        case "failed", "denied", "rejected": return Palette.bad
        case "running": return Palette.cool
        default: return .secondary
        }
    }

    var body: some View {
        HStack(spacing: 5) {
            Circle().fill(color).frame(width: 5, height: 5)
            Text(state.replacingOccurrences(of: "_", with: " "))
                .font(.system(size: 10, weight: .semibold))
        }
        .foregroundStyle(color)
        .padding(.horizontal, 8)
        .padding(.vertical, 3)
        .background(Capsule().fill(color.opacity(0.10)))
        .overlay(Capsule().stroke(color.opacity(0.25), lineWidth: 1))
    }
}

struct ContentView: View {
    @StateObject private var model = AppModel()
    @State private var section: Int? = 0

    var body: some View {
        ZStack {
            Palette.background.ignoresSafeArea()
            RadialGradient(
                colors: [Palette.warm.opacity(0.10), .clear],
                center: .topLeading,
                startRadius: 0,
                endRadius: 700
            ).ignoresSafeArea()
            NavigationSplitView {
                sidebar
            } detail: {
                VStack(spacing: 0) {
                    header
                    Divider().overlay(Palette.border)
                    Group {
                        switch section ?? 0 {
                        case 1: runsView
                        case 2: memoryView
                        default: todayView
                        }
                    }
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                    Divider().overlay(Palette.border)
                    footer
                }
                .background(Color.clear)
            }
            .background(Color.clear)
        }
        .frame(minWidth: 960, minHeight: 660)
        .task { await model.refresh() }
    }

    private var sidebar: some View {
        VStack(alignment: .leading, spacing: 18) {
            HStack(spacing: 11) {
                BrandMark().frame(width: 42, height: 42)
                VStack(alignment: .leading, spacing: 1) {
                    Text("Daythread").font(.system(size: 17, weight: .semibold))
                    Text("朝夕").font(.system(size: 10, weight: .medium)).tracking(2).foregroundStyle(.secondary)
                }
            }
            .padding(.horizontal, 12)
            .padding(.top, 10)

            List(selection: $section) {
                Label("Today", systemImage: "sun.max").tag(0)
                Label("Runs", systemImage: "list.bullet.rectangle").tag(1)
                Label("Memory", systemImage: "brain").tag(2)
            }
            .listStyle(.sidebar)
            .scrollContentBackground(.hidden)

            Spacer(minLength: 0)
        }
        .frame(minWidth: 220)
        .background(.ultraThinMaterial)
    }

    private var header: some View {
        HStack(alignment: .top) {
            VStack(alignment: .leading, spacing: 4) {
                Text(title).font(.system(size: 30, weight: .semibold, design: .rounded))
                Text(subtitle).font(.system(size: 13)).foregroundStyle(.secondary)
            }
            Spacer()
            Button {
                Task { await model.refresh() }
            } label: {
                Label("Refresh", systemImage: "arrow.clockwise")
            }
            .buttonStyle(.bordered)
        }
        .padding(.horizontal, 28)
        .padding(.vertical, 22)
    }

    private var title: String {
        switch section ?? 0 {
        case 1: return "Runs"
        case 2: return "Memory"
        default: return "Today"
        }
    }

    private var subtitle: String {
        switch section ?? 0 {
        case 1: return "Every action, approval, and undo."
        case 2: return "Facts you can read, edit, and forget."
        default: return "A thread from dawn to dusk."
        }
    }

    private var todayView: some View {
        ScrollView {
            VStack(spacing: 16) {
                VStack(alignment: .leading, spacing: 8) {
                    Text("MORNING BRIEF")
                        .font(.system(size: 10, weight: .semibold)).tracking(2)
                        .foregroundStyle(Palette.warm)
                    Text(greeting)
                        .font(.system(size: 30, weight: .semibold, design: .rounded))
                    Text("What changed, what matters, and what needs you.")
                        .foregroundStyle(.secondary)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(22)
                .background(
                    RoundedRectangle(cornerRadius: 22, style: .continuous)
                        .fill(
                            LinearGradient(
                                colors: [Palette.warm.opacity(0.12), Palette.cool.opacity(0.08), Palette.surface],
                                startPoint: .topLeading,
                                endPoint: .bottomTrailing
                            )
                        )
                )
                .overlay(
                    RoundedRectangle(cornerRadius: 22, style: .continuous)
                        .stroke(Palette.border, lineWidth: 1)
                )

                HStack(alignment: .top, spacing: 16) {
                    Card(title: "Brief") {
                        Text(model.today)
                            .font(.system(size: 12, design: .monospaced))
                            .textSelection(.enabled)
                            .frame(maxWidth: .infinity, alignment: .leading)
                    }
                    Card(title: "Status") {
                        Text(model.status)
                            .font(.system(size: 13))
                            .foregroundStyle(.secondary)
                        Text("Approvals are explicit. External actions stop for confirmation.")
                            .font(.system(size: 12))
                            .foregroundStyle(.tertiary)
                    }
                    .frame(width: 260)
                }
            }
            .padding(28)
        }
    }

    private var runsView: some View {
        ScrollView {
            VStack(spacing: 10) {
                ForEach(model.runs) { run in
                    HStack(alignment: .top, spacing: 12) {
                        VStack(alignment: .leading, spacing: 5) {
                            HStack(spacing: 8) {
                                StateBadge(state: run.state)
                                Text(run.tool).font(.system(size: 13, weight: .semibold, design: .monospaced))
                            }
                            Text(run.intent).font(.system(size: 12)).foregroundStyle(.secondary)
                            Text(run.id).font(.system(size: 10, design: .monospaced)).foregroundStyle(.tertiary)
                        }
                        Spacer()
                        HStack(spacing: 6) {
                            if run.state == "pending_approval" {
                                Button("Approve") { Task { await model.approve(run.id) } }
                                    .buttonStyle(.borderedProminent)
                                Button("Reject", role: .destructive) { Task { await model.reject(run.id) } }
                                    .buttonStyle(.bordered)
                            }
                            if run.state == "completed" {
                                Button("Undo") { Task { await model.undo(run.id) } }
                                    .buttonStyle(.bordered)
                            }
                        }
                    }
                    .padding(14)
                    .background(RoundedRectangle(cornerRadius: 14).fill(Palette.surface))
                    .overlay(RoundedRectangle(cornerRadius: 14).stroke(Palette.border, lineWidth: 1))
                }
                if model.runs.isEmpty {
                    Text("No runs yet.")
                        .foregroundStyle(.secondary)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(20)
                }
            }
            .padding(28)
        }
    }

    private var memoryView: some View {
        VStack(spacing: 14) {
            HStack {
                TextField("Search people, projects, preferences…", text: $model.query)
                    .textFieldStyle(.roundedBorder)
                    .onSubmit { Task { await model.search() } }
                Button("Search") { Task { await model.search() } }
                    .buttonStyle(.borderedProminent)
            }
            ScrollView {
                VStack(spacing: 8) {
                    ForEach(model.facts, id: \.self) { fact in
                        Text(fact)
                            .font(.system(size: 12, design: .monospaced))
                            .textSelection(.enabled)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .padding(12)
                            .background(RoundedRectangle(cornerRadius: 12).fill(Palette.surface))
                            .overlay(RoundedRectangle(cornerRadius: 12).stroke(Palette.border, lineWidth: 1))
                    }
                    if model.facts.isEmpty {
                        Text("Search your living memory.")
                            .foregroundStyle(.secondary)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .padding(12)
                    }
                }
            }
        }
        .padding(28)
    }

    private var footer: some View {
        HStack {
            HStack(spacing: 6) {
                Circle().fill(model.status == "Online" ? Palette.good : Palette.bad).frame(width: 6, height: 6)
                Text(model.status).font(.system(size: 11)).foregroundStyle(.secondary)
            }
            Spacer()
            Text("Daythread · 朝夕").font(.system(size: 10)).tracking(1).foregroundStyle(.tertiary)
        }
        .padding(.horizontal, 20)
        .padding(.vertical, 8)
    }

    private var greeting: String {
        let hour = Calendar.current.component(.hour, from: Date())
        if hour < 5 { return "Still up?" }
        if hour < 12 { return "Good morning." }
        if hour < 18 { return "Good afternoon." }
        return "Good evening."
    }
}

@main
struct DaythreadApp: App {
    var body: some Scene {
        WindowGroup { ContentView() }
    }
}
