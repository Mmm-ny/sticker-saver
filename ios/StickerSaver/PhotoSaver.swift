import Foundation
import Photos

enum PhotoSaverError: LocalizedError {
    case denied
    case saveFailed

    var errorDescription: String? {
        switch self {
        case .denied:
            return "没有照片写入权限"
        case .saveFailed:
            return "保存到照片失败"
        }
    }
}

final class PhotoSaver {
    static let shared = PhotoSaver()

    func saveGIFData(_ data: Data) async throws {
        let authorized = await requestAddPermission()
        guard authorized else {
            throw PhotoSaverError.denied
        }

        try await withCheckedThrowingContinuation { continuation in
            PHPhotoLibrary.shared().performChanges {
                let request = PHAssetCreationRequest.forAsset()
                let options = PHAssetResourceCreationOptions()
                options.uniformTypeIdentifier = "com.compuserve.gif"
                request.addResource(with: .photo, data: data, options: options)
            } completionHandler: { success, error in
                if let error {
                    continuation.resume(throwing: error)
                } else if success {
                    continuation.resume()
                } else {
                    continuation.resume(throwing: PhotoSaverError.saveFailed)
                }
            }
        }
    }

    private func requestAddPermission() async -> Bool {
        if #available(iOS 14, *) {
            let current = PHPhotoLibrary.authorizationStatus(for: .addOnly)
            if current == .authorized || current == .limited {
                return true
            }
            let status = await PHPhotoLibrary.requestAuthorization(for: .addOnly)
            return status == .authorized || status == .limited
        }

        let current = PHPhotoLibrary.authorizationStatus()
        if current == .authorized {
            return true
        }
        let status = await PHPhotoLibrary.requestAuthorization()
        return status == .authorized
    }
}
