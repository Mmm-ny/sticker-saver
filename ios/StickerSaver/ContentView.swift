import SwiftUI

struct ContentView: View {
    @State private var query = "哈哈"
    @State private var stickers: [Sticker] = []
    @State private var isLoading = false
    @State private var status = "搜索相似动图，保存到手机相册"
    @State private var recentSaves: [String] = []

    private let columns = [
        GridItem(.adaptive(minimum: 150), spacing: 12)
    ]

    var body: some View {
        NavigationStack {
            VStack(spacing: 12) {
                searchBar
                statusBar
                ScrollView {
                    VStack(alignment: .leading, spacing: 16) {
                        recentSection
                        resultGrid
                    }
                    .padding(.horizontal, 16)
                    .padding(.bottom, 24)
                }
            }
            .navigationTitle("表情保存器")
            .task {
                loadRecent()
                await search(silent: true)
            }
        }
    }

    private var searchBar: some View {
        HStack(spacing: 8) {
            TextField("输入关键词，如 哈哈、无语、谢谢", text: $query)
                .textFieldStyle(.roundedBorder)
                .submitLabel(.search)
                .onSubmit {
                    Task { await search(silent: false) }
                }

            Button("搜索") {
                Task { await search(silent: false) }
            }
            .buttonStyle(.borderedProminent)
            .disabled(isLoading)
        }
        .padding(.horizontal, 16)
        .padding(.top, 8)
    }

    private var statusBar: some View {
        HStack {
            if isLoading {
                ProgressView()
            }
            Text(status)
                .font(.footnote)
                .foregroundStyle(.secondary)
            Spacer()
        }
        .padding(.horizontal, 16)
    }

    private var recentSection: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("最近保存")
                .font(.headline)
            if recentSaves.isEmpty {
                Text("还没有保存记录")
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
            } else {
                ForEach(recentSaves, id: \.self) { item in
                    Text(item)
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private var resultGrid: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("搜索结果")
                .font(.headline)
            LazyVGrid(columns: columns, spacing: 12) {
                ForEach(stickers) { sticker in
                    StickerCard(sticker: sticker) {
                        await save(sticker)
                    }
                }
            }
        }
    }

    private func search(silent: Bool) async {
        isLoading = true
        status = silent ? "正在加载热门表情..." : "正在搜索..."
        do {
            let results = try await StickerAPI.shared.search(query: query)
            stickers = results
            status = results.isEmpty ? "没有找到结果，换个关键词试试" : "找到 \(results.count) 个表情"
        } catch {
            status = "搜索失败：\(error.localizedDescription)"
        }
        isLoading = false
    }

    private func save(_ sticker: Sticker) async {
        isLoading = true
        status = "正在下载并保存..."
        do {
            let data = try await StickerAPI.shared.downloadOriginal(sticker)
            try await PhotoSaver.shared.saveGIFData(data)
            remember(sticker.title)
            status = "已保存到照片"
        } catch {
            status = "保存失败：\(error.localizedDescription)"
        }
        isLoading = false
    }

    private func loadRecent() {
        recentSaves = UserDefaults.standard.stringArray(forKey: "recentSaves") ?? []
    }

    private func remember(_ title: String) {
        let formatter = DateFormatter()
        formatter.dateFormat = "MM-dd HH:mm"
        let item = "\(formatter.string(from: Date())) · \(title)"
        recentSaves.insert(item, at: 0)
        recentSaves = Array(recentSaves.prefix(8))
        UserDefaults.standard.set(recentSaves, forKey: "recentSaves")
    }
}

private struct StickerCard: View {
    let sticker: Sticker
    let onSave: () async -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            GIFWebView(url: sticker.thumbnailUrl)
                .frame(height: 140)
                .background(Color(.secondarySystemBackground))
                .clipShape(RoundedRectangle(cornerRadius: 8))

            Text(sticker.title)
                .font(.subheadline)
                .lineLimit(2)
                .frame(minHeight: 36, alignment: .topLeading)

            Text("\(sticker.source) · GIF")
                .font(.caption)
                .foregroundStyle(.secondary)

            HStack {
                Button("保存") {
                    Task { await onSave() }
                }
                .buttonStyle(.borderedProminent)

                if let pageUrl = sticker.pageUrl {
                    Link("打开", destination: pageUrl)
                        .buttonStyle(.bordered)
                }
            }
        }
        .padding(10)
        .background(Color(.systemBackground))
        .clipShape(RoundedRectangle(cornerRadius: 8))
        .shadow(color: Color.black.opacity(0.08), radius: 4, y: 2)
    }
}

#Preview {
    ContentView()
}
