import Foundation

struct StickerSearchResponse: Decodable {
    let items: [Sticker]
    let page: Int
    let source: String
}

struct Sticker: Decodable, Identifiable {
    let id: String
    let title: String
    let thumbnailUrl: URL
    let originalUrl: URL
    let source: String
    let width: Int
    let height: Int
    let mimeType: String
    let pageUrl: URL?
}

enum StickerAPIError: LocalizedError {
    case badURL
    case badStatus(Int)
    case emptyData

    var errorDescription: String? {
        switch self {
        case .badURL:
            return "服务地址无效"
        case .badStatus(let code):
            return "服务返回 HTTP \(code)"
        case .emptyData:
            return "没有收到表情数据"
        }
    }
}

final class StickerAPI {
    static let shared = StickerAPI()

    // iOS Simulator can usually reach a Mac-hosted server through localhost.
    // For a physical iPhone, replace this with the computer LAN IP or deployed HTTPS server.
    private let baseURL = URL(string: "http://127.0.0.1:8080")!

    func search(query: String, page: Int = 1) async throws -> [Sticker] {
        var components = URLComponents(url: baseURL.appendingPathComponent("/api/stickers/search"), resolvingAgainstBaseURL: false)
        components?.queryItems = [
            URLQueryItem(name: "q", value: query),
            URLQueryItem(name: "page", value: String(page))
        ]
        guard let url = components?.url else {
            throw StickerAPIError.badURL
        }

        let (data, response) = try await URLSession.shared.data(from: url)
        if let http = response as? HTTPURLResponse, !(200..<300).contains(http.statusCode) {
            throw StickerAPIError.badStatus(http.statusCode)
        }
        if data.isEmpty {
            throw StickerAPIError.emptyData
        }
        return try JSONDecoder().decode(StickerSearchResponse.self, from: data).items
    }

    func downloadOriginal(_ sticker: Sticker) async throws -> Data {
        let (data, response) = try await URLSession.shared.data(from: sticker.originalUrl)
        if let http = response as? HTTPURLResponse, !(200..<300).contains(http.statusCode) {
            throw StickerAPIError.badStatus(http.statusCode)
        }
        if data.isEmpty {
            throw StickerAPIError.emptyData
        }
        return data
    }
}
