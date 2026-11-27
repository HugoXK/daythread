// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "Daythread",
    platforms: [.macOS(.v13)],
    targets: [
        .executableTarget(
            name: "Daythread",
            path: "Sources/Daythread"
        )
    ]
)
